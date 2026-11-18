"""User settings, stored as JSON next to the user's profile."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    d = Path(base) / "GhostClip"
    d.mkdir(parents=True, exist_ok=True)
    return d


SETTINGS_PATH = app_dir() / "settings.json"
MAX_STREAMS = 10
DEFAULT_ROOT = Path.home() / "Videos" / "GhostClip"


# Words and emotes that mean chat is laughing. Matched case-insensitively as whole tokens.
DEFAULT_LAUGH = [
    "lol", "lmao", "lmfao", "lmaoo", "lmfaooo", "rofl", "xd", "xdd", "kekw", "kek", "kekl",
    "lul", "lulw", "omegalul", "icant", "dead", "deadge", "pepelaugh", "hahaha", "haha",
    "😂", "🤣", "💀", "😭",
]
# Words and emotes that mean chat is hyped or shocked.
DEFAULT_HYPE = [
    "pog", "poggers", "pogchamp", "pogu", "pagman", "pag", "w", "ww", "www", "letsgo",
    "omg", "wtf", "noway", "monkas", "insane", "clean", "holy", "goated",
    "🔥", "😱", "😳", "‼️",
]
# Phrases that ask for a clip. They count extra.
DEFAULT_CLIP_PHRASES = ["clip it", "clip that", "clipped", "clip this", "someone clip", "clip"]


@dataclass
class Settings:
    channel: str = ""                  # last single channel (older versions)
    channels: list = field(default_factory=list)   # up to MAX_STREAMS channels shown in the dashboard
    games: dict = field(default_factory=dict)      # channel -> game key from games.py ("auto" if missing)
    clips_dir: str = str(DEFAULT_ROOT / "Clips")
    recordings_dir: str = str(DEFAULT_ROOT / "Recordings")
    keep_full_recording: bool = False  # False: keep only a rolling buffer, delete it all on Stop
    buffer_minutes: int = 15           # rolling buffer size when not keeping the full stream
    quality: str = "1080p60,1080p,936p60,720p60,720p,best"
    twitch_oauth: str = ""             # optional; Turbo/sub token removes ads from the recording

    # Clip timing (seconds). Chat reacts after the moment, so most of the clip is before the spike.
    pre_roll: float = 30.0
    post_roll: float = 10.0
    min_clip: float = 30.0
    max_clip: float = 90.0
    sync_offset: float = 0.0           # + moves clips later, - earlier, if they feel off

    # Detection
    sensitivity: float = 1.0           # higher = more clips, lower = only the biggest moments
    cooldown: float = 45.0             # seconds after a clip before another can start
    laugh_words: list = field(default_factory=lambda: list(DEFAULT_LAUGH))
    hype_words: list = field(default_factory=lambda: list(DEFAULT_HYPE))
    clip_phrases: list = field(default_factory=lambda: list(DEFAULT_CLIP_PHRASES))

    # Editing
    layout: str = "stack"              # stack | vertical_crop | vertical_blur | original
    facecam: str = "auto"              # auto | none | top-left | top-right | bottom-left | bottom-right
    captions: bool = True
    whisper_model: str = "small"       # tiny | base | small | medium | large-v3
    whisper_device: str = "auto"       # auto | cuda | cpu
    language: str = ""                 # "" = auto-detect, or "en", "es", ...
    caption_font: str = "Arial Black"
    credit: bool = True                # small "twitch.tv/channel" tag at the top
    censor_swears: bool = True         # "F***" in captions and post text (see profanity.py)
    mute_swears: bool = True           # silence the audio where a swear word is said
    censor_words: list = field(default_factory=list)   # extra words to star out and mute
    encoder: str = "auto"              # auto | nvenc | x264
    ffmpeg_path: str = ""              # leave empty to find ffmpeg automatically

    # Auto-posting to Instagram (see autopost.py)
    auto_post: bool = False            # post the best new clips by themselves
    auto_post_per_day: int = 5         # most posts in any 24 hours (by hand or automatic)
    auto_post_gap: int = 60            # minutes between posts at least
    auto_post_min_hype: float = 1.6    # only clips whose chat hit at least this x the clip line
    auto_post_since: float = 0.0       # set when switched on: only clips made after this are posted
    auto_post_to: list = field(default_factory=lambda: ["instagram"])   # instagram, tiktok, youtube

    # Analytics: your own accounts (where you post the clips)
    ig_username: str = ""              # empty = the account logged in to the Instagram window
    tiktok_handle: str = ""
    youtube_handle: str = ""
    youtube_api_key: str = ""          # optional; exact YouTube numbers via Google's API

    port: int = 8765

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if data.get("settings_version", 1) < 2:   # v2: full recording off by default
                data.pop("keep_full_recording", None)
            if data.get("settings_version", 1) < 3:   # v3: face cam on top + gameplay layout
                data.pop("layout", None)
            s.update(data)
        except (OSError, ValueError):
            pass
        return s

    def save(self) -> None:
        data = asdict(self)
        data["settings_version"] = 3
        SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def update(self, data: dict) -> None:
        for f in fields(self):
            if f.name not in data:
                continue
            cur = getattr(self, f.name)
            val = data[f.name]
            try:
                if isinstance(cur, dict):
                    if not isinstance(val, dict):
                        continue
                    val = {clean_channel(k): str(v) for k, v in val.items() if clean_channel(k)}
                elif isinstance(cur, bool):
                    val = val if isinstance(val, bool) else str(val).lower() in ("1", "true", "on", "yes")
                elif isinstance(cur, int):
                    val = int(float(val))
                elif isinstance(cur, float):
                    val = float(val)
                elif isinstance(cur, list):
                    if isinstance(val, str):
                        val = [w.strip() for w in val.split(",") if w.strip()]
                    val = list(val)
                else:
                    val = str(val)
            except (TypeError, ValueError):
                continue
            setattr(self, f.name, val)
        self.channel = clean_channel(self.channel)
        chans = []
        for c in self.channels:
            c = clean_channel(c)
            if c and c not in chans:
                chans.append(c)
        if not chans and self.channel and "channels" not in data:
            chans = [self.channel]
        self.channels = chans[:MAX_STREAMS]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["twitch_oauth"] = "set" if self.twitch_oauth else ""
        d["youtube_api_key"] = "set" if self.youtube_api_key else ""
        return d


def clean_channel(text: str) -> str:
    """Accept 'name', '#name', 'twitch.tv/name' or a full URL."""
    text = (text or "").strip()
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", text)
    if m:
        text = m.group(1)
    return text.lstrip("#@").strip().lower()


class ChannelView:
    """The shared settings, but with one stream's channel name. Changes to the shared settings apply live."""

    def __init__(self, base: Settings, channel: str):
        self._base = base
        self.channel = channel

    def __getattr__(self, name):
        return getattr(self._base, name)
