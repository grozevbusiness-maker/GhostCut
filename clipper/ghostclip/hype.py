"""Decide when chat is hyped enough to clip.

Every chat message gets a hype weight (laughing, hype words, "clip it", emote spam).
Once a second we add up the weights of the last few seconds, and compare that with
the channel's own normal level, so it works for a 20-viewer stream and a 50k one.
A moment starts when the score jumps well above normal and ends when chat calms down.
"""
from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass, field

from .chat import ChatMessage

BOTS = {"nightbot", "streamelements", "streamlabs", "moobot", "fossabot", "soundalerts",
        "wizebot", "botrixoficial", "sery_bot", "pokemoncommunitygame", "kofistreambot"}

LAUGH_PATTERNS = [re.compile(p) for p in (
    r"^(ha){2,}h?$", r"^(he){3,}$", r"^l+m+f*a+o+$", r"^lo+l+$", r"^kek[wl]?$", r"^x+d+$",
    r"^omegalu+l$", r"^lu+lw?$", r"^rofl+$", r"^jaja(ja)*$", r"^kkkk+$", r"^ahah(ah)*$",
)]
HYPE_PATTERNS = [re.compile(p) for p in (r"^w+$", r"^po+g+(gers|champ|u)?$", r"^let'?s+go+$", r"^noo+$", r"^\?{2,}$", r"^!{3,}$", r"^o+m+g+$")]
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿‼⁉]")


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
        self.laugh = {w.lower() for w in laugh_words}
        self.hype = {w.lower() for w in hype_words}
        self.clip = [p.lower() for p in clip_phrases if p.strip()]

    def score(self, text: str) -> tuple[float, str]:
        """Return (weight, kind) for one message. Weight 0 means an ordinary message."""
        low = text.lower()
        words = re.findall(r"[\w']+|\?{2,}|!{3,}", low)
        emojis = EMOJI_RE.findall(text)
        laugh = sum(1 for w in words if w in self.laugh or any(p.match(w) for p in LAUGH_PATTERNS))
        laugh += sum(1 for e in emojis if e in self.laugh)
        hype = sum(1 for w in words if w in self.hype or any(p.match(w) for p in HYPE_PATTERNS))
        hype += sum(1 for e in emojis if e in self.hype)
        clip = any(re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", low) for p in self.clip)

        weight, kind = 0.0, "hype"
        if laugh or hype:
            weight = 1.0
            kind = "laugh" if laugh >= hype else "hype"
            # spam ("KEKW KEKW KEKW", "😂😂😂😂") means people really mean it
            if laugh + hype >= 3:
                weight += 0.5
            letters = [c for c in text if c.isalpha()]
            if len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
                weight += 0.25
        if clip:
            weight += 2.0
            kind = "clip"
        return min(weight, 3.0), kind


class HypeDetector:
    def __init__(self, settings, window: float = 8.0):
        self.s = settings
        self.window = window
        self.scorer = MessageScorer(settings.laugh_words, settings.hype_words, settings.clip_phrases)
        self.recent: deque = deque()   # (ts, weight, kind, user, text)
        self.baseline = 0.0
        self.ticks = 0
        self.event = None              # dict while a moment is in progress
        self.cooldown_until = 0.0
        self.score = 0.0
        self.threshold = 0.0
        self.history: deque = deque(maxlen=180)   # (ts, score, threshold) for the dashboard
        self.total_messages = 0

    # -- input ---------------------------------------------------------------
    def add(self, msg: ChatMessage) -> None:
        if msg.user.lower() in BOTS or msg.text.startswith("!"):
            return
        self.total_messages += 1
        weight, kind = self.scorer.score(msg.text)
        if msg.emotes >= 3 and weight == 0:
            weight = 0.5
        self.recent.append((msg.ts, weight, kind, msg.user, msg.text))

    # -- once a second --------------------------------------------------------
    def tick(self, now: float) -> HypeEvent | None:
        while self.recent and self.recent[0][0] < now - 120:
            self.recent.popleft()
        win = [m for m in self.recent if m[0] >= now - self.window]
        hype_msgs = [m for m in win if m[1] > 0]
        score = sum(m[1] for m in win) + 0.15 * len(win)
        ratio = len(hype_msgs) / len(win) if win else 0.0
        sens = max(self.s.sensitivity, 0.1)
        min_score = 5.0 / sens
        mult = 2.6 / sens
        warm = self.ticks >= 45
        threshold = max(min_score * (1.0 if warm else 1.5), mult * self.baseline)
        self.score, self.threshold = score, threshold
        self.history.append((now, round(score, 2), round(threshold, 2)))
        self.ticks += 1

        if self.event is None:
            # learn what "normal" looks like for this chat (slow average, ignores spikes)
            alpha = 1 / 60 if warm else 1 / max(self.ticks, 1)
            self.baseline += alpha * (min(score, max(self.baseline * 2, min_score * 0.5)) - self.baseline)
            if (score >= threshold and now >= self.cooldown_until
                    and len(hype_msgs) >= 3 and ratio >= 0.25):
                self.event = {"start": now, "peak": now, "peak_score": score,
                              "threshold": threshold, "quiet": 0, "kinds": Counter()}
            else:
                return None

        ev = self.event
        ev["kinds"].update(m[2] for m in hype_msgs)
        if score > ev["peak_score"]:
            ev["peak"], ev["peak_score"] = now, score
        ev["quiet"] = ev["quiet"] + 1 if score < 0.6 * ev["threshold"] else 0
        if ev["quiet"] >= 4 or now - ev["start"] >= 60:
            self.event = None
            self.cooldown_until = now + self.s.cooldown
            return self._finish(ev, now - (ev["quiet"] if ev["quiet"] >= 4 else 0))
        return None

    def force_finish(self, now: float) -> HypeEvent | None:
        if self.event is None:
            return None
        ev, self.event = self.event, None
        return self._finish(ev, now)

    def _finish(self, ev, end: float) -> HypeEvent:
        kinds = ev["kinds"]
        kind = "clip" if kinds.get("clip", 0) >= 3 else (kinds.most_common(1)[0][0] if kinds else "hype")
        lines = [m for m in self.recent if ev["start"] - 5 <= m[0] <= end and m[1] > 0]
        common = Counter(m[4].strip() for m in lines).most_common(8)
        return HypeEvent(start=ev["start"], peak=ev["peak"], end=end,
                         score=round(ev["peak_score"] / ev["threshold"], 2), kind=kind,
                         messages=[f"{t} (x{n})" if n > 1 else t for t, n in common])

    def rate_per_min(self, now: float) -> int:
        return sum(1 for m in self.recent if m[0] >= now - 60)
