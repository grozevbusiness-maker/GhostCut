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

from .instagram import ig_user_id
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
                info = json.loads(f.read_text(encoding="utf-8"))
                posted += 1 if any(info.get(p) for p in PLATFORMS) else 0
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
        uid = ig_user_id(cookies)
        if not uid:
            return {"ok": False, "error": "Couldn't tell which Instagram account is logged in. Log in again in the Instagram window"}
        page = ctx.new_page()
        try:
            page.goto("https://www.instagram.com/?hl=en", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            res = page.evaluate(IG_JS, {"username": clean_handle(self.s.ig_username), "uid": uid,
                                        "csrf": cookies.get("csrftoken", "")}) or {}
            if res.get("error") or not res.get("reels_ok"):
                # Instagram's data requests are refused (it rate-limits them): read the profile's
                # public Reels page instead, the way a person would see it.
                page_res = self._instagram_page(page, res.get("username") or clean_handle(self.s.ig_username))
                if page_res.get("ok"):
                    self.log("Analytics: instagram read from the profile page (" + "; ".join(res.get("notes") or [])[:160] + ")")
                    return page_res
                if res.get("error"):
                    return {"ok": False, "error": f"{res['error']}. Profile page: {page_res.get('error')}"}
        finally:
            page.close()
        if res.get("notes"):   # some requests failed but a fallback worked: note which, for fixing later
            self.log("Analytics: instagram partly read (" + "; ".join(res["notes"])[:200] + ")")
        reels = res.get("reels") or []
        top = sorted(reels, key=lambda r: r.get("views") or 0, reverse=True)[:5]
        return {"ok": True, "handle": res.get("username"), "followers": res.get("followers"),
                "posts": len(reels) if reels else res.get("posts", 0), "all_posts": res.get("posts"),
                "views": sum(r.get("views") or 0 for r in reels), "likes": sum(r.get("likes") or 0 for r in reels),
                "top": [{"title": r.get("caption") or "Reel", "views": r.get("views") or 0,
                         "url": f"https://www.instagram.com/reel/{r.get('code')}/"} for r in top]}

    def _instagram_page(self, page, username: str) -> dict:
        """Followers and post count from the profile's page description, views from the tiles
        of its Reels tab. `page` is on instagram.com (logged in)."""
        if not username:   # the "Profile" link in Instagram's side menu points at /<username>/
            username = page.evaluate(IG_ME_JS) or ""
        if not username:
            return {"ok": False, "error": "couldn't find the account name (type it in the Instagram box above)"}
        page.goto(f"https://www.instagram.com/{username}/reels/?hl=en", wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        desc = page.evaluate("""() => (document.querySelector('meta[name="description"]') || {}).content ||
                                      (document.querySelector('meta[property="og:description"]') || {}).content || ''""")
        items = self._scroll_collect(page, IG_TILES_JS, rounds=25)
        followers = posts = None
        m = re.search(r"([\d.,]+\s*[KMBkmb]?)\s+Followers", desc or "")
        if m:
            followers = parse_count(m.group(1))
        m = re.search(r"([\d.,]+\s*[KMBkmb]?)\s+Posts", desc or "")
        if m:
            posts = parse_count(m.group(1))
        if followers is None and not items:
            return {"ok": False, "error": f"couldn't read instagram.com/{username}/reels"}
        reels = [{"code": i.get("code"), "views": parse_count(i.get("views")) or 0, "title": i.get("title") or "Reel"}
                 for i in items if i.get("code")]
        top = sorted(reels, key=lambda r: r["views"], reverse=True)[:5]
        return {"ok": True, "handle": username, "followers": followers,
                "posts": len(reels) if reels else posts or 0, "all_posts": posts,
                "views": sum(r["views"] for r in reels), "likes": None,
                "top": [{"title": r["title"], "views": r["views"],
                         "url": f"https://www.instagram.com/reel/{r['code']}/"} for r in top]}

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

# Every request is read as text first (Instagram sometimes answers with an empty body), and
# each piece has a fallback: reels from the Reels tab or else the profile feed, the username
# from the reels themselves, follower counts from the profile or else the account info.
# If something still fails, the error says which request and its HTTP status.
IG_JS = """async ({username, uid, csrf}) => {
  const H = {"x-ig-app-id": "936619743392459", "x-requested-with": "XMLHttpRequest", "x-csrftoken": csrf};
  const notes = [];
  const get = async (label, url, o = {}) => {
    try {
      const r = await fetch(url, {credentials: "include", signal: AbortSignal.timeout(20000), ...o,
                                  headers: {...H, ...(o.headers || {})}});
      const t = await r.text();
      const title = ((t.match(/<title>([^<]{0,60})/) || [])[1] || "").trim();
      if (!r.ok || !t.trim()) { notes.push(label + ": HTTP " + r.status + (t.trim() ? "" : ", empty answer") + (title ? " '" + title + "'" : "")); return null; }
      try { return JSON.parse(t); } catch (e) { notes.push(label + ": not JSON (HTTP " + r.status + (title ? ", page '" + title + "'" : "") + ")"); return null; }
    } catch (e) { notes.push(label + ": " + (e.name || e)); return null; }
  };
  const reel = (m) => ({code: m.code, views: m.play_count || m.ig_play_count || m.view_count || 0,
                        likes: m.like_count || 0, caption: ((m.caption && m.caption.text) || "").slice(0, 90)});
  const reels = [];
  let owner = null, reelsOk = false;

  // 1) the Reels tab
  let maxId = "", more = true, pages = 0;
  while (more && pages < 40) {
    const body = new URLSearchParams({target_user_id: uid, page_size: "50", include_feed_video: "true"});
    if (maxId) body.set("max_id", maxId);
    const j = await get("reels list", "/api/v1/clips/user/", {method: "POST", body,
                        headers: {"content-type": "application/x-www-form-urlencoded"}});
    if (!j) break;
    reelsOk = true;
    for (const it of (j.items || [])) { const m = it.media || {}; owner = owner || m.user; reels.push(reel(m)); }
    more = !!(j.paging_info && j.paging_info.more_available);
    maxId = (j.paging_info && j.paging_info.max_id) || "";
    pages++;
  }
  // 2) fallback: the profile feed, keeping the videos
  if (!reelsOk) {
    let next = "";
    pages = 0;
    do {
      const j = await get("profile feed", "/api/v1/feed/user/" + uid + "/?count=33" + (next ? "&max_id=" + next : ""));
      if (!j) break;
      reelsOk = true;
      for (const m of (j.items || [])) {
        owner = owner || m.user;
        if (m.media_type === 2 || m.product_type === "clips") reels.push(reel(m));
      }
      next = j.more_available ? (j.next_max_id || "") : "";
      pages++;
    } while (next && pages < 40);
  }

  // 3) username, followers, post count
  username = username || (owner && owner.username) || "";
  let followers = null, posts = null;
  if (username) {
    const j = await get("profile", "/api/v1/users/web_profile_info/?username=" + encodeURIComponent(username));
    const p = j && j.data && j.data.user;
    if (p) { followers = p.edge_followed_by.count; posts = p.edge_owner_to_timeline_media.count; }
  }
  if (followers === null) {
    const j = await get("account info", "/api/v1/users/" + uid + "/info/");
    const u = j && j.user;
    if (u) { followers = u.follower_count; posts = u.media_count; username = username || u.username; }
  }
  if (!reelsOk && followers === null) return {error: "Instagram didn't answer (" + notes.join("; ") + ")", username, notes};
  return {username, followers, posts, reels, reels_ok: reelsOk, notes};
}"""

# The logged-in account's name, from Instagram's side menu ("Profile" link, or the profile picture's alt text).
IG_ME_JS = """() => {
  const skip = new Set(["explore", "reels", "direct", "accounts", "p", "reel", "stories", "about", "legal"]);
  const name = (href) => { const m = (href || "").match(/^\\/([A-Za-z0-9._]+)\\/$/); return m && !skip.has(m[1]) ? m[1] : ""; };
  for (const a of document.querySelectorAll('a[href]'))
    if (/^profile$/i.test((a.innerText || "").trim()) && name(a.getAttribute("href"))) return name(a.getAttribute("href"));
  for (const img of document.querySelectorAll('img[alt]')) {
    const m = img.alt.match(/^([A-Za-z0-9._]+)'s profile picture$/);
    if (m) return m[1];
  }
  return "";
}"""

# Reels-tab tiles: each links to /<user>/reel/<code>/ (or /reel/<code>/) and shows its view count.
IG_TILES_JS = """() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/reel/"]')) {
    const m = a.getAttribute("href").match(/\\/reel\\/([A-Za-z0-9_-]+)/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    const nums = (a.innerText || "").split(/\\n/).map(s => s.trim()).filter(s => /^\\d[\\d.,]*\\s*[KMBkmb]?$/.test(s));
    const img = a.querySelector("img[alt]");
    out.push({code: m[1], views: nums[0] || "0", title: img ? img.alt.slice(0, 90) : ""});
  }
  return out;
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
