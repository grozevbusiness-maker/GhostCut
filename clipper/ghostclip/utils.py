from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW: no console pop-ups on Windows
APP_ROOT = Path(__file__).resolve().parent.parent


def _candidates(name: str, configured: str = ""):
    exe = name + (".exe" if os.name == "nt" else "")
    if configured:
        p = Path(configured.strip().strip('"'))
        yield p / exe if p.is_dir() else p.with_name(exe)
    yield APP_ROOT / "ffmpeg" / "bin" / exe
    found = shutil.which(name)
    if found:
        yield Path(found)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        yield Path(local) / "Microsoft" / "WinGet" / "Links" / exe
        for g in sorted(glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", exe))):
            yield Path(g)
    for base in (r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"):
        yield Path(base) / exe


def find_tool(name: str, configured: str = "") -> str | None:
    for c in _candidates(name, configured):
        if c and c.is_file():
            return str(c)
    return None


def streamlink_cmd() -> list[str]:
    """Run streamlink from the same Python environment the app runs in."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        exe = exe.with_name("python.exe")
    return [str(exe), "-m", "streamlink"]


def run(cmd, timeout=None, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=NO_WINDOW, **kw)


def open_path(path: str) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
