"""Decide when chat is hyped enough to clip.

Every chat message gets a hype weight (laughing, shock, "clip it", emote spam). Once a second
we add up the last few seconds and compare that with the channel's own normal level, so it
works for a 20-viewer stream and a 100k one.

Built for huge chats (Kai Cenat, Fanum, Jason, Duke Dennis... on a GTA 6 launch day), where
chat never stops spamming. Tuned on real chat replays of Kai Cenat's GTA marathon:
- each chatter counts once per window, so one person's emote wall isn't a moment;
- what chat spams *all the time* (W, "get sturdy", "GTA 6", 🔥) fades out: a word only counts
  fully when it surges above its own normal level for this channel;
- W / L / GG are weak (sub gifts and raids trigger them, not moments); laughing is strongest;
- shock words ("nah", "huh", "damn", "yooo") only count in short messages;
- the more clips a stream made in the last hour, the higher the bar (an hourly budget set
  by the sensitivity slider), so a wild day keeps only its best moments.
"""
from __future__ import annotations

import math
import re
from collections import Counter, deque
from dataclasses import dataclass, field

from .chat import ChatMessage

BOTS = {"nightbot", "streamelements", "streamlabs", "moobot", "fossabot", "soundalerts",
        "wizebot", "botrixoficial", "sery_bot", "pokemoncommunitygame", "kofistreambot"}

# matched against a word with its repeated letters squeezed ("LMAOOOOO" -> "lmao", "loool" -> "lol")
LAUGH_PATTERNS = [re.compile(p) for p in (
    r"^(ha){2,}h?$", r"^(he){3,}$", r"^l+m+f*a+o+$", r"^lo+l+$", r"^kek[wl]?$", r"^x+d+$",
    r"^omegalu+l$", r"^lu+lw?$", r"^rofl+$", r"^jaja(ja)*$", r"^kkkk+$", r"^ahah(ah)*$",
    r"^lmf?ao$", r"^lul$", r"^icant$", r"^sob$", r"^crying$",
    r"^[a-z0-9]+(lul|lulw|kekw|kekl|omegalul|lmao|lol|icant|laugh|haha)$",   # channel emotes: kaicLUL, JinxLUL
)]
HYPE_PATTERNS = [re.compile(p) for p in (
    r"^po+g+(gers|champ|u)?$", r"^let'?s+go+$", r"^\?+$", r"^!+$", r"^o+m+g+$", r"^w+t+f+$",
    r"^[a-z0-9]+(pog|poggers|pogu|wow|omg|shock|scream)$",
)]
# only when stretched out as typed: "NOOOO", "YOOOO", "BROOOO" (plain "no", "yo", "bro" is chat talk)
STRETCHED = re.compile(r"^(no{2,}|yo{2,}|ayo{2,}|bro{2,}|o{3,}|wo{2,}w?|ah{3,})$")
# chat that shows up for sub gifts, raids and "GG" as much as for moments: counts a little
WEAK = {"w", "l", "gg", "ggs", "ez"}
# celebrating ("LETS GOOO", POG, 🔥): on a launch day it never stops, so it can add to a moment
# that laughing or shock started, but can't start one by itself
CELEBRATE = {"pog", "pogers", "pogchamp", "pogu", "pagman", "pag", "letsgo", "insane", "clean", "goated",
             "🔥", "banger", "shesh"}
CELEBRATE_RE = re.compile(r"^(po+g+(ers|champ|u)?|let'?s+go+|[a-z0-9]+(pog|poggers|pogu|hype))$")
# shock / disbelief: strong in a short message ("NAHHH", "HUH??"), noise in a sentence
SHORT_ONLY = re.compile(r"^(na+h+|naw+|hu+h+|da+m+n+|sh+e+sh|crazy|insane|holy)$")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿‼⁉]")
WORD_RE = re.compile(r"[\w']+|\?{2,}|!{3,}")


STRONG = {"laugh", "shock", "clip"}
KIND_SHOWN = {"laugh": "laugh", "clip": "clip"}   # everything else is shown as "hype"


def squeeze(word: str) -> str:
    """LMAOOOOO -> lmao, WWWWW -> w, loooool -> lol, ???? -> ?"""
    return re.sub(r"(.)\1+", r"\1", word.lower())


@dataclass
class HypeEvent:
    start: float        # wall time chat crossed the threshold
    peak: float         # wall time of the loudest second
    end: float          # wall time chat calmed down
    score: float        # peak score divided by the threshold (1.0 = just made it)
    kind: str = "hype"  # laugh | hype | clip | manual
    messages: list = field(default_factory=list)   # most representative chat lines


