"""End-to-end check without Twitch: a fake live stream (clock burned into the picture)
plus fake chat that bursts into laughter at a known time.

    python tests/e2e_sim.py [layout]

Prints the burst time and the clip made; look at the clip's first frame to see
that it starts ~pre_roll seconds before chat reacted.
"""
import json
import random
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ghostclip.captions import Word  # noqa: E402
from ghostclip.chat import ChatMessage  # noqa: E402
from ghostclip.engine import Engine  # noqa: E402
from ghostclip.settings import Settings  # noqa: E402
from ghostclip.utils import find_tool  # noqa: E402

BURST_AT = 45   # seconds after start


class SimChat(threading.Thread):
    burst_wall = None

    def __init__(self, channel, on_message, on_status):
        super().__init__(daemon=True)
        self.on_message, self.on_status = on_message, on_status
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        self.on_status("Reading chat (simulated)")
        rnd, t0 = random.Random(3), time.time()
        while not self._stop.wait(0.25):
            t = time.time() - t0
            burst = BURST_AT <= t < BURST_AT + 10
            if burst and SimChat.burst_wall is None:
                SimChat.burst_wall = time.time()
            for _ in range(rnd.randint(2, 6) if burst else int(rnd.random() < 0.5)):
                text = rnd.choice(["KEKW KEKW", "LMFAO", "😂😂😂", "OMEGALUL", "CLIP IT"]) if burst else \
                    rnd.choice(["hi", "what game", "gg", "true", "LUL"])
                self.on_message(ChatMessage(time.time(), f"u{rnd.randint(1, 999)}", text))


class FakeTranscriber:
    available = True

    def transcribe(self, wav):
        text = "no way he actually did that bro what are you doing chat clip it right now".split()
        return [Word(2 + i * 0.4, 2 + i * 0.4 + 0.35, w) for i, w in enumerate(text)]


def main():
    layout = sys.argv[1] if len(sys.argv) > 1 else "vertical_blur"
    ff = find_tool("ffmpeg")
    root = Path(tempfile.mkdtemp(prefix="ghostclip_e2e_"))
    s = Settings(channel="testchannel", clips_dir=str(root / "clips"), recordings_dir=str(root / "rec"),
                 pre_roll=20, post_roll=6, min_clip=20, cooldown=20, layout=layout)
    src = [ff, "-hide_banner", "-loglevel", "error", "-re",
           "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
           "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000",
           "-vf", "drawtext=text='%{localtime}':fontsize=64:fontcolor=white:box=1:boxcolor=black@0.6:x=40:y=40",
           "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-c:a", "aac", "-f", "mpegts", "pipe:1"]
    logs = []
    eng = Engine(s, ff, FakeTranscriber(), lambda m: (logs.append(m), print(time.strftime("%H:%M:%S"), m)),
                 source_cmd=src, chat_source=SimChat)
    eng.start()
    deadline = time.time() + BURST_AT + 90
    while time.time() < deadline and not eng.made:
        time.sleep(1)
    done = threading.Event()
    eng.stop(done.set)
    done.wait(120)
    clips = sorted((root / "clips").glob("*.mp4"))
    print("\nburst wall time:", time.strftime("%H:%M:%S", time.localtime(SimChat.burst_wall or 0)))
    print("clips:", [c.name for c in clips])
    for c in clips:
        print(json.dumps(json.loads(c.with_suffix(".json").read_text()), indent=1, ensure_ascii=False)[:600])
    left = [str(p.relative_to(root)) for p in (root / "rec").rglob("*")]
    print("left in recordings folder:", left or "nothing")
    print("ROOT", root)
    return 0 if clips else 1


if __name__ == "__main__":
    sys.exit(main())
