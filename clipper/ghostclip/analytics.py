"""Account stats across Instagram, TikTok and YouTube, for the Analytics board.

Instagram is read through the app's logged-in browser window, TikTok and YouTube from the
public profile pages in that same window (or YouTube's official API when an API key is set).
Results and a daily history are kept in %APPDATA%\\GhostClip\\analytics.json.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from .settings import app_dir

FILE = app_dir() / "analytics.json"
PLATFORMS = ("instagram", "tiktok", "youtube")


def parse_count(text: str) -> int | None:
    """'12.3K' -> 12300, '1,234' -> 1234, '1.2M views' -> 1200000, '2,5 хил.' -> 2500."""
    if text is None:
        return None
    s = str(text).strip().replace(" ", " ").replace(" ", " ")
    m = re.search(r"(\d[\d.,\s]*)\s*(K|M|B|хил|млн|мил|k|m|b|тыс|mil|mln|Mio|Md)?", s)
    if not m:
        return None
    num, suf = m.group(1).strip(), (m.group(2) or "")
    mult = {"k": 1e3, "хил": 1e3, "тыс": 1e3, "mil": 1e3, "m": 1e6, "млн": 1e6, "мил": 1e6, "mln": 1e6,
            "mio": 1e6, "b": 1e9, "md": 1e9}.get(suf.lower(), 1)
    num = num.replace(" ", "")
    if mult > 1:
        num = num.replace(",", ".")
        if num.count(".") > 1:
            num = num.replace(".", "", num.count(".") - 1)
    else:
        num = num.replace(",", "").replace(".", "")
    try:
        return int(round(float(num) * mult))
    except ValueError:
        return None


def clean_handle(text: str) -> str:
    text = (text or "").strip()
    m = re.search(r"(?:instagram\.com|tiktok\.com|youtube\.com)/@?([A-Za-z0-9_.\-]+)", text)
    if m:
        text = m.group(1)
    return text.lstrip("@").strip().strip("/")


class Analytics:
    def __init__(self, settings, browser, log: Callable[[str], None]):
        self.s = settings
        self.browser = browser          # the Instagram window object (runs jobs on the browser thread)
        self.log = log
        self.running: set[str] = set()
        self.lock = threading.Lock()
        self.data = {"platforms": {}, "history": []}
        try:
            self.data.update(json.loads(FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    # -- public ----------------------------------------------------------------
    def refresh(self) -> str | None:
        wanted = self.configured()
        if not wanted:
            return "Add your account names first (Instagram, TikTok and/or YouTube)"
        if self.running:
            return None
        browser_jobs = [p for p in wanted if not (p == "youtube" and self.s.youtube_api_key)]
        if browser_jobs and not self.browser.available():
            return "The browser part isn't installed. Run update.bat."
        self.running = set(wanted)
        if "youtube" in wanted and self.s.youtube_api_key:
            threading.Thread(target=self._guard, args=("youtube", self._youtube_api), daemon=True).start()
        if browser_jobs:
            self.browser.call(self._browser_jobs, browser_jobs)
        return None

    def configured(self) -> list[str]:
        out = []
        if self.s.ig_username or self.browser.logged_in:
            out.append("instagram")
        if self.s.tiktok_handle:
            out.append("tiktok")
        if self.s.youtube_handle:
            out.append("youtube")
        return out

    def summary(self, clips_dir: str) -> dict:
        plats = self.data.get("platforms", {})
        views = posts = followers = 0
        for p in PLATFORMS:
            d = plats.get(p) or {}
            if d.get("ok"):
                views += d.get("views") or 0
                posts += d.get("posts") or 0
                followers += d.get("followers") or 0
        made, posted = self._clip_counts(clips_dir)
        return {"platforms": plats, "history": self.data.get("history", [])[-120:], "running": sorted(self.running),
                "total": {"views": views, "posts": posts, "followers": followers},
                "ghostclip": {"made": made, "posted": posted}, "configured": self.configured()}

    def _clip_counts(self, clips_dir: str) -> tuple[int, int]:
        d = Path(clips_dir)
        try:
            key = (str(d), d.stat().st_mtime_ns)
        except OSError:
            return 0, 0
        cached = getattr(self, "_counts", None)
        if cached and cached[0] == key and time.time() - cached[2] < 30:
            return cached[1]
        made = posted = 0
        for f in d.glob("*.json"):
            made += 1
            try:
                posted += 1 if json.loads(f.read_text(encoding="utf-8")).get("instagram") else 0
            except (OSError, ValueError):
                pass
        self._counts = (key, (made, posted), time.time())
        return made, posted

    # -- plumbing ----------------------------------------------------------------
    def _store(self, platform: str, result: dict) -> None:
        result["updated"] = datetime.now().isoformat(timespec="seconds")
        with self.lock:
            old = self.data["platforms"].get(platform) or {}
            if not result.get("ok") and old.get("ok"):
                # keep the last good numbers, just show the error
                old = dict(old, error=result.get("error"), error_at=result["updated"])
                self.data["platforms"][platform] = old
            else:
                self.data["platforms"][platform] = result
            self.running.discard(platform)
            self._snapshot()
            try:
                FILE.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        if result.get("ok"):
            self.log(f"Analytics: {platform} updated ({result.get('views', 0):,} views, {result.get('posts', 0)} posts)")
        else:
            self.log(f"Analytics: couldn't read {platform}: {result.get('error')}")

    def _snapshot(self) -> None:
        plats = self.data["platforms"]
        snap = {"date": date.today().isoformat()}
        for p in PLATFORMS:
            d = plats.get(p) or {}
            if d.get("ok"):
                snap[p] = d.get("views") or 0
        hist = [h for h in self.data["history"] if h.get("date") != snap["date"]]
        hist.append(snap)
        self.data["history"] = hist[-400:]

    def _guard(self, platform: str, fn, *args) -> None:
        try:
            self._store(platform, fn(*args))
        except Exception as e:
            self._store(platform, {"ok": False, "error": str(e).splitlines()[0][:200]})

    def _browser_jobs(self, ctx, platforms: list[str]) -> None:
        fns = {"instagram": self._instagram, "tiktok": self._tiktok, "youtube": self._youtube_page}
        for p in platforms:
            self._guard(p, fns[p], ctx)

    # -- Instagram ---------------------------------------------------------------
    def _instagram(self, ctx) -> dict:
        cookies = {c["name"]: c["value"] for c in ctx.cookies(["https://www.instagram.com/"])}
        if not cookies.get("sessionid"):
            return {"ok": False, "error": "Log in to Instagram in the app's Instagram window first"}
        page = ctx.new_page()
        try:
            page.goto("https://www.instagram.com/?hl=en", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            res = page.evaluate(IG_JS, {"username": clean_handle(self.s.ig_username), "uid": cookies.get("ds_user_id", ""),
                                        "csrf": cookies.get("csrftoken", "")})
        finally:
            page.close()
        if res.get("error"):
            return {"ok": False, "error": res["error"]}
        reels = res.get("reels") or []
        top = sorted(reels, key=lambda r: r.get("views") or 0, reverse=True)[:5]
        return {"ok": True, "handle": res.get("username"), "followers": res.get("followers"),
                "posts": len(reels) if reels else res.get("posts", 0), "all_posts": res.get("posts"),
                "views": sum(r.get("views") or 0 for r in reels), "likes": sum(r.get("likes") or 0 for r in reels),
                "top": [{"title": r.get("caption") or "Reel", "views": r.get("views") or 0,
                         "url": f"https://www.instagram.com/reel/{r.get('code')}/"} for r in top]}

    # -- TikTok ------------------------------------------------------------------
    def _tiktok(self, ctx) -> dict:
        handle = clean_handle(self.s.tiktok_handle)
        page = ctx.new_page()
        try:
            page.goto(f"https://www.tiktok.com/@{handle}?lang=en", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            stats = page.evaluate(TT_STATS_JS) or {}
            items = self._scroll_collect(page, TT_ITEMS_JS)
        finally:
            page.close()
        if not items and not stats:
            return {"ok": False, "error": f"Couldn't read tiktok.com/@{handle} (wrong name, private, or a captcha)"}
        vids = [{"title": i.get("title") or "TikTok", "views": parse_count(i.get("views")) or 0, "url": i.get("url")}
                for i in items]
        return {"ok": True, "handle": handle, "followers": stats.get("followers"), "likes": stats.get("likes"),
                "posts": stats.get("videos") or len(vids), "views": sum(v["views"] for v in vids),
                "top": sorted(vids, key=lambda v: v["views"], reverse=True)[:5]}

    # -- YouTube -----------------------------------------------------------------
    def _youtube_page(self, ctx) -> dict:
        handle = clean_handle(self.s.youtube_handle)
        page = ctx.new_page()
        try:
            out = {}
            for tab in ("shorts", "videos"):
                page.goto(f"https://www.youtube.com/@{handle}/{tab}?hl=en", wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                if "consent." in page.url:   # EU cookie page
                    for label in ("Reject all", "Accept all"):
                        try:
                            page.get_by_role("button", name=re.compile(label, re.I)).first.click(timeout=3000)
                            break
                        except Exception:
                            pass
                    page.wait_for_timeout(2500)
                out[tab] = self._scroll_collect(page, YT_ITEMS_JS)
                if "subs" not in out:
                    out["subs"] = page.evaluate(YT_SUBS_JS)
        finally:
            page.close()
        shorts = [{"title": i.get("title") or "Short", "views": parse_count(i.get("views")) or 0, "url": i.get("url")}
                  for i in out.get("shorts", [])]
        videos = [{"title": i.get("title") or "Video", "views": parse_count(i.get("views")) or 0, "url": i.get("url")}
                  for i in out.get("videos", [])]
        if not shorts and not videos:
            return {"ok": False, "error": f"Couldn't read youtube.com/@{handle} (check the handle)"}
        return self._yt_result(handle, parse_count(out.get("subs")), shorts, videos)

    def _youtube_api(self) -> dict:
        key, handle = self.s.youtube_api_key.strip(), clean_handle(self.s.youtube_handle)

        def get(path, **params):
            params["key"] = key
            url = "https://www.googleapis.com/youtube/v3/" + path + "?" + urllib.parse.urlencode(params)
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                msg = json.loads(e.read().decode() or "{}").get("error", {}).get("message", str(e))
                raise RuntimeError(f"YouTube API: {msg}") from None

        ch = get("channels", part="statistics,contentDetails", forHandle="@" + handle).get("items") or []
        if not ch:
            return {"ok": False, "error": f"No YouTube channel @{handle}"}
        uploads = ch[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        subs = int(ch[0]["statistics"].get("subscriberCount") or 0)
        ids, token = [], None
        while len(ids) < 1000:
            kw = {"part": "contentDetails", "playlistId": uploads, "maxResults": 50}
            if token:
                kw["pageToken"] = token
            pl = get("playlistItems", **kw)
            ids += [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
            token = pl.get("nextPageToken")
            if not token:
                break
        shorts, videos = [], []
        for i in range(0, len(ids), 50):
            vs = get("videos", part="contentDetails,statistics,snippet", id=",".join(ids[i:i + 50]))
            for v in vs.get("items", []):
                item = {"title": v["snippet"]["title"], "views": int(v["statistics"].get("viewCount") or 0),
                        "url": f"https://www.youtube.com/watch?v={v['id']}"}
                (shorts if iso_seconds(v["contentDetails"]["duration"]) <= 180 else videos).append(item)
        return self._yt_result(handle, subs, shorts, videos)

    @staticmethod
    def _yt_result(handle, subs, shorts, videos) -> dict:
        return {"ok": True, "handle": handle, "followers": subs,
                "shorts": len(shorts), "shorts_views": sum(v["views"] for v in shorts),
                "videos": len(videos), "videos_views": sum(v["views"] for v in videos),
                "posts": len(shorts) + len(videos), "views": sum(v["views"] for v in shorts + videos),
                "top": sorted(shorts + videos, key=lambda v: v["views"], reverse=True)[:5]}

    @staticmethod
    def _scroll_collect(page, js: str, rounds: int = 40) -> list[dict]:
        """Scroll down until no new items load, then return them all."""
        last, same = -1, 0
        items = []
        for _ in range(rounds):
            items = page.evaluate(js) or []
            if len(items) == last:
                same += 1
                if same >= 3:
                    break
            else:
                same = 0
            last = len(items)
            page.mouse.wheel(0, 4000)
            page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(1200)
        return items


def iso_seconds(d: str) -> int:
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    days, h, mi, s = (int(x or 0) for x in m.groups())
    return days * 86400 + h * 3600 + mi * 60 + s


# -- page scripts ------------------------------------------------------------------

IG_JS = """async ({username, uid, csrf}) => {
  const H = {'x-ig-app-id': '936619743392459', 'x-requested-with': 'XMLHttpRequest'};
  try {
    if (!username) {
      const r = await fetch(`/api/v1/users/${uid}/info/`, {headers: H, credentials: 'include'});
      username = (await r.json()).user.username;
    }
    const r2 = await fetch(`/api/v1/users/web_profile_info/?username=${encodeURIComponent(username)}`, {headers: H, credentials: 'include'});
    const p = (await r2.json()).data.user;
    const reels = [];
    let maxId = '', more = true, pages = 0;
    while (more && pages < 40) {
      const body = new URLSearchParams({target_user_id: p.id, page_size: '50', include_feed_video: 'true'});
      if (maxId) body.set('max_id', maxId);
      const r = await fetch('/api/v1/clips/user/', {method: 'POST', credentials: 'include', body,
        headers: {...H, 'x-csrftoken': csrf, 'content-type': 'application/x-www-form-urlencoded'}});
      const j = await r.json();
      for (const it of (j.items || [])) {
        const m = it.media || {};
        reels.push({code: m.code, views: m.play_count || m.ig_play_count || m.view_count || 0,
                    likes: m.like_count || 0, caption: ((m.caption && m.caption.text) || '').slice(0, 90)});
      }
      more = !!(j.paging_info && j.paging_info.more_available);
      maxId = (j.paging_info && j.paging_info.max_id) || '';
      pages++;
    }
    return {username, followers: p.edge_followed_by.count, posts: p.edge_owner_to_timeline_media.count, reels};
  } catch (e) { return {error: 'Instagram didn\\'t answer as expected (' + e + ')'}; }
}"""

TT_STATS_JS = """() => {
  try {
    const s = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
    const st = JSON.parse(s.textContent).__DEFAULT_SCOPE__['webapp.user-detail'].userInfo.stats;
    return {followers: st.followerCount, likes: st.heartCount, videos: st.videoCount};
  } catch (e) {
    const t = sel => (document.querySelector(sel) || {}).textContent;
    const f = t('[data-e2e="followers-count"]');
    return f ? {followers: f, likes: t('[data-e2e="likes-count"]')} : null;
  }
}"""

TT_ITEMS_JS = """() => [...document.querySelectorAll('[data-e2e="user-post-item"]')].map(el => {
  const a = el.querySelector('a[href*="/video/"], a[href*="/photo/"]');
  const v = el.querySelector('[data-e2e="video-views"]');
  const img = el.querySelector('img[alt]');
  return {url: a ? a.href : '', views: v ? v.textContent : '0', title: img ? img.alt.slice(0, 90) : ''};
})"""

YT_ITEMS_JS = """() => {
  const tiles = document.querySelectorAll('ytm-shorts-lockup-view-model, ytd-reel-item-renderer, ytd-rich-item-renderer:not(:has(ytm-shorts-lockup-view-model)), ytd-grid-video-renderer');
  return [...tiles].map(el => {
    const text = el.innerText || '';
    const m = text.match(/([\\d.,]+\\s*[KMB]?)\\s+views?/i);
    const a = el.querySelector('a[href*="/shorts/"], a#video-title-link, a#thumbnail, a[href*="watch?v="]');
    const title = (el.querySelector('#video-title, h3, [title]') || {}).textContent || '';
    return {url: a ? a.href : '', views: m ? m[1] : '0', title: title.trim().slice(0, 90)};
  }).filter(x => x.url);
}"""

YT_SUBS_JS = """() => { const m = (document.body.innerText || '').match(/([\\d.,]+\\s*[KMB]?)\\s+subscribers?/i); return m ? m[1] : null; }"""