class MessageScorer:
    def __init__(self, laugh_words, hype_words, clip_phrases):
        self.update_words(laugh_words, hype_words, clip_phrases)

    def update_words(self, laugh_words, hype_words, clip_phrases):
        self.laugh = {squeeze(w) for w in laugh_words}
        self.hype = {squeeze(w) for w in hype_words}
        self.clip = [p.lower() for p in clip_phrases if p.strip()]

    def score(self, text: str) -> tuple[float, str]:
        """(weight, kind) with kind as clips show it: laugh | hype | clip."""
        weight, kind, _keys = self.score_keys(text)
        return weight, KIND_SHOWN.get(kind, "hype")

    def score_keys(self, text: str) -> tuple[float, str, set]:
        """Return (weight, kind, keys) for one message. Weight 0 means an ordinary message.

        kind is laugh | shock | celebrate | weak | clip. Laugh, shock and clip are "strong":
        only they can start a moment. keys are the squeezed words that made it count (used to
        spot what a chat spams all the time)."""
        low = text.lower()
        raw = WORD_RE.findall(low)
        words = [squeeze(w) for w in raw]
        short = len(words) <= 4
        laugh, shock, celeb, weak = set(), set(), set(), set()
        for r, w in zip(raw, words):
            if w in self.laugh or any(p.match(w) for p in LAUGH_PATTERNS):
                laugh.add(w)
            elif w in WEAK:
                weak.add(w)
            elif w in CELEBRATE or CELEBRATE_RE.match(w):
                celeb.add(w)
            elif (w in self.hype or any(p.match(w) for p in HYPE_PATTERNS) or STRETCHED.match(r)
                  or (short and SHORT_ONLY.match(w))):
                shock.add(w)
        if re.search(r"\blet'?s+\s+go{2,}", low) or re.search(r"\blets go\b", low) and short:
            celeb.add("letsgo")
        for e in EMOJI_RE.findall(text):
            if e in self.laugh:
                laugh.add(e)
            elif e in CELEBRATE:
                celeb.add(e)
            elif e in self.hype:
                shock.add(e)
        # "clip it" counts when it's what the message is about, not "some clip went viral"
        clip = len(words) <= 6 and any(re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", low) for p in self.clip)
        keys = laugh | shock | celeb | weak | ({"<clip>"} if clip else set())

        if clip:
            return 3.0, "clip", keys
        if laugh or shock:
            kind = "laugh" if len(laugh) >= len(shock) else "shock"
            weight = 1.0
            # spam ("KEKW KEKW KEKW", "😂😂😂😂") means people really mean it
            repeats = len(re.findall(r"[\w']+", low)) + len(EMOJI_RE.findall(text))
            if repeats >= 3 and len(set(words)) <= 2:
                weight += 0.5
            letters = [c for c in text if c.isalpha()]
            if len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
                weight += 0.25
            return weight, kind, keys
        if celeb:
            return 0.4, "celebrate", keys
        if weak:
            return 0.2, "weak", keys
        return 0.0, "hype", keys


def clips_per_hour(sensitivity: float) -> float:
    """The hourly budget the slider stands for: 0.4 -> ~1.5, 1.0 -> 5, 2.2 -> ~14."""
    return 5.0 * max(sensitivity, 0.1) ** 1.3


def min_peak(sensitivity: float) -> float:
    """How far above the clip line a moment must peak: 0.4 -> 2.25x, 1.0 -> 1.5x, 2.2 -> ~1.2x."""
    return 1.0 + 0.5 / max(sensitivity, 0.1)


class HypeDetector:
    def __init__(self, settings, window: float = 8.0):
        self.s = settings
        self.window = window
        self.scorer = MessageScorer(settings.laugh_words, settings.hype_words, settings.clip_phrases)
        self.recent: deque = deque()   # (ts, weight, kind, user, text, keys)
        self.baseline = 0.0
        self.ticks = 0
        self.event = None              # dict while a moment is in progress
        self.cooldown_until = 0.0
        self.score = 0.0
        self.threshold = 0.0
        self.history: deque = deque(maxlen=180)   # (ts, score, threshold) for the dashboard
        self.total_messages = 0
        self.normal: dict[str, float] = {}         # word -> chatters/second using it normally
        self.made: deque = deque()                 # start times of this stream's recent moments
        self._last_tick = None

    # -- input ---------------------------------------------------------------
    def add(self, msg: ChatMessage) -> None:
        if msg.user.lower() in BOTS or msg.text.startswith("!"):
            return
        self.total_messages += 1
        weight, kind, keys = self.scorer.score_keys(msg.text)
        if msg.emotes >= 3 and weight == 0:
            weight = 0.3
            keys = {"<emotes>"}
        self.recent.append((msg.ts, weight, kind, msg.user, msg.text, keys))

    # -- once a second --------------------------------------------------------
    def tick(self, now: float) -> HypeEvent | None:
        while self.recent and self.recent[0][0] < now - 120:
            self.recent.popleft()
        while self.made and self.made[0] < now - 3600:
            self.made.popleft()
        win = [m for m in self.recent if m[0] >= now - self.window]

        # each chatter once: their strongest message in the window (laughing/shock before celebrating)
        best: dict[str, tuple] = {}
        rank = lambda m: (m[2] in STRONG, m[1])   # noqa: E731
        for m in win:
            if m[3] not in best or rank(m) > rank(best[m[3]]):
                best[m[3]] = m
        users = list(best.values())

        # how much each word is being used right now vs. normally in this chat
        now_rate = Counter()
        for m in users:
            now_rate.update(m[5])
        warm = self.ticks >= 45
        surge = {k: (c / self.window) / (self.normal.get(k, 0.0) + 0.08) for k, c in now_rate.items()}

        def novelty(keys) -> float:
            if not keys or not warm:
                return 1.0
            # a word at its usual level counts ~1/3; twice its usual level or more counts fully
            return max(0.25, min(1.0, max(surge.get(k, 1.0) for k in keys) / 2.0))

        weighted = [(m, m[1] * novelty(m[5])) for m in users]
        # voices: chatters laughing, shocked or asking for a clip, with something new (not the usual spam)
        voices = [(m, w) for m, w in weighted if m[2] in STRONG and w >= 0.3]
        # the score counts reactions, not raw volume: a flood of chatters barely moves it by itself
        score = sum(w for _m, w in weighted) + 0.05 * len(users)

        sens = max(self.s.sensitivity, 0.1)
        min_score = 5.0 / sens
        mult = 2.6 / sens
        # hourly budget: each clip already made this hour raises the bar, steeply past the budget,
        # and twice the budget is a hard ceiling (the slider's number is what you get on a wild day)
        budget = clips_per_hour(sens)
        used = len(self.made) / budget
        bar = 1.0 + 1.6 * used * used if used <= 1 else 2.6 * 4 ** (used - 1)
        full = len(self.made) >= 2 * budget
        threshold = max(min_score * (1.0 if warm else 1.5), mult * self.baseline) * bar
        self.score, self.threshold = score, threshold
        self.history.append((now, round(score, 2), round(threshold, 2)))
        self.ticks += 1

        # learn each word's normal level (about the last 5 minutes)
        dt = 1.0 if self._last_tick is None else min(max(now - self._last_tick, 0.2), 5.0)
        self._last_tick = now
        this_second = Counter()
        for m in users:
            if m[0] >= now - dt:
                this_second.update(m[5])
        a = 1 / 300 if warm else 1 / max(self.ticks, 1)
        for k in set(self.normal) | set(this_second):
            v = self.normal.get(k, 0.0) + a * (this_second.get(k, 0) / dt - self.normal.get(k, 0.0))
            if v < 0.002 and k not in this_second:
                self.normal.pop(k, None)
            else:
                self.normal[k] = v

        if self.event is None:
            # learn what "normal" looks like for this chat (slow average, ignores spikes)
            alpha = 1 / 60 if warm else 1 / max(self.ticks, 1)
            self.baseline += alpha * (min(score, max(self.baseline * 2, min_score * 0.5)) - self.baseline)
            need = max(3, int(0.8 * math.sqrt(len(users))))   # a real crowd, not a handful, in a big chat
            if score >= threshold and now >= self.cooldown_until and len(voices) >= need and not full:
                self.event = {"start": now, "peak": now, "peak_score": score,
                              "threshold": threshold, "quiet": 0, "kinds": Counter()}
            else:
                return None

        ev = self.event
        ev["kinds"].update(KIND_SHOWN.get(m[2], "hype") for m, _w in voices)
        if score > ev["peak_score"]:
            ev["peak"], ev["peak_score"] = now, score
        ev["quiet"] = ev["quiet"] + 1 if score < 0.6 * ev["threshold"] else 0
        if ev["quiet"] >= 4 or now - ev["start"] >= 60:
            self.event = None
            # a moment that only just crossed the line isn't worth a clip: it must peak clearly
            # above it (how clearly is what the sensitivity slider sets)
            if ev["peak_score"] < ev["threshold"] * min_peak(sens):
                return None
            self.cooldown_until = now + self.s.cooldown
            return self._finish(ev, now - (ev["quiet"] if ev["quiet"] >= 4 else 0))
        return None

    def force_finish(self, now: float) -> HypeEvent | None:
        if self.event is None:
            return None
        ev, self.event = self.event, None
        return self._finish(ev, now)

    def _finish(self, ev, end: float) -> HypeEvent:
        self.made.append(ev["start"])
        kinds = ev["kinds"]
        kind = "clip" if kinds.get("clip", 0) >= 3 else (kinds.most_common(1)[0][0] if kinds else "hype")
        during = [m for m in self.recent if ev["start"] - 5 <= m[0] <= end]
        lines = [m for m in during if m[2] in STRONG] or [m for m in during if m[1] > 0]
        common = Counter(m[4].strip() for m in lines).most_common(8)
        return HypeEvent(start=ev["start"], peak=ev["peak"], end=end,
                         score=round(ev["peak_score"] / ev["threshold"], 2), kind=kind,
                         messages=[f"{t} (x{n})" if n > 1 else t for t, n in common])

    def rate_per_min(self, now: float) -> int:
        return sum(1 for m in self.recent if m[0] >= now - 60)
