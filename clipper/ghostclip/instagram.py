"""Post clips to Instagram through a browser window the user logs in to once.

The app drives its own Chrome (or Edge) window with a separate profile kept in
%APPDATA%\\GhostClip\\instagram_profile, so the login is remembered and your normal
browser isn't touched. Posting does what a person would do on instagram.com:
New post -> pick the video -> Next -> Next -> caption -> Share. If Instagram changes
its page and a step can't be found, the window is left at that step so you can finish
by hand (the caption is copied to your clipboard by the dashboard).
"""
from __future__ import annotations

import importlib.util
import json
import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable

from .settings import app_dir

IG_URL = "https://www.instagram.com/"
PROFILE = app_dir() / "instagram_profile"


class Instagram:
    def __init__(self, log: Callable[[str], None], base_url: str = IG_URL, channels=("chrome", "msedge", None),
                 profile: Path | None = None, **launch_kwargs):
        self.log = log
        self.profile = profile or PROFILE
        self.launch_kwargs = launch_kwargs
        self.base = base_url.rstrip("/") + "/"
        self.browser_channels = channels
        self.jobs: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.window_open = False
        self.logged_in: bool | None = None
        self.status = "Not opened yet"
        self.posts: dict[str, str] = {}   # clip file name -> what's happening with it

    # -- called from the dashboard (any thread) -----------------------------------
    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("playwright") is not None

    def open_window(self) -> None:
        self._submit(("open", None))

    def post(self, clip: Path, caption: str) -> None:
        self.posts[clip.name] = "Waiting to post..."
        self._submit(("post", (clip, caption)))

    def close(self) -> None:
        if self.thread and self.thread.is_alive():
            self.jobs.put(("quit", None))

    def state(self) -> dict:
        return {"available": self.available(), "window": self.window_open, "logged_in": self.logged_in,
                "status": self.status, "posts": dict(self.posts)}

    def _submit(self, job) -> None:
        if not self.available():
            self.status = "Instagram posting isn't installed. Run update.bat."
            return
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True, name="instagram")
            self.thread.start()
        self.jobs.put(job)

    # -- browser thread (Playwright objects stay on this thread) --------------------
    def _run(self) -> None:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            ctx = None
            while True:
                try:
                    cmd, arg = self.jobs.get(timeout=5)
                except queue.Empty:
                    if ctx is not None:
                        ctx = self._check(ctx)
                    continue
                if cmd == "quit":
                    break
                try:
                    ctx = self._ensure(pw, ctx)
                    if cmd == "open":
                        page = ctx.pages[0] if ctx.pages else ctx.new_page()
                        if "instagram" not in page.url and self.base not in page.url:
                            page.goto(self.base + "?hl=en", wait_until="domcontentloaded")
                        page.bring_to_front()
                        ctx = self._check(ctx)
                        self.status = "Logged in ✓" if self.logged_in else "Log in to Instagram in the window that opened"
                    elif cmd == "post":
                        self._post(ctx, *arg)
                except Exception as e:
                    msg = str(e).splitlines()[0][:200]
                    self.status = f"Instagram window problem: {msg}"
                    self.log(f"Instagram: {msg}")
                    if cmd == "post":
                        self.posts[arg[0].name] = f"Couldn't post: {msg}"
                    ctx = self._check(ctx)
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass

    def _ensure(self, pw, ctx):
        if ctx is not None:
            try:
                ctx.pages  # noqa: B018 - raises if the window was closed
                if ctx.pages:
                    return ctx
                ctx.close()
            except Exception:
                pass
        self.profile.mkdir(parents=True, exist_ok=True)
        last = None
        for ch in self.browser_channels:
            try:
                opts = {"channel": ch, "headless": False, "viewport": None, "args": ["--window-size=1100,950"]}
                opts.update(self.launch_kwargs)
                ctx = pw.chromium.launch_persistent_context(str(self.profile), **opts)
                self.window_open = True
                return ctx
            except Exception as e:
                last = e
        raise RuntimeError(f"couldn't open Chrome or Edge ({str(last).splitlines()[0][:120]})")

    def _check(self, ctx):
        try:
            if not ctx.pages:
                raise RuntimeError("closed")
            cookies = ctx.cookies([self.base])
            self.logged_in = any(c["name"] == "sessionid" and c["value"] for c in cookies)
            self.window_open = True
            return ctx
        except Exception:
            self.window_open = False
            if self.status.startswith("Logged in") or self.status.startswith("Log in"):
                self.status = "Window closed"
            return None

    def _post(self, ctx, clip: Path, caption: str) -> None:
        name = clip.name
        step = "start"

        def say(text):
            self.posts[name] = text
            self.status = f"{name}: {text}"

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()
        try:
            step = "opening Instagram"
            say("Opening Instagram...")
            page.goto(self.base + "?hl=en", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            self._check(ctx)
            if not self.logged_in:
                say("Log in to Instagram first (click 'Instagram window')")
                return
            self._dismiss(page)

            step = "the New post button"
            say("Starting a new post...")
            new_post = page.locator('svg[aria-label="New post"], svg[aria-label="Create"]').first
            new_post.wait_for(timeout=15000)
            new_post.click()
            page.wait_for_timeout(1200)
            sub = page.locator('svg[aria-label="Post"]').first
            if sub.count():
                try:
                    sub.click(timeout=3000)
                except Exception:
                    pass

            step = "uploading the video"
            say("Uploading the video...")
            file_input = page.locator('div[role="dialog"] input[type="file"], form input[type="file"]').last
            file_input.wait_for(state="attached", timeout=20000)
            file_input.set_input_files(str(clip))
            page.wait_for_timeout(2500)
            self._click_text(page, r"^OK$", 4000)            # "Video posts are now shared as reels"

            step = "choosing the crop"
            try:                                            # keep the full 9:16 picture
                page.locator('svg[aria-label="Select crop"]').first.click(timeout=5000)
                page.get_by_text(re.compile(r"^Original$")).first.click(timeout=3000)
            except Exception:
                pass

            for i in range(2):
                step = "the Next button"
                say("Next..." if i == 0 else "Almost there...")
                nxt = page.locator('div[role="dialog"]').get_by_role("button", name=re.compile(r"^Next$")).first
                nxt.wait_for(timeout=90000)
                nxt.click()
                page.wait_for_timeout(2000)

            step = "the caption box"
            say("Writing the caption...")
            box = page.locator('div[role="dialog"] [aria-label^="Write a caption"]').first
            box.wait_for(timeout=20000)
            box.click()
            lines = caption.split("\n")
            for j, line in enumerate(lines):
                if line:
                    page.keyboard.insert_text(line)
                if j < len(lines) - 1:
                    page.keyboard.press("Enter")

            step = "the Share button"
            say("Sharing...")
            page.locator('div[role="dialog"]').get_by_role("button", name=re.compile(r"^Share$")).first.click(timeout=15000)

            step = "waiting for Instagram to finish"
            say("Instagram is processing the video...")
            page.get_by_text(re.compile(r"(reel|post) (has been )?shared", re.I)).first.wait_for(timeout=600000)
            say("Posted ✓")
            self.log(f"Instagram: posted {name}")
            self._mark_posted(clip)
            page.wait_for_timeout(1500)
            page.keyboard.press("Escape")
        except Exception as e:
            first = str(e).splitlines()[0][:120]
            say(f"Stopped at {step}. Finish it in the Instagram window (caption copied).")
            self.log(f"Instagram: couldn't finish posting {name} at {step}: {first}")

    @staticmethod
    def _dismiss(page) -> None:
        for pat in (r"^Not now$", r"^Not Now$", r"^Allow all cookies$", r"^Decline optional cookies$"):
            Instagram._click_text(page, pat, 1500)

    @staticmethod
    def _click_text(page, pattern: str, timeout: int) -> bool:
        try:
            page.get_by_role("button", name=re.compile(pattern)).first.click(timeout=timeout)
            return True
        except Exception:
            return False

    @staticmethod
    def _mark_posted(clip: Path) -> None:
        meta = clip.with_suffix(".json")
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        info["instagram"] = time.strftime("%Y-%m-%d %H:%M")
        try:
            meta.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
