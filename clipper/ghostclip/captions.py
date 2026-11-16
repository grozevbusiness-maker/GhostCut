"""Speech-to-text with word timings (faster-whisper) and caption styling (ASS subtitles)."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Callable

from .profanity import censor_word


@dataclass
class Word:
    start: float
    end: float
    text: str


class Transcriber:
    """Loads the Whisper model once and keeps it for every clip."""

    def __init__(self, model: str, device: str, language: str, log: Callable[[str], None]):
        self.model_name, self.device, self.language, self.log = model, device, language, log
        self._model = None
        self._lock = threading.Lock()
        self.available = True

    def _load(self, device: str):
        from faster_whisper import WhisperModel  # imported here so the app starts fast
        if device in ("auto", "cuda"):
            try:
                m = WhisperModel(self.model_name, device="cuda", compute_type="float16")
                self.log(f"Captions: Whisper '{self.model_name}' on the graphics card")
                return m, "cuda"
            except Exception as e:  # no NVIDIA GPU or CUDA libraries missing
                if device == "cuda":
                    self.log(f"Captions: GPU not usable ({e}); using the CPU")
        m = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        self.log(f"Captions: Whisper '{self.model_name}' on the CPU")
        return m, "cpu"

    def transcribe(self, wav_path: str) -> list[Word]:
        with self._lock:
            if self._model is None:
                try:
                    self.log(f"Captions: loading the speech model (first time downloads it, ~{_size(self.model_name)})")
                    self._model, self._on = self._load(self.device)
                except ImportError:
                    self.available = False
                    self.log("Captions: faster-whisper is not installed, so clips have no captions. Run setup again.")
                    return []
            try:
                return self._run(wav_path)
            except Exception as e:
                if self._on == "cuda":   # usually missing cuBLAS/cuDNN on Windows: fall back to CPU
                    self.log(f"Captions: GPU failed ({str(e)[:120]}); switching to the CPU")
                    self._model, self._on = self._load("cpu")
                    return self._run(wav_path)
                raise

    def _run(self, wav_path: str) -> list[Word]:
        words = self._pass(wav_path, vad=True)
        if not words:
            # the voice filter can mistake speech over loud game audio or music for noise
            words = self._pass(wav_path, vad=False)
        return words

    def _pass(self, wav_path: str, vad: bool) -> list[Word]:
        # Hand the model raw samples: faster-whisper's own file reader (PyAV) breaks with newer PyAV versions.
        segments, _info = self._model.transcribe(
            read_wav(wav_path), language=self.language or None, word_timestamps=True, vad_filter=vad,
            beam_size=5, condition_on_previous_text=False,
        )
        words = []
        for seg in segments:
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
                continue
            for w in seg.words or []:
                if w.word.strip():
                    words.append(Word(float(w.start), float(w.end), w.word.strip()))
        return words


def read_wav(path: str):
    """16 kHz mono 16-bit WAV (as ffmpeg writes it) -> float32 samples in [-1, 1]."""
    import wave

    import numpy as np
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
            raise ValueError("expected 16 kHz mono 16-bit audio")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def _size(model: str) -> str:
    return {"tiny": "75 MB", "base": "145 MB", "small": "480 MB", "medium": "1.5 GB"}.get(model, "3 GB")


# ---------------------------------------------------------------------------
# Caption layout

LAYOUTS = {
    #               width, height, caption x, y, font size, credit position
    "vertical_blur": (1080, 1920, 540, 1430, 84, r"{\an2\pos(540,630)}"),
    "vertical_crop": (1080, 1920, 540, 1350, 88, r"{\an8\pos(540,150)}"),
    "stack":         (1080, 1920, 540, 1560, 84, r"{\an7\pos(24,18)}"),
    "original":      (1920, 1080, 960, 940, 76, r"{\an9\pos(1880,40)}"),
}

_HIGHLIGHT = r"{\c&H00F2FF&\fscx112\fscy112}"   # yellow (ASS colours are BGR)
_NORMAL = r"{\c&HFFFFFF&\fscx100\fscy100}"


def _clean(text: str) -> str:
    text = text.replace("\\", "").replace("{", "(").replace("}", ")")
    text = re.sub(r"[,.;:]+$", "", text)
    return text.upper()


def _ts(t: float) -> str:
    t = max(t, 0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def chunk_words(words: list[Word], max_words: int = 3, max_chars: int = 16,
                censor: list[str] | None = None) -> list[list[Word]]:
    """censor: None leaves swearing as said; a list (maybe empty: extra words) stars it out."""
    chunks, cur = [], []
    for w in words:
        text = _clean(censor_word(w.text, censor) if censor is not None else w.text)
        if not text:
            continue
        w = Word(w.start, w.end, text)
        gap = w.start - cur[-1].end if cur else 0
        chars = sum(len(x.text) for x in cur) + len(text)
        if cur and (len(cur) >= max_words or chars > max_chars or gap > 0.5):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[?!]$", text):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[Word], layout: str, duration: float, font: str = "Arial Black",
              credit: str = "", censor: list[str] | None = None) -> str:
    W, H, cx, cy, size, credit_pos = LAYOUTS.get(layout, LAYOUTS["vertical_blur"])
    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {W}\nPlayResY: {H}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,3,5,60,60,0,1\n"
        f"Style: Credit,{font},{int(size * 0.45)},&H33FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,3,1,8,40,40,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    if credit:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(duration)},Credit,,0,0,0,,{credit_pos}{credit}")
    chunks = chunk_words(words, censor=censor)
    for i, chunk in enumerate(chunks):
        nxt = chunks[i + 1][0].start if i + 1 < len(chunks) else duration
        c_end = min(chunk[-1].end + 0.5, nxt, duration)
        for j, w in enumerate(chunk):
            a = chunk[0].start if j == 0 else w.start
            b = chunk[j + 1].start if j + 1 < len(chunk) else c_end
            if b - a < 0.02:
                continue
            pop = r"{\fscx85\fscy85\t(0,90,\fscx100\fscy100)}" if j == 0 else ""
            text = " ".join(
                (_HIGHLIGHT + x.text + _NORMAL) if k == j else x.text for k, x in enumerate(chunk)
            )
            lines.append(f"Dialogue: 0,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{\\an5\\pos({cx},{cy})}}{pop}{text}")
    return head + "\n".join(lines) + "\n"
