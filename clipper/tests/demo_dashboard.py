"""Run the dashboard against a fake stream and fake chat (no Twitch needed).

    python tests/demo_dashboard.py [port]
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e2e_sim import SimChat  # noqa: E402
from ghostclip.settings import Settings  # noqa: E402
from ghostclip.utils import find_tool  # noqa: E402
from ghostclip.web import App, serve  # noqa: E402

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
root = Path(tempfile.mkdtemp(prefix="ghostclip_demo_"))
s = Settings(clips_dir=str(root / "clips"), recordings_dir=str(root / "rec"),
             pre_roll=20, post_roll=6, min_clip=20, keep_full_recording=False)
ff = find_tool("ffmpeg")
src = [ff, "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
       "-f", "lavfi", "-i", "sine=frequency=330", "-c:v", "libx264", "-preset", "ultrafast", "-g", "60",
       "-c:a", "aac", "-f", "mpegts", "pipe:1"]
app = App(s, source_cmd=src, chat_source=SimChat)
app.s.save = lambda: None   # don't touch the real settings file
serve(app, port)
print(f"http://127.0.0.1:{port}/", flush=True)
while True:
    time.sleep(1)
