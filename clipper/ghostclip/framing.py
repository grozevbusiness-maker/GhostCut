"""Find the streamer's face cam so the vertical clip can show it on top.

We look at a few frames from the clip, find faces (YuNet face detector, bundled),
and pick the one that stays put and sits away from the middle of the screen:
the middle is shown anyway in the gameplay part, so a face there is usually
something on stream (a video, a game character), not the face cam.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .utils import NO_WINDOW

MODEL = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"
FW, FH = 960, 540          # analysis size
CAM_ASPECT = 1080 / 700    # shape of the face cam panel on the phone (width / height)

# Face cam presets if auto-detection picks wrong: (x, y, w, h) as fractions of the frame
CORNERS = {
    "top-left": (0.0, 0.0, 0.27, 0.27 * 16 / 9 / CAM_ASPECT),
    "top-right": (0.73, 0.0, 0.27, 0.27 * 16 / 9 / CAM_ASPECT),
    "bottom-left": (0.0, 1 - 0.27 * 16 / 9 / CAM_ASPECT, 0.27, 0.27 * 16 / 9 / CAM_ASPECT),
    "bottom-right": (0.73, 1 - 0.27 * 16 / 9 / CAM_ASPECT, 0.27, 0.27 * 16 / 9 / CAM_ASPECT),
}


@dataclass
class Plan:
    mode: str                      # "stack" (cam on top + gameplay) or "crop" (one full-height crop)
    cam: tuple | None = None       # (x, y, w, h) fractions, for stack
    center_x: float = 0.5          # horizontal centre of the crop, for crop
    note: str = ""


def available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        return MODEL.exists()
    except ImportError:
        return False


def sample_frames(ffmpeg: str, cwd: Path, src_args: list[str], duration: float, n: int = 8):
    import numpy as np
    fps = n / max(duration, 1.0)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", *src_args,
           "-vf", f"fps={fps:.5f},scale={FW}:{FH}", "-frames:v", str(n),
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, creationflags=NO_WINDOW, timeout=120)
    size = FW * FH * 3
    count = len(r.stdout) // size
    return [np.frombuffer(r.stdout[i * size:(i + 1) * size], np.uint8).reshape(FH, FW, 3) for i in range(count)]


def detect(frames) -> list[list[tuple]]:
    """Faces per frame as (cx, cy, w, h, score), all as fractions of the frame."""
    import cv2
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (FW, FH), 0.6, 0.3, 50)
    out = []
    for f in frames:
        _, faces = det.detect(f)
        found = []
        for x, y, w, h, *rest in (faces if faces is not None else []):
            found.append(((x + w / 2) / FW, (y + h / 2) / FH, w / FW, h / FH, float(rest[-1])))
        out.append(found)
    return out


def choose(per_frame: list[list[tuple]]) -> Plan:
    n = max(len(per_frame), 1)
    clusters: list[list[tuple]] = []
    for faces in per_frame:
        for f in faces:
            for c in clusters:
                if abs(c[0][0] - f[0]) < 0.07 and abs(c[0][1] - f[1]) < 0.09:
                    c.append(f)
                    break
            else:
                clusters.append([f])
    best, best_score = None, 0.0
    for c in clusters:
        hits = len(c)
        if hits < max(2, 0.3 * n):
            continue   # faces that come and go are on-screen content, not the cam
        cx = sum(f[0] for f in c) / hits
        cy = sum(f[1] for f in c) / hits
        w = max(f[2] for f in c)
        h = max(f[3] for f in c)
        off_center = max(0.0, abs(cx - 0.5) - 0.22)
        dist = ((cx - 0.5) ** 2 + (cy - 0.5) ** 2) ** 0.5
        score = hits * (1 + 4 * off_center) * (1 + dist) * (sum(f[4] for f in c) / hits)
        if score > best_score:
            best, best_score = (cx, cy, w, h), score
    if best is None:
        return Plan("crop", note="no face cam found")
    cx, cy, w, h = best
    if w > 0.14:
        # a big face: the camera fills the screen (Just Chatting) - one crop that follows the face
        return Plan("crop", center_x=cx, note="full-screen camera")
    if abs(cx - 0.5) < 0.2:
        return Plan("crop", center_x=0.5, note="face is in the middle, already in the crop")
    return Plan("stack", cam=cam_box(cx, cy, w), note=f"face cam at {cx:.2f},{cy:.2f}")


def cam_box(cx: float, cy: float, w: float) -> tuple:
    """A box around the face, shaped like the phone's face cam panel, kept inside the frame."""
    bw = min(max(w * 3.0, 0.16), 0.5)
    bh = bw * 16 / 9 / CAM_ASPECT          # fractions: frame is 16:9
    if bh > 1:
        bh, bw = 1.0, CAM_ASPECT * 9 / 16
    x = min(max(cx - bw / 2, 0.0), 1 - bw)
    y = min(max(cy + 0.1 * bh - bh / 2, 0.0), 1 - bh)
    return (round(x, 4), round(y, 4), round(bw, 4), round(bh, 4))


def plan(ffmpeg: str, cwd: Path, src_args: list[str], duration: float, facecam: str) -> Plan:
    if facecam in CORNERS:
        return Plan("stack", cam=CORNERS[facecam], note=facecam)
    if facecam == "none":
        return Plan("crop", note="face cam turned off")
    if not available():
        return Plan("crop", note="face detection not installed")
    frames = sample_frames(ffmpeg, cwd, src_args, duration)
    if not frames:
        return Plan("crop", note="could not read frames")
    return choose(detect(frames))
