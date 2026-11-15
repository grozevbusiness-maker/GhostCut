"""Runs one clipping session: recorder + chat reader + hype detector + clip maker."""
from __future__ import annotations

import itertools
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .captions import Transcriber
from .chat import ChatMessage, TwitchChat
from .editor import ClipEditor
from .hype import HypeDetector, HypeEvent
from .recorder import Recorder

_ids = itertools.count(1)


@dataclass
class ClipJob:
    id: int
    event: HypeEvent
    wall_a: float
    wall_b: float


class Engine:
    def __init__(self, settings, ffmpeg: str, transcriber: Transcriber | None,
                 log: Callable[[str], None], on_clip: Callable[[str], None] = lambda p: None,
                 source_cmd: list[str] | None = None, chat_source=None):
        self.s = settings
        self.log = log
        self.on_clip = on_clip
        self.status = "Starting..."
        self.state = "running"            # running | stopping | stopped
        self.detector = HypeDetector(settings)
        self.lock = threading.Lock()
        self.jobs: "queue.Queue[ClipJob | None]" = queue.Queue()
        self.pending = 0
        self.made: deque = deque(maxlen=50)
        self.recorder = Recorder(settings, ffmpeg, self._status, log, source_cmd=source_cmd)
        self.editor = ClipEditor(settings, ffmpeg, transcriber, log)
        self.chat = (chat_source or TwitchChat)(settings.channel, self._on_message, self._chat_status)
        self.chat_status = "Connecting to chat..."
        self._stop = threading.Event()
        self.started = time.time()

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        self.log(f"Started for twitch.tv/{self.s.channel}. Clips go to {self.s.clips_dir}")
        self.recorder.start()
        self.chat.start()
        threading.Thread(target=self._ticker, daemon=True, name="ticker").start()
        threading.Thread(target=self._worker, daemon=True, name="clipper").start()

    def stop(self, done: Callable[[], None] = lambda: None) -> None:
        if self.state != "running":
            return
        self.state = "stopping"

        def finish():
            with self.lock:
                ev = self.detector.force_finish(time.time())
            if ev:
                self._queue_event(ev)
            # let the recorder catch up on footage the last clip needs
            if self.pending:
                self._status("Finishing the last clip before stopping...")
                deadline = time.time() + self.s.post_roll + 20
                while self.pending and time.time() < deadline:
                    time.sleep(0.5)
            self._stop.set()
            self.chat.stop()
            self.recorder.stop()
            self.recorder.join(timeout=60)
            self.jobs.put(None)
            while self.pending:
                time.sleep(0.5)
            if self.s.keep_full_recording:
                self._status("Saving the full stream recording...")
            saved = self.recorder.finalize()
            for p in saved:
                self.log(f"Full stream saved: {p}")
            self.state = "stopped"
            self._status("Stopped")
            done()

        threading.Thread(target=finish, daemon=True, name="stopper").start()

    # -- callbacks -------------------------------------------------------------
    def _status(self, text: str) -> None:
        if self.status != text:
            self.status = text
            self.log(text)

    def _chat_status(self, text: str) -> None:
        self.chat_status = text
        self.log(text)

    def _on_message(self, msg: ChatMessage) -> None:
        with self.lock:
            self.detector.add(msg)

    def clip_now(self) -> None:
        now = time.time()
        ev = HypeEvent(start=now, peak=now, end=now, score=0, kind="manual")
        a = now - self.s.pre_roll - 5
        self._add_job(ClipJob(next(_ids), ev, a + self.s.sync_offset, now + 5 + self.s.sync_offset))
        self.log("Manual clip of the last ~%ds queued" % (self.s.pre_roll + 10))

    # -- loops -----------------------------------------------------------------
    def _ticker(self) -> None:
        while not self._stop.wait(1.0):
            if self.state != "running":
                continue
            with self.lock:
                ev = self.detector.tick(time.time())
            if ev:
                self._queue_event(ev)

    def _queue_event(self, ev: HypeEvent) -> None:
        s = self.s
        a = ev.start - s.pre_roll
        b = max(ev.peak, ev.start) + s.post_roll
        if b - a < s.min_clip:
            a = b - s.min_clip
        if b - a > s.max_clip:
            a = b - s.max_clip
        a += s.sync_offset
        b += s.sync_offset
        self.log(f"Hype moment! {ev.kind}, {ev.score}x normal chat. Chat: {' | '.join(ev.messages[:3])}")
        self._add_job(ClipJob(next(_ids), ev, a, b))

    def _add_job(self, job: ClipJob) -> None:
        self.recorder.hold(job.id, job.wall_a)
        with self.lock:
            self.pending += 1
        self.jobs.put(job)

    def _worker(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                if self.pending == 0:
                    return
                continue
            try:
                self._make(job)
            except Exception as e:
                self.log(f"Clip failed: {e}")
            finally:
                self.recorder.release(job.id)
                with self.lock:
                    self.pending -= 1

    def _make(self, job: ClipJob) -> None:
        # wait for the recording to contain the end of the moment
        deadline = time.time() + 180
        while time.time() < deadline:
            covered = self.recorder.covered_until()
            if covered and covered >= job.wall_b:
                break
            if self.recorder.is_live_part_ended() and (self._stop.is_set() or not self.recorder.recording):
                break
            time.sleep(1)
        found = self.recorder.locate(job.wall_a, job.wall_b)
        if not found:
            self.log("Skipped a moment: the recording doesn't cover it (was the stream recording yet?)")
            return
        segs, offset, dur = found
        self._status(f"Editing a {dur:.0f}s clip...")
        t0 = time.time()
        path = self.editor.make(segs, offset, dur, job.event, datetime.fromtimestamp(job.wall_a))
        self.made.appendleft(str(path))
        self.log(f"Clip saved: {path.name} ({dur:.0f}s, edited in {time.time() - t0:.0f}s)")
        self.on_clip(str(path))
        if self.state == "running":
            self._status(f"Recording {self.s.channel} live" if self.recorder.recording
                         else f"Waiting for {self.s.channel} to go live...")
