"""Post the best new clips to Instagram, TikTok and YouTube Shorts by themselves, within safe limits.

When auto-posting is on, GhostClip looks at the clips made since it was switched on and, when
the daily limit and the gap between posts allow it, posts the strongest one through the app's
browser window (the same steps as the post buttons under each clip), to every platform that's
switched on. Each platform has its own count against the daily limit and gap. It never posts
the old backlog, never posts a clip twice to the same place, and stops posting to a platform
after repeated failures there, so a problem can't turn into a string of broken posts.

Limits count every post made from GhostClip, by hand or automatically: posting dozens of
videos a day from one account is what makes a platform suspicious of it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .platforms import NAMES, PLATFORMS, prepare

MAX_AGE_HOURS = 6        # a moment older than this is stale: don't post it any more
FAILS_BEFORE_PAUSE = 2   # in a row, on one platform


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


def _posted_at(info: dict, platform: str = "instagram") -> float | None:
    """When the clip was posted to that platform (by hand or automatically), as a timestamp."""
    when = info.get(platform)
    if not when:
        return None
    try:
        return datetime.strptime(when, "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        return None


def _claim_key(platform: str) -> str:
    return "auto_post" if platform == "instagram" else f"auto_{platform}"   # Instagram's is the older name


class AutoPoster:
    def __init__(self, settings, browser, log: Callable[[str], None], running: Callable[[], bool],
                 start: bool = True):
        self.s = settings
        self.ig = browser               # the app's browser window (instagram.py): posts to every platform
        self.log = log
        self.running = running          # any stream recording or waiting to go live
        self.wake = threading.Event()
        self.posting: str | None = None # "TikTok: clip.mp4" while a post is under way
        self.fails = {p: 0 for p in PLATFORMS}
        self.paused = {p: "" for p in PLATFORMS}   # why posting there stopped itself ("" = not paused)
        self.last = ""                  # what happened last, for the dashboard
        self._was_on = bool(settings.auto_post)
        self._was_to = set(self.targets())
        self._awake = False
        self._state = None              # (time, counts) cache for state()
        if start:
            threading.Thread(target=self._loop, daemon=True, name="autopost").start()

    def targets(self) -> list[str]:
        """The platforms auto-posting posts to, in a fixed order."""
        to = getattr(self.s, "auto_post_to", None)
        if to is None:
            to = ["instagram"]
        return [p for p in PLATFORMS if p in to]

    # -- called from the app ----------------------------------------------------
    def settings_changed(self) -> None:
        """Turning auto-posting on starts fresh: only clips made from now on, no old pauses."""
        on = bool(self.s.auto_post)
        to = set(self.targets())
        if on and not self._was_on:
            self.s.auto_post_since = time.time()
            self.s.save()
            self.paused = {p: "" for p in PLATFORMS}
            self.fails = {p: 0 for p in PLATFORMS}
            names = ", ".join(NAMES[p] for p in self.targets()) or "no platform picked yet"
            self.log(f"Auto-posting on ({names}): the best new clips will be posted by themselves")
        elif not on and self._was_on:
            self.log("Auto-posting off")
        for p in to - self._was_to:      # a platform switched on gets a clean start too
            self.paused[p], self.fails[p] = "", 0
        self._was_on, self._was_to = on, to
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
            targets = self.targets()
            counts = {p: {"posted": self._recent_posts(p),
                          "waiting": len(self._candidates(p)) if self.s.auto_post and p in targets else 0}
                      for p in PLATFORMS}
            self._state = (time.time(), counts)
        plats = {}
        for p in PLATFORMS:
            nxt = self._next_slot(counts[p]["posted"])
            plats[p] = {"on": p in self.targets(), "paused": self.paused[p], "today": len(counts[p]["posted"]),
                        "next": datetime.fromtimestamp(nxt).strftime("%H:%M") if nxt > time.time() else "",
                        "waiting": counts[p]["waiting"]}
        on_plats = [plats[p] for p in self.targets()]
        paused = "; ".join(f"{NAMES[p]}: {self.paused[p]}" for p in self.targets() if self.paused[p])
        return {"on": bool(self.s.auto_post), "platforms": plats, "posting": self.posting, "last": self.last,
                "limit": int(self.s.auto_post_per_day), "paused": paused,
                "all_paused": bool(on_plats) and all(x["paused"] for x in on_plats),
                "today": max((x["today"] for x in on_plats), default=0),
                "waiting": max((x["waiting"] for x in on_plats), default=0)}

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
        """Post at most one clip (the browser does one thing at a time); the next step does the next."""
        if not self.s.auto_post or self.posting or self.ig.busy or not self.ig.available():
            return
        for p in self.targets():
            if self.paused[p]:
                continue
            posted = self._recent_posts(p)
            if len(posted) >= max(int(self.s.auto_post_per_day), 0) or time.time() < self._next_slot(posted):
                continue
            cands = self._candidates(p)
            if cands:
                clip, info = cands[0]   # the strongest moment waiting
                self._post(p, clip, info)
                return

    def _post(self, p: str, clip: Path, info: dict) -> None:
        info[_claim_key(p)] = "posting " + time.strftime("%Y-%m-%d %H:%M")
        _write(clip.with_suffix(".json"), info)      # claimed: never picked twice, even after a restart
        self.posting = f"{NAMES[p]}: {clip.name}"
        self.last = f"Posting {clip.name} to {NAMES[p]}"
        self.log(f"Auto-post: posting {clip.name} to {NAMES[p]} ({info.get('kind', 'clip')}, {info.get('hype', 0)}x)")
        text, title = prepare(info, p, self.s)
        self.ig.post(clip, text, done=lambda ok, status, c=clip, p=p: self._finished(p, c, ok, status),
                     platform=p, title=title)

    def _finished(self, p: str, clip: Path, ok: bool, status: str) -> None:
        meta = clip.with_suffix(".json")
        info = _read(meta)
        info[_claim_key(p)] = "posted" if ok else f"failed: {status}"[:200]
        _write(meta, info)
        self.posting = None
        self._state = None
        name = NAMES[p]
        if ok:
            self.fails[p] = 0
            self.last = f"Posted {clip.name} to {name} at {time.strftime('%H:%M')}"
        else:
            self.fails[p] += 1
            self.last = f"Couldn't post {clip.name} to {name}: {status}"
            self.log(f"Auto-post: {self.last}")
            if self.fails[p] >= FAILS_BEFORE_PAUSE or status.startswith("Log in"):
                self.paused[p] = (f"log in to {name} (Posting, top right), then switch auto-posting off and on"
                                  if status.startswith("Log in") else
                                  f"paused after {self.fails[p]} failed posts in a row. Check the window, "
                                  "then switch auto-posting off and on")
                self.log(f"Auto-post: {name} {self.paused[p]}")
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

    def _recent_posts(self, platform: str = "instagram") -> list[float]:
        """Times of every post to that platform in the last 24 hours."""
        day_ago = time.time() - 86400
        out = []
        for _clip, meta in self._metas():
            t = _posted_at(_read(meta), platform)
            if t and t >= day_ago:
                out.append(t)
        return sorted(out)

    def _next_slot(self, posted: list[float]) -> float:
        return (posted[-1] + float(self.s.auto_post_gap) * 60) if posted else 0.0

    def _candidates(self, platform: str = "instagram") -> list[tuple[Path, dict]]:
        since = max(float(self.s.auto_post_since or 0), time.time() - MAX_AGE_HOURS * 3600)
        out = []
        for clip, meta in self._metas():
            try:
                if clip.stat().st_mtime < since:
                    continue
            except OSError:
                continue
            info = _read(meta)
            if not info or info.get(platform) or info.get(_claim_key(platform)):
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
