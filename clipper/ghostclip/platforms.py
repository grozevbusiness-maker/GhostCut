"""Posting to TikTok and YouTube Shorts in the app's browser window, plus the text every
platform gets (credit to the streamer, swear words starred out).

Each flow does what a person does on the site: open the upload page, pick the video, write the
description, press Post. Buttons are found by the sites' own ids (the same in every language)
with button text as a fallback. If a step can't be found, the caller saves a screenshot and
leaves the window at that step so the post can be finished by hand.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from .profanity import censor_word

PLATFORMS = ("instagram", "tiktok", "youtube")
NAMES = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}
LOGIN_URL = {
    "instagram": "https://www.instagram.com/?hl=en",
    "tiktok": "https://www.tiktok.com/login",
    "youtube": "https://studio.youtube.com/",   # sends you to Google's sign-in when logged out
}
SITE = {"instagram": "https://www.instagram.com/", "tiktok": "https://www.tiktok.com/",
        "youtube": "https://www.youtube.com/"}
LOGIN_COOKIES = {"instagram": {"sessionid"}, "tiktok": {"sessionid", "sessionid_ss", "sid_tt"},
                 "youtube": {"SID", "__Secure-3PSID", "LOGIN_INFO"}}
TEXT_LIMIT = {"instagram": 2200, "tiktok": 2200, "youtube": 4900}


class NeedLogin(Exception):
    pass


# ---------------------------------------------------------------------------
# The text: credit to the streamer on every platform, swearing starred out

def credit_line(channel: str, platform: str = "") -> str:
    link = f"https://www.twitch.tv/{channel}" if platform == "youtube" else f"twitch.tv/{channel}"
    return f"🎥 Credit: {link}"


def _censor(text: str, censor: list[str] | None) -> str:
    if censor is None:
        return text
    # never touch a link: a channel name can look like a swear word
    return re.sub(r"\S+", lambda m: m.group(0) if "twitch.tv" in m.group(0).lower()
                  else censor_word(m.group(0), censor), text)


def with_credit(text: str, channel: str, platform: str = "") -> str:
    """Add the streamer's twitch.tv link (once), above the hashtags."""
    text = (text or "").strip()
    if not channel:
        return text
    if f"twitch.tv/{channel.lower()}" not in text.lower():
        parts = text.split("\n\n") if text else []
        line = credit_line(channel, platform)
        if parts and all(w.startswith("#") for w in parts[-1].split()):
            parts.insert(len(parts) - 1, line)
        else:
            parts.append(line)
        text = "\n\n".join(parts)
    if platform == "youtube":   # YouTube only makes a link clickable with https://
        text = re.sub(r"(?<![/\w.])(?:www\.)?twitch\.tv/", "https://www.twitch.tv/", text, flags=re.I)
    return text


def final_caption(text: str, channel: str, platform: str, censor: list[str] | None = None) -> str:
    """What gets posted: swearing starred out (censor=None leaves it), credit added, within the
    platform's length limit."""
    text = with_credit(_censor(text or "", censor), channel, platform)
    if platform == "youtube":
        text = text.replace("<", "‹").replace(">", "›")   # YouTube refuses < and > in descriptions
    limit = TEXT_LIMIT.get(platform, 2200)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0]
    return text


def youtube_title(text: str, channel: str, censor: list[str] | None = None) -> str:
    """The first real line of the caption (no hashtags, no credit), at most 100 characters."""
    title = ""
    for line in (text or "").splitlines():
        line = re.sub(r"(^|\s)#\S+", " ", line).strip()
        if line and "twitch.tv" not in line.lower():
            title = line
            break
    title = _censor(title, censor).replace("<", "").replace(">", "").strip() or f"{channel} moment"
    if len(title) > 100:
        title = title[:99].rsplit(" ", 1)[0] + "…"
    return title


# ---------------------------------------------------------------------------
# TikTok (TikTok Studio upload page)

TIKTOK_UPLOAD = "https://www.tiktok.com/tiktokstudio/upload?from=webapp&lang=en"
TIKTOK_POST = re.compile(r"^(Post|Publicar|Publier|Posten|Pubblica|Опубликовать|Публикуване|Opublikuj|Paylaş)$", re.I)
TIKTOK_POST_NOW = re.compile(r"^(Post now|Publicar ahora|Publier maintenant|Jetzt posten|Публикувай сега)$", re.I)
TIKTOK_DISMISS = re.compile(r"^(Got it|Not now|Skip|OK|Разбрах|Не сега|Entendido|Ahora no|Compris|Verstanden)$", re.I)
TIKTOK_DONE = re.compile(r"(video (has been |was )?(uploaded|posted|published)|your video is being uploaded|"
                         r"manage your posts|видеоклипът е публикуван|publicado|publiée|veröffentlicht)", re.I)


def _dismiss(page, pattern: re.Pattern, scope: str = '[role="dialog"]') -> bool:
    """Close a tip or pop-up if one is showing (only buttons inside a dialog)."""
    try:
        b = page.locator(scope).get_by_role("button", name=pattern).first
        if b.count() and b.is_visible():
            b.click(timeout=2000)
            page.wait_for_timeout(500)
            return True
    except Exception:
        pass
    return False


def _type_lines(page, text: str) -> None:
    lines = text.split("\n")
    for j, line in enumerate(lines):
        if line:
            page.keyboard.insert_text(line)
        if j < len(lines) - 1:
            page.keyboard.press("Enter")


def _enabled(btn) -> bool:
    try:
        if not btn.count() or not btn.is_visible() or not btn.is_enabled():
            return False
        return (btn.get_attribute("aria-disabled") or "").lower() != "true" and \
            (btn.get_attribute("data-disabled") or "").lower() != "true"
    except Exception:
        return False


