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

from . import platforms as plat
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
    "crop": ["Select crop", "Crop", "Избор на изрязване", "Изберете изрязване", "Изрязване", "Seleccionar recorte", "Sélectionner le recadrage",
             "Zuschneiden auswählen", "Seleziona ritaglio", "Selecionar corte"],
    "original": ["Original", "Оригинал", "Originale", "Оригинальный", "Oryginał", "Orijinal"],
    "not_now": ["Not now", "Not Now", "Не сега", "Ahora no", "Plus tard", "Jetzt nicht", "Non ora", "Agora não",
                "Не сейчас", "Nie teraz", "Şimdi değil"],
}
SHARED_RE = re.compile(r"(reel|post).{0,20}shared|shared.{0,20}(reel|post)|споделен|compartid|partagé|geteilt|"
                       r"condivis|compartilhad|опубликован|udostępnion|paylaşıldı", re.I)


# Newest reel on the logged-in account: its code and stored size (width x height).
REEL_SHAPE_JS = """async ({uid, csrf}) => {
  const H = {'x-ig-app-id': '936619743392459', 'x-requested-with': 'XMLHttpRequest', 'x-csrftoken': csrf};
  const asJson = async (r) => { try { return JSON.parse(await r.text() || '{}'); } catch (e) { return {}; } };
  const opts = (o) => ({credentials: 'include', signal: AbortSignal.timeout(15000), ...o});
  try {
    let items = [];
    try {   // the reels tab
      const r = await fetch('/api/v1/clips/user/', opts({method: 'POST',
        body: new URLSearchParams({target_user_id: uid, page_size: '6', include_feed_video: 'true'}),
        headers: {...H, 'content-type': 'application/x-www-form-urlencoded'}}));
      items = ((await asJson(r)).items || []).map(i => i.media || {});
    } catch (e) {}
    if (!items.length) {   // fallback: the profile feed
      const r = await fetch(`/api/v1/feed/user/${uid}/?count=6`, opts({headers: H}));
      items = (await asJson(r)).items || [];
    }
    items.sort((a, b) => (b.taken_at || 0) - (a.taken_at || 0));
    if (!items.length) return {error: 'no reels found'};
    return {reels: items.slice(0, 6).map(m => {
      const v = (m.video_versions || [])[0] || {};
      return {code: m.code, w: m.original_width || v.width, h: m.original_height || v.height, taken_at: m.taken_at || 0};
    })};
  } catch (e) { return {error: String(e)}; }
}"""


