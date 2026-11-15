"""Start GhostClip.

    python -m ghostclip                  open the dashboard in the browser
    python -m ghostclip --channel a b c  also start recording these channels right away (up to 5)
    python -m ghostclip --check          check that ffmpeg, streamlink and captions are installed
"""
from __future__ import annotations

import argparse
import shlex
import signal
import sys
import time
import webbrowser

from .settings import Settings
from .web import App, serve


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ghostclip", description="Auto-clip Twitch streams from chat hype.")
    p.add_argument("--channel", nargs="*", default=[], help="start recording these channels immediately")
    p.add_argument("--clips", help="folder for finished clips")
    p.add_argument("--port", type=int, help="dashboard port (default 8765)")
    p.add_argument("--no-browser", action="store_true", help="don't open the dashboard")
    p.add_argument("--check", action="store_true", help="check dependencies and exit")
    p.add_argument("--source-cmd", help=argparse.SUPPRESS)   # testing: command that writes MPEG-TS to stdout
    args = p.parse_args(argv)

    s = Settings.load()
    if args.clips:
        s.clips_dir = args.clips
    if args.port:
        s.port = args.port
    app = App(s, source_cmd=shlex.split(args.source_cmd) if args.source_cmd else None)

    if args.check:
        c = app.checks()
        for k, ok in c.items():
            print(f"{k:11s} {'OK' if ok else 'MISSING'}")
        print(f"ffmpeg path: {app.ffmpeg()}")
        return 0 if c["ffmpeg"] and c["streamlink"] else 1

    port = s.port
    for attempt in range(10):
        try:
            serve(app, port)
            break
        except OSError:
            if attempt == 0:   # probably already running: just open it
                try:
                    import urllib.request
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2)
                    if not args.no_browser:
                        webbrowser.open(f"http://127.0.0.1:{port}/")
                    print("GhostClip is already running; opened the dashboard.")
                    return 0
                except Exception:
                    pass
            port += 1
    url = f"http://127.0.0.1:{port}/"
    app.log(f"Dashboard: {url}")
    c = app.checks()
    if not c["ffmpeg"]:
        app.log("ffmpeg is missing. Run setup_windows.bat (or install ffmpeg) before starting.")
    if not c["whisper"]:
        app.log("faster-whisper is missing, so clips will have no captions.")
    for ch in args.channel:
        err = app.start(ch)
        if err:
            app.log(f"{ch}: {err}")
    if not args.no_browser:
        webbrowser.open(url)

    def on_signal(*_):
        if app.running():
            app.log("Stopping (finishing clips first)...")
        app.stop_all(done=app.quit_event.set)

    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, on_signal)
    try:
        while not app.quit_event.wait(0.5):
            pass
    except KeyboardInterrupt:
        on_signal()
        app.quit_event.wait(600)
    time.sleep(0.3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
