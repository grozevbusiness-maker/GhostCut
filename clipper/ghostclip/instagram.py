"""The app's own browser window: posting clips to Instagram, and reading account stats.

GhostClip drives its own Chrome (or Edge) window with a separate profile kept in
%APPDATA%\\GhostClip\\instagram_profile, so logins (Instagram, TikTok, YouTube) are
remembered and your normal browser isn't touched. Posting does what a person does on
instagram.com: New post -> pick the video -> Next (until the caption screen) -> caption ->
Share. Buttons are recognised in several languages. If a step can't be found, the window is
left at that step so you can finish by hand, and a screenshot of it is saved for fixing it.
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
DEBUG_DIR = app_dir() / "instagram_debug"

# Instagram's button texts in the languages people are most likely to use.
WORDS = {
    "new_post": ["New post", "Create", "Нова публикация", "Създаване", "Nueva publicación", "Crear",
                 "Nouvelle publication", "Créer", "Neuer Beitrag", "Erstellen", "Nuovo post", "Crea",
                 "Nova publicação", "Criar", "Новая публикация", "Создать", "Nowy post", "Utwórz",
                 "Yeni gönderi", "Oluştur"],
    "post": ["Post", "Публикация", "Publicación", "Publication", "Beitrag", "Publicação", "Публикация", "Gönderi"],
    "ok": ["OK", "Ок", "Aceptar", "D'accord", "Okay"],
    "next": ["Next", "Напред", "Siguiente", "Suivant", "Weiter", "Avanti", "Avançar", "Próximo", "Далее",
             "Dalej", "İleri", "Volgende"],
    "share": ["Share", "Сподели", "Споделяне", "Compartir", "Partager", "Teilen", "Condividi", "Compartilhar",
              "Поделиться", "Udostępnij", "Paylaş", "Delen"],
    "crop": ["Select crop", "Избор на изрязване", "Изрязване", "Seleccionar recorte", "Sélectionner le recadrage",
             "Zuschneiden auswählen", "Seleziona ritaglio", "Selecionar corte"],
    "original": ["Original", "Оригинал", "Originale", "Оригинальный", "Oryginał", "Orijinal"],
    "not_now": ["Not now", "Not Now", "Не сега", "Ahora no", "Plus tard", "Jetzt nicht", "Non ora", "Agora não",
                "Не сейчас", "Nie teraz", "Şimdi değil"],
}
SHARED_RE = re.compile(r"(reel|post).{0,20}shared|shared.{0,20}(reel|post)|споделен|compartid|partagé|geteilt|"
                       r"condivis|compartilhad|опубликован|udostępnion|paylaşıldı", re.I)


def _any(words: list[str]) -> re.Pattern:
    return re.compile("^(" + "|".join(re.escape(w) for w in words) + ")$", re.I)


class Instagram:
    def __init__(self, log: Callable[[str], None], base_url: str = IG_URL, channels=("chrome", "msedge", None),
                 profile: Path | None = None, **launch_kwargs):
        self.log = log
        self.base = base_url.rstrip("/") + "/"
        self.browser_channels = channels
        self.profile = profile or PROFILE
        self.launch_kwargs = launch_kwargs
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

    def open_window(self, url: str | None = None) -> None:
        self._submit(("open", url))

    def post(self, clip: Path, caption: str) -> None:
        self.posts[clip.name] = "Waiting to post..."
        self._submit(("post", (clip, caption)))

    def call(self, fn: Callable, *args) -> None:
        """Run fn(browser_context, *args) on the browser thread (used for reading stats)."""
        self._submit(("call", (fn, args)))

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
            self.thread = threading.Thread(target=self._run, daemon=True, name="browser")
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
                        target = arg or (self.base + "?hl=en")
                        if arg or ("instagram" not in page.url and self.base not in page.url):
                            page.goto(target, wait_until="domcontentloaded")
                        page.bring_to_front()
                        ctx = self._check(ctx)
                        self.status = "Logged in ✓" if self.logged_in else "Log in to Instagram in the window that opened"
                    elif cmd == "post":
                        self._post(ctx, *arg)
                    elif cmd == "call":
                        fn, args = arg
                        fn(ctx, *args)
                except Exception as e:
                    msg = str(e).splitlines()[0][:200]
                    self.status = f"Browser window problem: {msg}"
                    self.log(f"Browser: {msg}")
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
            if ctx is None or not ctx.pages:
                raise RuntimeError("closed")
            cookies = ctx.cookies([self.base])
            self.logged_in = any(c["name"] == "sessionid" and c["value"] for c in cookies)
            self.window_open = True
            return ctx
        except Exception:
            self.window_open = False
            if self.status.startswith(("Logged in", "Log in")):
                self.status = "Window closed"
            return None

    # -- posting ------------------------------------------------------------------
    def _post(self, ctx, clip: Path, caption: str) -> None:
        name = clip.name
        step = "start"

        def say(text):
            self.posts[name] = text
            self.status = f"{name}: {text}"

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()
        dialog = page.locator('div[role="dialog"]')
        try:
            step = "opening Instagram"
            say("Opening Instagram...")
            page.goto(self.base + "?hl=en", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            self._check(ctx)
            if not self.logged_in:
                say("Log in to Instagram first (click 'Instagram' at the top)")
                return
            self._click_any(page, "not_now", 1500)

            step = "the New post button"
            say("Starting a new post...")
            labels = ", ".join(f'svg[aria-label="{w}"]' for w in WORDS["new_post"])
            new_post = page.locator(labels).first
            new_post.wait_for(timeout=20000)
            new_post.click()
            page.wait_for_timeout(1500)
            file_input = page.locator('input[type="file"]')
            if not file_input.count():
                sub = page.locator(", ".join(f'svg[aria-label="{w}"]' for w in WORDS["post"])).first
                try:
                    sub.click(timeout=4000)
                except Exception:
                    page.get_by_role("link", name=_any(WORDS["post"])).first.click(timeout=4000)

            step = "uploading the video"
            say("Uploading the video...")
            file_input = page.locator('div[role="dialog"] input[type="file"], form input[type="file"]').last
            file_input.wait_for(state="attached", timeout=25000)
            file_input.set_input_files(str(clip))
            page.wait_for_timeout(3000)
            self._click_any(page, "ok", 5000)            # "Video posts are now shared as reels"

            step = "choosing the crop"
            try:                                          # keep the full 9:16 picture
                page.locator(", ".join(f'svg[aria-label="{w}"]' for w in WORDS["crop"])).first.click(timeout=5000)
                page.get_by_text(_any(WORDS["original"])).first.click(timeout=3000)
                page.wait_for_timeout(600)
            except Exception:
                pass

            # Press Next until the caption box shows up (Instagram has 1-3 screens before it).
            step = "the Next button"
            caption_box = dialog.locator('[contenteditable="true"]').first
            for i in range(5):
                if caption_box.count() and caption_box.is_visible():
                    break
                say("Next..." if i == 0 else "Almost there...")
                nxt = dialog.get_by_role("button", name=_any(WORDS["next"])).first
                nxt.wait_for(timeout=120000)          # the video may still be processing
                nxt.click()
                page.wait_for_timeout(2500)
            step = "the caption box"
            say("Writing the caption...")
            caption_box.wait_for(timeout=20000)
            caption_box.click()
            lines = caption.split("\n")
            for j, line in enumerate(lines):
                if line:
                    page.keyboard.insert_text(line)
                if j < len(lines) - 1:
                    page.keyboard.press("Enter")
            page.wait_for_timeout(800)

            step = "the Share button"
            say("Sharing...")
            dialog.get_by_role("button", name=_any(WORDS["share"])).first.click(timeout=20000)

            step = "waiting for Instagram to finish"
            say("Instagram is processing the video...")
            deadline = time.time() + 600
            while time.time() < deadline:
                page.wait_for_timeout(2000)
                body = page.locator("body").inner_text(timeout=5000)
                if SHARED_RE.search(body):
                    break
            else:
                raise TimeoutError("Instagram never said the reel was shared")
            say("Posted ✓")
            self.log(f"Instagram: posted {name}")
            self._mark_posted(clip)
            page.wait_for_timeout(1500)
            page.keyboard.press("Escape")
        except Exception as e:
            first = str(e).splitlines()[0][:160]
            shot = self._debug(page, name, step)
            say(f"Stopped at {step}. Finish it in the Instagram window (caption copied).")
            self.log(f"Instagram: couldn't finish posting {name} at {step}: {first}. Screenshot: {shot}")

    def _debug(self, page, name: str, step: str) -> str:
        """Save what the window looked like, plus the buttons on it, to fix the next version."""
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            png = DEBUG_DIR / f"{stamp}.png"
            page.screenshot(path=str(png))
            buttons = page.evaluate("""() => [...document.querySelectorAll('[role=button],button,svg[aria-label],[contenteditable=true],input[type=file]')]
                .map(e => (e.tagName + ' ' + (e.getAttribute('aria-label') || '') + ' ' + (e.innerText || '')).trim().slice(0, 80))
                .filter(Boolean).slice(0, 120)""")
            (DEBUG_DIR / f"{stamp}.txt").write_text(f"clip: {name}\nstep: {step}\nurl: {page.url}\n\n" + "\n".join(buttons),
                                                    encoding="utf-8")
            return str(png)
        except Exception:
            return "(couldn't save one)"

    @staticmethod
    def _click_any(page, key: str, timeout: int) -> bool:
        try:
            page.get_by_role("button", name=_any(WORDS[key])).first.click(timeout=timeout)
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