def ig_user_id(cookies: dict) -> str:
    """The logged-in account's numeric id: the ds_user_id cookie, or the start of sessionid
    ("<id>%3A<token>..."), since ds_user_id isn't always set."""
    uid = (cookies.get("ds_user_id") or "").strip()
    if uid.isdigit():
        return uid
    m = re.match(r"(\d+)(?:%3A|:)", cookies.get("sessionid") or "")
    return m.group(1) if m else ""


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
        self.logins: dict[str, bool | None] = {p: None for p in plat.PLATFORMS}
        # platform -> clip file name -> what's happening with it
        self.posts: dict[str, dict[str, str]] = {p: {} for p in plat.PLATFORMS}

    # -- called from the dashboard (any thread) -----------------------------------
    _has_playwright = False   # remembered once found: the dashboard asks every second

    @classmethod
    def available(cls) -> bool:
        if not cls._has_playwright:
            cls._has_playwright = importlib.util.find_spec("playwright") is not None
        return cls._has_playwright

    def open_window(self, url: str | None = None) -> None:
        """url: a web address, or a platform name to open its login page."""
        self._submit(("open", url))

    def post(self, clip: Path, caption: str, done: Callable[[bool, str], None] | None = None,
             platform: str = "instagram", title: str = "") -> None:
        """Queue a post. `done(ok, status)` is called on the browser thread when it's over.
        title: YouTube only."""
        self.posts[platform][clip.name] = "Waiting to post..."
        if not self._submit(("post", (clip, caption, done, platform, title))) and done:
            done(False, self.status)

    @property
    def busy(self) -> bool:
        """A post is waiting or in progress, on any platform."""
        return any(not re.match(r"(Posted|Stopped|Couldn't|Log in)", s)
                   for d in self.posts.values() for s in d.values())

    def call(self, fn: Callable, *args) -> None:
        """Run fn(browser_context, *args) on the browser thread (used for reading stats)."""
        self._submit(("call", (fn, args)))

    def close(self) -> None:
        if self.thread and self.thread.is_alive():
            self.jobs.put(("quit", None))

    def state(self) -> dict:
        return {"available": self.available(), "window": self.window_open, "logged_in": self.logged_in,
                "logins": dict(self.logins), "status": self.status,
                "posts": {p: dict(d) for p, d in self.posts.items()}}

    def _submit(self, job) -> bool:
        if not self.available():
            self.status = "Instagram posting isn't installed. Run update.bat."
            return False
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True, name="browser")
            self.thread.start()
        self.jobs.put(job)
        return True

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
                        if arg in plat.LOGIN_URL:          # a platform: its login page (or the site)
                            ctx = self._check(ctx)
                            target = plat.LOGIN_URL[arg] if not self.logins.get(arg) else plat.SITE[arg]
                            page.goto(target, wait_until="domcontentloaded")
                            name = plat.NAMES[arg]
                            self.status = f"{name}: logged in ✓" if self.logins.get(arg) else                                 f"Log in to {name} in the window that opened"
                        else:
                            target = arg or (self.base + "?hl=en")
                            if arg or ("instagram" not in page.url and self.base not in page.url):
                                page.goto(target, wait_until="domcontentloaded")
                            ctx = self._check(ctx)
                            self.status = "Logged in ✓" if self.logged_in else "Log in to Instagram in the window that opened"
                        page.bring_to_front()
                    elif cmd == "post":
                        clip, caption, done, platform, title = arg
                        if platform == "instagram":
                            ok = self._post(ctx, clip, caption)
                        else:
                            ok = self._post_other(ctx, platform, clip, caption, title)
                        self._done(done, ok, self.posts[platform].get(clip.name, ""))
                    elif cmd == "call":
                        fn, args = arg
                        fn(ctx, *args)
                except Exception as e:
                    msg = str(e).splitlines()[0][:200]
                    self.status = f"Browser window problem: {msg}"
                    self.log(f"Browser: {msg}")
                    if cmd == "post":
                        posts = self.posts[arg[3]]
                        posts[arg[0].name] = f"Couldn't post: {msg}"
                        self._done(arg[2], False, posts[arg[0].name])
                    ctx = self._check(ctx)
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass

    def _done(self, done, ok: bool, status: str) -> None:
        if done:
            try:
                done(ok, status)
            except Exception as e:
                self.log(f"Instagram: after-post step failed: {e}")

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
            self.logins["instagram"] = self.logged_in
            for p in ("tiktok", "youtube"):
                self.logins[p] = any(c["name"] in plat.LOGIN_COOKIES[p] and c["value"]
                                     for c in ctx.cookies([plat.SITE[p]]))
            self.window_open = True
            return ctx
        except Exception:
            self.window_open = False
            if self.status.startswith(("Logged in", "Log in")):
                self.status = "Window closed"
            return None

    # -- posting ------------------------------------------------------------------
    def _post(self, ctx, clip: Path, caption: str) -> bool:
        """Post one clip as a Reel. True if Instagram said it was shared."""
        name = clip.name
        step = "start"
        started = time.time()

        def say(text):
            self.posts["instagram"][name] = text
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
                return False
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
            say("Keeping the full vertical video...")
            if not self._set_original_crop(page, dialog):
                raise RuntimeError("couldn't set the crop to Original (Instagram would cut the video to a square)")

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
            self._report_shape(ctx, name, since=started)
            return True
        except Exception as e:
            first = str(e).splitlines()[0][:160]
            shot = self._debug(page, name, step)
            say(f"Stopped at {step}. Finish it in the Instagram window (caption copied).")
            self.log(f"Instagram: couldn't finish posting {name} at {step}: {first}. Screenshot: {shot}")
            return False

    def _post_other(self, ctx, platform: str, clip: Path, caption: str, title: str) -> bool:
        """TikTok or YouTube (see platforms.py). True if the site said it was posted."""
        name, label = clip.name, plat.NAMES[platform]
        where = ["start"]

        def say(text):
            self.posts[platform][name] = text
            self.status = f"{label}: {name}: {text}"

        def step(text):
            where[0] = text

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()
        try:
            if platform == "tiktok":
                link = plat.post_tiktok(page, clip, caption, say, step)
            else:
                link = plat.post_youtube(page, clip, title, caption, say, step)
            say("Posted ✓")
            self.log(f"{label}: posted {name}" + (f" ({link})" if link else ""))
            self._mark_posted(clip, platform, link)
            self._check(ctx)
            return True
        except plat.NeedLogin:
            self.logins[platform] = False
            say(f"Log in to {label} first (Posting panel, top right)")
            self.log(f"{label}: not logged in, so {name} wasn't posted")
            return False
        except Exception as e:
            first = str(e).splitlines()[0][:160]
            shot = self._debug(page, f"{platform}: {name}", where[0])
            say(f"Stopped at {where[0]}. Finish it in the window (caption copied).")
            self.log(f"{label}: couldn't finish posting {name} at {where[0]}: {first}. Screenshot: {shot}")
            return False

    def _report_shape(self, ctx, name: str, since: float) -> None:
        """Ask Instagram how big the new reel (and the few before it) came out, so a square crop
        can't slip through unnoticed. Runs in its own tab: the posting tab may be navigating."""
        def vertical(r) -> bool:
            return bool(r.get("w") and r.get("h") and r["h"] / r["w"] > 1.7)

        page = None
        try:
            cookies = {c["name"]: c["value"] for c in ctx.cookies([self.base])}
            uid, csrf = ig_user_id(cookies), cookies.get("csrftoken", "")
            if not uid:
                self.log("Instagram: couldn't check the reel's size (no account id in the login cookies)")
                return
            page = ctx.new_page()
            page.goto(self.base + "?hl=en", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            info, reels = {}, []
            for _ in range(8):   # the new reel can take a little while to show up in the list
                try:
                    info = page.evaluate(REEL_SHAPE_JS, {"uid": uid, "csrf": csrf}) or {}
                except Exception as e:   # e.g. the page navigated under us: try again
                    info = {"error": str(e).splitlines()[0][:100]}
                reels = info.get("reels") or []
                if reels and reels[0].get("w") and reels[0]["taken_at"] >= since - 120:
                    break
                page.wait_for_timeout(5000)
            if not reels or reels[0]["taken_at"] < since - 120:
                self.log(f"Instagram: couldn't find the new reel to check its size ({info.get('error', 'not listed yet')})")
                return
            new, older = reels[0], [r for r in reels[1:] if r.get("w") and r.get("h")]
            url = f"{self.base}reel/{new.get('code')}/"
            if vertical(new):
                self.log(f"Instagram: reel is {new['w']}x{new['h']}, full 9:16 vertical ✓ {url}")
            else:
                self.posts["instagram"][name] = f"Posted, but Instagram cropped it to {new.get('w')}x{new.get('h')}"
                self.log(f"Instagram: WARNING reel is {new.get('w')}x{new.get('h')}, not full vertical (cropped) {url}")
            cropped = [r for r in older if not vertical(r)]
            if cropped:
                self.log("Instagram: earlier reels that aren't full vertical: " + ", ".join(
                    f"{self.base}reel/{r.get('code')}/ ({r.get('w')}x{r.get('h')})" for r in cropped))
            elif older:
                self.log(f"Instagram: the {len(older)} reels before it are full vertical too ✓")
        except Exception as e:
            self.log(f"Instagram: couldn't check the reel's size: {str(e).splitlines()[0][:120]}")
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def _set_original_crop(self, page, dialog) -> bool:
        """Instagram's website crops new videos to a square unless 'Original' is picked. Pick it.

        Open the crop menu (by its label, or by trying the small buttons at the bottom of the
        preview until the menu with 1:1 / 4:5 / 16:9 shows up), then click the option that isn't
        a ratio: that's 'Original' in every language.
        """
        ratio = re.compile(r"^\s*\d+\s*:\s*\d+\s*$")

        def menu_open() -> bool:
            try:
                return dialog.get_by_text(re.compile(r"^\s*1\s*:\s*1\s*$")).first.is_visible()
            except Exception:
                return False

        def pick_original() -> bool:
            square = dialog.get_by_text(re.compile(r"^\s*1\s*:\s*1\s*$")).first
            # the menu's options are siblings: walk up until the container holds 3+ options
            options = page.evaluate("""(el) => {
                let box = el;
                for (let i = 0; i < 6 && box; i++) {
                    box = box.parentElement;
                    const texts = [...box.querySelectorAll('span,div')].filter(e => e.children.length === 0)
                        .map(e => e.textContent.trim()).filter(Boolean);
                    if (texts.filter(t => /^\\d+\\s*:\\s*\\d+$/.test(t)).length >= 2) return [...new Set(texts)];
                }
                return [];
            }""", square.element_handle())
            original = [o for o in options if not ratio.match(o) and len(o) < 20]
            if not original:
                return False
            dialog.get_by_text(original[0], exact=True).first.click(timeout=4000)
            page.wait_for_timeout(700)
            self.log(f"Instagram: crop set to '{original[0]}'")
            return True

        # 1) the crop button by its label
        for w in WORDS["crop"]:
            btn = dialog.locator(f'svg[aria-label="{w}"]').first
            if btn.count():
                try:
                    btn.click(timeout=4000)
                    page.wait_for_timeout(700)
                    if menu_open():
                        return pick_original()
                except Exception:
                    pass
        # 2) try the small icon buttons in the lower half of the preview
        try:
            box = dialog.last.bounding_box()
        except Exception:
            box = None
        icons = dialog.locator("svg[aria-label]")
        cands = []
        for i in range(min(icons.count(), 40)):
            el = icons.nth(i)
            try:
                b = el.bounding_box()
            except Exception:
                b = None
            if not b or not box or b["width"] > 60:
                continue
            if b["y"] > box["y"] + box["height"] * 0.5:
                cands.append((b["x"], el))
        for _x, el in sorted(cands, key=lambda c: c[0]):
            try:
                el.click(timeout=3000)
                page.wait_for_timeout(700)
                if menu_open():
                    return pick_original()
                el.click(timeout=3000)   # not the crop button: undo (zoom slider / gallery)
                page.wait_for_timeout(400)
            except Exception:
                continue
        return False

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
    def _mark_posted(clip: Path, platform: str = "instagram", link: str = "") -> None:
        meta = clip.with_suffix(".json")
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        info[platform] = time.strftime("%Y-%m-%d %H:%M")
        if link:
            info[platform + "_link"] = link
        try:
            meta.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