def post_tiktok(page, clip: Path, caption: str, say: Callable[[str], None], step: Callable[[str], None]) -> str:
    """Returns a link to the post when TikTok shows one (else "")."""
    step("opening TikTok Studio")
    say("Opening TikTok...")
    page.goto(TIKTOK_UPLOAD, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    if "/login" in page.url:
        raise NeedLogin()

    step("uploading the video")
    say("Uploading the video...")
    inp = page.locator('input[type="file"]').first
    inp.wait_for(state="attached", timeout=60000)
    inp.set_input_files(str(clip))

    step("the description box")
    box = page.locator('.public-DraftEditor-content, div[contenteditable="true"]').first
    box.wait_for(timeout=120000)
    page.wait_for_timeout(2500)   # TikTok writes the file name in there first; wait, then replace it
    _dismiss(page, TIKTOK_DISMISS)
    say("Writing the description...")
    box.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    _type_lines(page, caption)
    page.wait_for_timeout(800)
    page.keyboard.press("End")     # leave any #hashtag suggestion list closed
    _dismiss(page, TIKTOK_DISMISS)

    step("waiting for the upload to finish")
    say("Waiting for TikTok to finish uploading...")
    post = page.locator('button[data-e2e="post_video_button"]')
    deadline = time.time() + 900
    while True:
        if not post.count():
            post = page.get_by_role("button", name=TIKTOK_POST)
        if _enabled(post.first):
            break
        if time.time() > deadline:
            raise TimeoutError("the Post button never became available")
        _dismiss(page, TIKTOK_DISMISS)
        page.wait_for_timeout(2000)

    step("the Post button")
    say("Posting...")
    post.first.click(timeout=20000)

    step("waiting for TikTok to finish")
    deadline = time.time() + 600
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        # "Continue to post?" (the copyright check is still running): post anyway
        _dismiss(page, TIKTOK_POST_NOW)
        if "/tiktokstudio/content" in page.url or "/tiktokstudio/upload" not in page.url:
            return ""
        try:
            if TIKTOK_DONE.search(page.locator("body").inner_text(timeout=5000)):
                return ""
        except Exception:
            pass
    raise TimeoutError("TikTok never said the video was posted")


# ---------------------------------------------------------------------------
# YouTube Shorts (YouTube Studio upload dialog). A vertical video up to 3 minutes is a Short.

def post_youtube(page, clip: Path, title: str, description: str, say: Callable[[str], None],
                 step: Callable[[str], None]) -> str:
    """Returns the video's link."""
    step("opening YouTube Studio")
    say("Opening YouTube Studio...")
    page.goto("https://www.youtube.com/upload", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    if "accounts.google.com" in page.url or "ServiceLogin" in page.url:
        raise NeedLogin()
    if page.locator("ytd-channel-creation-dialog-renderer, #create-channel-button").count():
        raise RuntimeError("this Google account has no YouTube channel yet: create one in the window first")

    step("uploading the video")
    say("Uploading the video...")
    inp = page.locator('input[type="file"]').first
    inp.wait_for(state="attached", timeout=60000)
    inp.set_input_files(str(clip))

    step("the title box")
    say("Writing the title and description...")
    tbox = page.locator("#title-textarea #textbox")
    tbox.wait_for(timeout=120000)
    page.wait_for_timeout(2000)
    tbox.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.insert_text(title)

    step("the description box")
    dbox = page.locator("#description-textarea #textbox")
    dbox.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    _type_lines(page, description)

    step("the 'not made for kids' choice")
    page.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').click(timeout=20000)

    step("the Next button")
    for _ in range(3):   # Details -> Video elements -> Checks -> Visibility
        page.locator("#next-button").click(timeout=30000)
        page.wait_for_timeout(1500)

    step("choosing Public")
    say("Making it public...")
    page.locator('tp-yt-paper-radio-button[name="PUBLIC"]').click(timeout=20000)

    step("the Publish button")
    done = page.locator("#done-button")
    deadline = time.time() + 900
    while not _enabled(done):
        if time.time() > deadline:
            raise TimeoutError("the Publish button never became available")
        page.wait_for_timeout(2000)
    say("Publishing...")
    done.click()

    step("waiting for YouTube to finish")
    finished = page.locator("ytcp-video-share-dialog, ytcp-uploads-still-processing-dialog")
    warning = page.locator("ytcp-prechecks-warning-dialog #primary-action-button")   # "Publish anyway"
    deadline = time.time() + 600
    while True:
        try:
            if finished.first.is_visible():
                break
            if warning.count() and warning.first.is_visible():
                warning.first.click(timeout=5000)
        except Exception:
            pass
        if time.time() > deadline:
            raise TimeoutError("YouTube never said the video was published")
        page.wait_for_timeout(1500)
    link = ""
    try:
        link = page.locator('ytcp-video-share-dialog a[href*="youtu"], ytcp-uploads-still-processing-dialog a[href*="youtu"]'
                            ).first.get_attribute("href", timeout=5000) or ""
    except Exception:
        pass
    try:
        page.locator("ytcp-video-share-dialog #close-button, ytcp-uploads-still-processing-dialog #close-button"
                     ).first.click(timeout=5000)
    except Exception:
        pass
    return link


def prepare(info: dict, platform: str, settings, caption: str | None = None) -> tuple[str, str]:
    """(text, title) to post a clip with: its post caption (or the one given), swearing starred
    out if that's on, the streamer credited. title is for YouTube."""
    channel = info.get("channel") or ""
    text = caption if caption else info.get("post") or ""
    censor = list(getattr(settings, "censor_words", []) or []) if getattr(settings, "censor_swears", True) else None
    return final_caption(text, channel, platform, censor), youtube_title(text, channel, censor)
