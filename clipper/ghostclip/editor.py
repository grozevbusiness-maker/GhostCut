"""Turn a stretch of the recording into a finished, captioned clip."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import framing, games
from .captions import LAYOUTS, Transcriber, build_ass
from .utils import run

_encoder_cache: dict[str, list[str]] = {}

X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high", "-pix_fmt", "yuv420p"]
NVENC = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0", "-pix_fmt", "yuv420p"]


def pick_encoder(ffmpeg: str, choice: str) -> list[str]:
    if choice == "x264":
        return X264
    if choice in _encoder_cache:
        return _encoder_cache[choice]
    r = run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.2",
             "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=30)
    enc = NVENC if r.returncode == 0 else X264
    _encoder_cache[choice] = enc
    return enc


def video_filter(layout: str, with_subs: bool, plan=None) -> str:
    W, H = LAYOUTS.get(layout, LAYOUTS["vertical_blur"])[:2]
    subs = ",ass=captions.ass" if with_subs else ""
    if layout == "stack" and plan is not None and plan.mode == "stack":
        x, y, w, h = plan.cam
        cam_h = 700
        return ("[0:v]split[a][b];"
                f"[a]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale={W}:{cam_h}:flags=lanczos,setsar=1[cam];"
                f"[b]scale={W}:{H - cam_h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H - cam_h},setsar=1[game];"
                f"[cam][game]vstack=inputs=2{subs}[v]")
    if layout in ("vertical_crop", "stack"):
        cx = plan.center_x if plan is not None else 0.5
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H}:'max(0,min(iw-{W},{cx:.4f}*iw-{W // 2}))':0,setsar=1{subs}[v]")
    if layout == "original":
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1{subs}[v]")
    return ("[0:v]split[a][b];"
            "[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,"
            f"scale={W}:{H},eq=brightness=-0.07:saturation=1.15[bg];"
            f"[b]scale={W}:-2:flags=lanczos[fg];"
            f"[bg][fg]overlay=0:(H-h)/2,setsar=1{subs}[v]")


def post_text(channel: str, kind: str, transcript: str, tags: str = "") -> str:
    hook = {
        "laugh": f"{channel} had chat crying 😂",
        "clip": f"chat said CLIP IT 🎬 ({channel})",
        "manual": f"{channel} moment",
    }.get(kind, f"{channel} had chat going crazy 🔥")
    if transcript:
        first = transcript.split(".")[0].strip()
        if 8 <= len(first) <= 80:
            hook = f'"{first}" 💀'
    return f"{hook}\n\n{tags or games.hashtags(channel, 'none')}"


class ClipEditor:
    def __init__(self, settings, ffmpeg: str, transcriber: Transcriber | None, log: Callable[[str], None]):
        self.s, self.ffmpeg, self.transcriber, self.log = settings, ffmpeg, transcriber, log

    def make(self, segments: list[Path], offset: float, duration: float, event, when: datetime) -> Path:
        clips = Path(self.s.clips_dir)
        clips.mkdir(parents=True, exist_ok=True)
        name = f"{self.s.channel}_{when:%Y-%m-%d_%H-%M-%S}_{event.kind}"
        work = clips / ".work" / name
        work.mkdir(parents=True, exist_ok=True)
        try:
            return self._make(work, name, segments, offset, duration, event, when)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            try:
                (clips / ".work").rmdir()
            except OSError:
                pass

    def _make(self, work, name, segments, offset, duration, event, when) -> Path:
        ff = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        (work / "list.txt").write_text("".join(
            "file '{}'\n".format(str(p.resolve()).replace("\\", "/").replace("'", "'\\''")) for p in segments))
        src = ["-f", "concat", "-safe", "0", "-ss", f"{offset:.3f}", "-i", "list.txt", "-t", f"{duration:.3f}"]

        words, transcript, cap_status = [], "", "off"
        if self.s.captions and self.transcriber and self.transcriber.available:
            r = run(ff + src + ["-vn", "-ac", "1", "-ar", "16000", "audio.wav"], cwd=work)
            if r.returncode == 0:
                try:
                    words = self.transcriber.transcribe(str(work / "audio.wav"))
                    transcript = " ".join(w.text for w in words)
                    cap_status = f"{len(words)} words" if words else "no speech heard"
                    self.log(f"Captions: {cap_status}")
                except Exception as e:
                    cap_status = f"failed: {e.__class__.__name__}: {str(e)[:200]}"
                    self.log(f"Captions {cap_status}; saving the clip without captions")
            else:
                cap_status = "failed: could not read the clip's audio"
                self.log("Could not read the clip's audio: " + r.stderr.strip()[-200:])

        layout, plan = self.s.layout, None
        if layout in ("stack", "vertical_crop"):
            try:
                plan = framing.plan(self.ffmpeg, work, src, duration, self.s.facecam if layout == "stack" else "none")
            except Exception as e:
                plan = framing.Plan("crop", note=f"face detection failed: {e}")
            if layout == "stack":
                self.log(f"Framing: {'face cam on top' if plan.mode == 'stack' else 'center crop'} ({plan.note})")
        cap_layout = "stack" if plan is not None and plan.mode == "stack" else (
            "vertical_crop" if layout == "stack" else layout)

        credit = f"twitch.tv/{self.s.channel}" if self.s.credit and self.s.channel else ""
        with_subs = bool(words or credit)
        if with_subs:
            (work / "captions.ass").write_text(
                build_ass(words, cap_layout, duration, self.s.caption_font, credit), encoding="utf-8")
        fade_out = max(duration - 0.4, 0)
        enc = pick_encoder(self.ffmpeg, self.s.encoder)
        out_tmp = work / "out.mp4"
        cmd = ff + src + [
            "-filter_complex", video_filter(layout, with_subs, plan), "-map", "[v]", "-map", "0:a:0?",
            "-af", f"loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=in:d=0.25,afade=t=out:st={fade_out:.2f}:d=0.4",
            *enc, "-fpsmax", "60", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-movflags", "+faststart", "out.mp4",
        ]
        r = run(cmd, cwd=work)
        if r.returncode != 0 and enc is NVENC:
            self.log("Graphics-card encoding failed, retrying on the CPU")
            _encoder_cache[self.s.encoder] = X264
            cmd = [c for c in cmd]
            i = cmd.index("h264_nvenc") - 1
            cmd[i:i + len(NVENC)] = X264
            r = run(cmd, cwd=work)
        if r.returncode != 0 or not out_tmp.exists():
            raise RuntimeError("ffmpeg could not render the clip: " + r.stderr.strip()[-400:])

        clips = Path(self.s.clips_dir)
        final = clips / f"{name}.mp4"
        n = 2
        while final.exists():
            final = clips / f"{name}_{n}.mp4"
            n += 1
        shutil.move(str(out_tmp), final)
        thumbs = clips / ".thumbs"
        thumbs.mkdir(exist_ok=True)
        run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "1", "-i", str(final), "-frames:v", "1",
             "-vf", "scale=360:-2", "-q:v", "5", str(thumbs / (final.stem + ".jpg"))], timeout=30)

        choice = (getattr(self.s, "games", {}) or {}).get(self.s.channel, "auto")
        category = getattr(self.s, "category", "")
        game_name, _ = games.resolve(choice, category)
        tags = games.hashtags(self.s.channel, choice, category)
        info = {
            "file": final.name, "channel": self.s.channel, "time": when.isoformat(timespec="seconds"),
            "kind": event.kind, "hype": event.score, "length": round(duration, 1),
            "transcript": transcript, "chat": event.messages,
            "game": game_name, "captions": cap_status,
            "post": post_text(self.s.channel, event.kind, transcript, tags),
        }
        final.with_suffix(".json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        final.with_suffix(".txt").write_text(
            f"{info['post']}\n\n---\nWhat was said: {transcript or '(no speech detected)'}\n"
            f"Chat: {' | '.join(event.messages[:6])}\nHype: {event.score}x normal, {duration:.0f}s\n",
            encoding="utf-8")
        log_csv = clips / "clips_log.csv"
        new = not log_csv.exists()
        with log_csv.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["file", "channel", "time", "kind", "hype", "seconds", "transcript"])
            w.writerow([final.name, self.s.channel, info["time"], event.kind, event.score,
                        info["length"], transcript])
        return final
