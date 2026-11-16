"""Post the best new clips to Instagram by themselves, within safe limits.

When auto-posting is on, GhostClip looks at the clips made since it was switched on and, when
the daily limit and the gap between posts allow it, posts the strongest one through the
Instagram window (the same steps as the POST TO INSTAGRAM button). It never posts the old
backlog, never posts a clip twice, and stops itself after repeated failures so a problem
can't turn into a string of broken posts.

Limits count every post made from GhostClip, by hand or automatically: posting dozens of
reels a day from one account is what makes Instagram suspicious of it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

MAX_AGE_HOURS = 6        # a moment older than this is stale: don't post it any more
FAILS_BEFORE_PAUSE = 2   # in a row


def _read(meta: Path) -> dict:
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(meta: Path, info: dict) -> None:
    try:
        meta.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _posted_at(info: dict) -> float | None:
    """When the clip was posted to Instagram (any way), as a timestamp."""
    when = info.get("instagram")
    if not when:
        return None
    try:
        return datetime.strptime(when, "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        return None


class AutoPoster:
    def __init__(self, settings, instagram, log: Callable[[str], None], running: Callable[[], bool],
                 start: bool = True):
        self.s = settings
        self.ig = instagram
        self.log = log
        self.running = running          # any stream recording or waiting to go live
        self.wake = threading.Event()
        self.posting: str | None = None # clip being posted right now
        self.fails = 0
        self.paused = ""                # why auto-posting stopped itself ("" = not paused)
        self.last = ""                  # what happened last, for the dashboard
        self._was_on = bool(settings.auto_post)
        self._awake = False
        self._state = None              # (time, counts) cache for state()
        if start:
            threading.Thread(target=self._loop, daemon=True, name="autopost").start()

    # -- called from the app ----------------------------------------------------
    def settings_changed(self) -> None:
        """Turning auto-posting on starts fresh: only clips made from now on, no old pause."""
        on = bool(self.s.auto_post)
        if on and not self._was_on:
            self.s.auto_post_since = time.time()
            self.s.save()
            self.paused, self.fails = "", 0
            self.log("Auto-posting on: the best new clips will be posted to Instagram by themselves")
        elif not on and self._was_on:
            self.log("Auto-posting off")
        self._was_on = on
        self._state = None
        self.wake.set()

    def clip_made(self, path: str) -> None:
        self._state = None
        self.wake.set()

    def state(self) -> dict:
        """For the dashboard. Reading every clip's file is kept to once every 5 seconds."""
        if self._state and time.time() - self._state[0] < 5:
            counts = self._state[1]
        else:
            posted = self._recent_posts()
            counts = {"posted": posted, "waiting": len(self._candidates()) if self.s.auto_post else 0}
            self._state = (time.time(), counts)
        nxt = self._next_slot(counts["posted"])
        return {"on": bool(self.s.auto_post), "paused": self.paused, "posting": self.posting,
                "today": len(counts["posted"]), "limit": int(self.s.auto_post_per_day),
                "next": datetime.fromtimestamp(nxt).strftime("%H:%M") if nxt > time.time() else "",
                "waiting": counts["waiting"], "last": self.last}

    # -- the loop -----------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            self.wake.wait(30)
            self.wake.clear()
            self._keep_awake(bool(self.s.auto_post) or self.running())
            try:
                self._step()
            except Exception as e:   # never let one odd clip file stop auto-posting for good
                self.last = f"Auto-post problem: {e}"
                self.log(self.last)

    def _step(self) -> None:
        if not self.s.auto_post or self.paused or self.posting or self.ig.busy:
            return
        if not self.ig.available():
            return
        posted = self._recent_posts()
        if len(posted) >= max(int(self.s.auto_post_per_day), 0):
            return
        if time.time() < self._next_slot(posted):
            return
        cands = self._candidates()
        if not cands:
            return
        clip, info = cands[0]   # the strongest moment waiting
        meta = clip.with_suffix(".json")
        info["auto_post"] = "posting " + time.strftime("%Y-%m-%d %H:%M")
        _write(meta, info)      # claimed: never picked twice, even after a restart
        self.posting = clip.name
        self.last = f"Posting {clip.name}"
        self.log(f"Auto-post: posting {clip.name} ({info.get('kind', 'clip')}, {info.get('hype', 0)}x)")
        self.ig.post(clip, info.get("post", ""), done=lambda ok, status, c=clip: self._finished(c, ok, status))

    def _finished(self, clip: Path, ok: bool, status: str) -> None:
        meta = clip.with_suffix(".json")
        info = _read(meta)
        info["auto_post"] = "posted" if ok else f"failed: {status}"[:200]
        _write(meta, info)
        self.posting = None
        self._state = None
        if ok:
            self.fails = 0
            self.last = f"Posted {clip.name} at {time.strftime('%H:%M')}"
        else:
            self.fails += 1
            self.last = f"Couldn't post {clip.name}: {status}"
            self.log(f"Auto-post: {self.last}")
            if self.fails >= FAILS_BEFORE_PAUSE or status.startswith("Log in"):
                self.paused = ("Log in to Instagram in the Instagram window, then switch auto-posting off and on"
                               if status.startswith("Log in") else
                               f"Paused after {self.fails} failed posts in a row. Check the Instagram window, "
                               "then switch auto-posting off and on")
                self.log(f"Auto-post: {self.paused}")
        self.wake.set()

    # -- what to post, and when ----------------------------------------------------
    def _metas(self):
        d = Path(self.s.clips_dir)
        if not d.is_dir():
            return
        for meta in d.glob("*.json"):
            clip = meta.with_suffix(".mp4")
            if clip.is_file():
                yield clip, meta

    def _recent_posts(self) -> list[float]:
        """Times of every post in the last 24 hours."""
        day_ago = time.time() - 86400
        out = []
        for _clip, meta in self._metas():
            t = _posted_at(_read(meta))
            if t and t >= day_ago:
                out.append(t)
        return sorted(out)

    def _next_slot(self, posted: list[float]) -> float:
        return (posted[-1] + float(self.s.auto_post_gap) * 60) if posted else 0.0

    def _candidates(self) -> list[tuple[Path, dict]]:
        since = max(float(self.s.auto_post_since or 0), time.time() - MAX_AGE_HOURS * 3600)
        out = []
        for clip, meta in self._metas():
            try:
                if clip.stat().st_mtime < since:
                    continue
            except OSError:
                continue
            info = _read(meta)
            if not info or info.get("instagram") or info.get("auto_post"):
                continue
            if not info.get("post"):
                continue
            hype = float(info.get("hype") or 0)
            if info.get("kind") != "manual" and hype < float(self.s.auto_post_min_hype):
                continue
            out.append((clip, info))
        # strongest moment first; a clip you made by hand (✂) counts as strong
        out.sort(key=lambda ci: (99 if ci[1].get("kind") == "manual" else float(ci[1].get("hype") or 0),
                                 ci[0].stat().st_mtime), reverse=True)
        return out

    # -- keep the PC awake while there's work to do ------------------------------------
    def _keep_awake(self, on: bool) -> None:
        if on == self._awake or os.name != "nt":
            return
        try:
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
            self._awake = on
        except Exception:
            pass
