"""Record the live stream into short pieces, and map chat time to video time.

streamlink pulls the stream from Twitch, our relay thread passes the bytes to ffmpeg,
and ffmpeg cuts them into 10-second .ts pieces without re-encoding (almost no CPU).
For every piece we note the wall-clock time it started arriving, so a chat message's
time can be turned into a position in the video even after ads, lag or reconnects.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .utils import NO_WINDOW, run, streamlink_cmd

SEGMENT_SECONDS = 10


def fetch_meta(channel: str) -> tuple[str, str]:
    """(category, title) of a live Twitch stream, or ("", "") if offline/unknown."""
    try:
        r = run(streamlink_cmd() + ["--json", f"https://www.twitch.tv/{channel}"], timeout=45)
        md = (json.loads(r.stdout or "{}").get("metadata") or {})
        return (md.get("category") or "").strip(), (md.get("title") or "").strip()
    except Exception:
        return "", ""


@dataclass
class Segment:
    path: Path
    start: float          # position in the part (seconds)
    end: float
    wall: float | None    # wall time this piece started arriving (None until known)


@dataclass
class Part:
    """One continuous recording. A new part starts if the stream drops and comes back."""
    dir: Path
    started: float
    segments: list = field(default_factory=list)
    seen: dict = field(default_factory=dict)        # file name -> wall time first seen
    live: bool = True
    first_byte: float | None = None

    def refresh(self) -> None:
        now = time.time()
        for f in self.dir.glob("seg_*.ts"):
            self.seen.setdefault(f.name, now)
        csv = self.dir / "list.csv"
        try:
            rows = [r.split(",") for r in csv.read_text().splitlines() if r.count(",") >= 2]
        except OSError:
            return
        segs, base = [], None
        for name, s, e in ((r[0], float(r[1]), float(r[2])) for r in rows):
            base = s if base is None else base
            segs.append(Segment(self.dir / name, s - base, e - base, self.seen.get(name)))
        # The first piece shows up late (ffmpeg probes the input first), so anchor it from the second one.
        if segs:
            if len(segs) > 1 and segs[1].wall is not None:
                segs[0].wall = segs[1].wall - (segs[1].start - segs[0].start)
            elif self.first_byte is not None and segs[0].wall is None:
                segs[0].wall = self.first_byte
        self.segments = segs

    def wall_to_pos(self, wall: float) -> float | None:
        anchored = [s for s in self.segments if s.wall is not None]
        if not anchored:
            return None
        before = [s for s in anchored if s.wall <= wall]
        ref = before[-1] if before else anchored[0]
        return max(0.0, ref.start + (wall - ref.wall))

    @property
    def wall_start(self) -> float | None:
        return self.segments[0].wall if self.segments and self.segments[0].wall else self.first_byte

    @property
    def wall_end(self) -> float | None:
        """Wall time up to which finished pieces exist."""
        if not self.segments or self.segments[-1].wall is None:
            return None
        last = self.segments[-1]
        return last.wall + (last.end - last.start)


class Recorder(threading.Thread):
    def __init__(self, settings, ffmpeg: str, on_status: Callable[[str], None],
                 log: Callable[[str], None], source_cmd: list[str] | None = None):
        super().__init__(daemon=True, name="recorder")
        self.s = settings
        self.ffmpeg = ffmpeg
        self.on_status = on_status
        self.log = log
        self.source_cmd = source_cmd
        self.parts: list[Part] = []
        self.session_dir = Path(settings.recordings_dir) / f"{settings.channel}_{datetime.now():%Y-%m-%d_%H-%M-%S}"
        self._stop = threading.Event()
        self._procs: list[subprocess.Popen] = []
        self.lock = threading.Lock()
        self.holds: dict[int, float] = {}   # clip jobs still needing footage from this wall time on
        self.recording = False

    # -- public --------------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()
        procs = list(self._procs)
        for p in procs:
            try:
                p.terminate()
            except OSError:
                pass

        def reap():   # anything that ignored the polite request gets closed for good
            time.sleep(5)
            for p in procs + list(self._procs):
                if p.poll() is None:
                    try:
                        p.kill()
                    except OSError:
                        pass

        threading.Thread(target=reap, daemon=True).start()

    def hold(self, key: int, wall: float) -> None:
        with self.lock:
            self.holds[key] = wall

    def release(self, key: int) -> None:
        with self.lock:
            self.holds.pop(key, None)

    def locate(self, wall_a: float, wall_b: float):
        """Footage for a wall-clock window: (segment paths, offset, duration) or None."""
        with self.lock:
            for part in reversed(self.parts):
                part.refresh()
                ws, we = part.wall_start, part.wall_end
                if ws is None or we is None or wall_b < ws:
                    continue
                if wall_a > we:
                    return None
                a = part.wall_to_pos(max(wall_a, ws))
                b = part.wall_to_pos(min(wall_b, we))
                segs = [s for s in part.segments if s.end > a and s.start < b and s.path.exists()]
                if not segs or b - a < 3:
                    return None
                return [s.path for s in segs], a - segs[0].start, b - a
        return None

    def covered_until(self) -> float | None:
        with self.lock:
            for part in reversed(self.parts):
                part.refresh()
                if part.wall_end:
                    return part.wall_end
        return None

    def is_live_part_ended(self) -> bool:
        return not self.parts or not self.parts[-1].live

    # -- thread --------------------------------------------------------------
    def run(self) -> None:
        waiting_said = False
        while not self._stop.is_set():
            started = time.time()
            got_data = self._record_part()
            if self._stop.is_set():
                break
            if got_data:
                self.on_status("Stream stopped or dropped. Watching for it to come back...")
                waiting_said = False
                wait = 5
            else:
                if not waiting_said:
                    self.on_status(f"Waiting for {self.s.channel} to go live...")
                    waiting_said = True
                wait = max(5, 20 - (time.time() - started))
            self._stop.wait(wait)
        self.recording = False

    def _source(self) -> list[str]:
        if self.source_cmd:
            return self.source_cmd
        cmd = streamlink_cmd() + [
            "--stdout", "--loglevel", "error",
            "--twitch-low-latency",
            "--stream-segment-threads", "2", "--stream-timeout", "60",
            "--retry-open", "3",
        ]
        if self.s.twitch_oauth:
            tok = self.s.twitch_oauth.removeprefix("oauth:")
            cmd += ["--twitch-api-header", f"Authorization=OAuth {tok}"]
        return cmd + [f"https://www.twitch.tv/{self.s.channel}", self.s.quality]

    def _record_part(self) -> bool:
        part_dir = self.session_dir / f"part_{len(self.parts) + 1:02d}"
        part_dir.mkdir(parents=True, exist_ok=True)
        src = subprocess.Popen(self._source(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=NO_WINDOW)
        self._procs.append(src)
        if self._stop.is_set():
            src.kill()
        first = src.stdout.read(188 * 64) if src.stdout else b""
        if not first:
            err = src.stderr.read().decode("utf-8", "replace").strip() if src.stderr else ""
            src.wait()
            self._procs.remove(src)
            shutil.rmtree(part_dir, ignore_errors=True)
            if err and "No playable streams" not in err and "offline" not in err.lower():
                self.log(f"streamlink: {err.splitlines()[-1][:300]}")
            return False

        part = Part(dir=part_dir, started=time.time(), first_byte=time.time())
        with self.lock:
            self.parts.append(part)
        ff = subprocess.Popen([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-fflags", "+genpts",
            "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
            "-f", "segment", "-segment_time", str(SEGMENT_SECONDS), "-segment_format", "mpegts",
            "-segment_list", "list.csv", "-segment_list_type", "csv", "-reset_timestamps", "0",
            "seg_%06d.ts",
        ], cwd=part_dir, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        self._procs.append(ff)
        self.recording = True
        self.on_status(f"Recording {self.s.channel} live")
        self.log(f"Recording to {part_dir}")

        watcher = threading.Thread(target=self._watch, args=(part, ff), daemon=True)
        watcher.start()
        try:
            ff.stdin.write(first)
            while True:
                chunk = src.stdout.read(65536)
                if not chunk:
                    break
                ff.stdin.write(chunk)
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                ff.stdin.close()
            except OSError:
                pass
            try:
                src.terminate()
            except OSError:
                pass
            src.wait()
            try:
                ff.wait(timeout=30)
            except subprocess.TimeoutExpired:
                ff.kill()
            err = ff.stderr.read().decode("utf-8", "replace").strip() if ff.stderr else ""
            if err:
                self.log("ffmpeg (recording): " + err.splitlines()[-1][:300])
            for p in (src, ff):
                if p in self._procs:
                    self._procs.remove(p)
            part.live = False
            with self.lock:
                part.refresh()
            self.recording = False
        return True

    def _watch(self, part: Part, ff: subprocess.Popen) -> None:
        while ff.poll() is None:
            with self.lock:
                part.refresh()
                self._prune()
            time.sleep(0.5)

    def _prune(self) -> None:
        if self.s.keep_full_recording:
            return
        keep_from = time.time() - max(self.s.buffer_minutes, 5) * 60
        if self.holds:
            keep_from = min(keep_from, min(self.holds.values()) - 30)
        for part in self.parts:
            for seg in part.segments[:-1]:
                if seg.wall is not None and seg.wall + (seg.end - seg.start) < keep_from and seg.path.exists():
                    try:
                        seg.path.unlink()
                    except OSError:
                        pass

    # -- after the stream ------------------------------------------------------
    def finalize(self) -> list[str]:
        """Join each part's pieces into one .mp4 (when keeping the full stream) or clean up."""
        out = []
        for i, part in enumerate(self.parts, 1):
            files = sorted(part.dir.glob("seg_*.ts"))
            if not self.s.keep_full_recording or not files:
                shutil.rmtree(part.dir, ignore_errors=True)
                continue
            suffix = f"_part{i}" if len(self.parts) > 1 else ""
            target = self.session_dir.with_name(self.session_dir.name + suffix + ".mp4")
            lst = part.dir / "concat.txt"
            lst.write_text("".join(f"file '{f.name}'\n" for f in files))
            r = run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                     "-i", "concat.txt", "-c", "copy", "-movflags", "+faststart", str(target)], cwd=part.dir)
            if r.returncode == 0 and target.exists() and target.stat().st_size > 0:
                shutil.rmtree(part.dir, ignore_errors=True)
                out.append(str(target))
            else:
                self.log(f"Could not join the recording; the pieces are kept in {part.dir}")
        try:
            if self.session_dir.exists() and not any(self.session_dir.iterdir()):
                self.session_dir.rmdir()
        except OSError:
            pass
        return out
