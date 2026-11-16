"""Swear words: star them out in captions and post text, and find where to mute them in the audio.

Instagram shows clips with swearing (spoken or written) to fewer people, so by default every clip
gets "F***" in its captions and silence where the streamer swore.
"""
from __future__ import annotations

import re

# Matched against the word in lower case with everything but letters and * removed, so
# "Fuckin'", "FUUUCK!" and "motherfucker" are all caught.
_ANYWHERE = [
    r"f+u+c*k+", r"f+c+k+", r"f+u+q+",            # fuck, fuk, fck, fuq and every word built on them
    r"sh+i+t", r"b+i+t+c+h", r"biatch",
    r"cunt", r"puss(y|ies)", r"dick", r"motherf",
    r"n+i+g{2,}", r"fagg?ot", r"retard",
    r"whore", r"slut", r"bastard", r"twat", r"wank",
]
_WHOLE = [
    r"(dumb|jack|bad|smart|fat|kick|lame|hard|wise|half|big|broke|punk|bitch|dick)?ass(es|hole|holes|wipe|hat)?",
    r"arse(hole)?s?", r"cocks?", r"cocksucker", r"fags?", r"hoes?", r"thots?", r"cum(ming|s)?",
    r"wtf", r"stfu", r"gtfo", r"mfs?",
]
_RE = re.compile("|".join(_ANYWHERE))
_RE_WHOLE = re.compile("(?:" + "|".join(_WHOLE) + ")")


def _letters(word: str) -> str:
    return re.sub(r"[^a-z*]", "", word.lower())


def is_swear(word: str, extra: list[str] | tuple = ()) -> bool:
    w = _letters(word)
    if not w:
        return False
    if "*" in w and re.search(r"[a-z]", w):   # the speech model wrote "f***" itself: still muted
        return True
    w = w.replace("*", "")
    if _RE.search(w) or _RE_WHOLE.fullmatch(w):
        return True
    return any(x and _letters(x) in w for x in extra)


def censor_word(word: str, extra: list[str] | tuple = ()) -> str:
    """'Fucking!' -> 'F***!'. Words that aren't swearing come back unchanged."""
    if not is_swear(word, extra):
        return word
    m = re.match(r"^([^A-Za-z]*)(.*?)([^A-Za-z*]*)$", word)
    lead, core, tail = m.groups() if m else ("", word, "")
    letters = [c for c in core if c.isalpha()]
    first = letters[0] if letters else ""
    return f"{lead}{first}***{tail}"


def censor_text(text: str, extra: list[str] | tuple = ()) -> str:
    return re.sub(r"\S+", lambda m: censor_word(m.group(0), extra), text or "")


def mute_spans(words, extra: list[str] | tuple = (), before: float = 0.08, after: float = 0.12,
               duration: float | None = None) -> list[tuple[float, float]]:
    """Time ranges (seconds into the clip) to silence: every swear word, a little padded, merged."""
    spans = []
    for w in words:
        if is_swear(w.text, extra):
            a, b = max(w.start - before, 0.0), w.end + after
            if duration is not None:
                b = min(b, duration)
            if b > a:
                spans.append((a, b))
    spans.sort()
    merged: list[tuple[float, float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 0.05:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def mute_filter(spans: list[tuple[float, float]], ramp: float = 0.025) -> str:
    """ffmpeg audio filters that silence those ranges, fading in and out over a few milliseconds
    so there's no click. Empty string when there's nothing to mute."""
    if not spans:
        return ""
    terms = "*".join(f"clip(max({a:.3f}-t,t-{b:.3f})/{ramp},0,1)" for a, b in spans)
    # small audio frames so the volume (worked out once per frame) changes smoothly
    return f"asetnsamples=n=256:p=0,volume='{terms}':eval=frame"
