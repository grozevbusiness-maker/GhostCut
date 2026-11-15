"""The local dashboard: a small web server on 127.0.0.1 that the browser talks to."""
from __future__ import annotations

import importlib.util
import json
import mimetypes
import os
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .captions import Transcriber
from .engine import Engine
from .settings import Settings, app_dir, clean_channel
from .utils import find_tool, open_path, run

HERE = Path(__file__).resolve().parent


class App:
    def __init__(self, settings: Settings, source_cmd=None, chat_source=None):
        self.s = settings
        self.engine: Engine | None = None
        self.logs: deque = deque(maxlen=300)
        self.log_file = app_dir() / "ghostclip.log"
        self._transcriber: Transcriber | None = None
        self.source_cmd = source_cmd
        self.chat_source = chat_source
        self.quit_event = threading.Event()
        self.lock = threading.Lock()

    # -- helpers ---------------------------------------------------------------
    def log(self, text: str) -> None:
        line = f"{datetime.now():%H:%M:%S}  {text}"
        self.logs.append(line)
        try:
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d} {line}\n")
        except OSError:
            pass
        print(line, flush=True)

    def ffmpeg(self) -> str | None:
        return find_tool("ffmpeg", self.s.ffmpeg_path)

    def transcriber(self) -> Transcriber | None:
        if not self.s.captions:
            return None
        t = self._transcriber
        key = (self.s.whisper_model, self.s.whisper_device, self.s.language)
        if t is None or (t.model_name, t.device, t.language) != key:
            self._transcriber = Transcriber(*key, log=self.log)
        return self._transcriber

    def checks(self) -> dict:
        has = lambda m: importlib.util.find_spec(m) is not None
        return {"ffmpeg": bool(self.ffmpeg()), "streamlink": has("streamlink"), "whisper": has("faster_whisper")}

    # -- actions ---------------------------------------------------------------
    def start(self, channel: str | None = None) -> str | None:
        with self.lock:
            if self.engine and self.engine.state != "stopped":
                return "Already running"
            if channel is not None:
                self.s.channel = clean_channel(channel)
            if not self.s.channel:
                return "Type the Twitch channel name first"
            ff = self.ffmpeg()
            if not ff:
                return "ffmpeg was not found. Run setup_windows.bat again, or set its path in Settings."
            if not self.source_cmd and not self.checks()["streamlink"]:
                return "streamlink is not installed. Run setup_windows.bat again."
            for d in (self.s.clips_dir, self.s.recordings_dir):
                try:
                    Path(d).mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    return f"Can't use the folder {d}: {e}"
            self.s.save()
            self.engine = Engine(self.s, ff, self.transcriber(), self.log,
                                 source_cmd=self.source_cmd, chat_source=self.chat_source)
            self.engine.start()
            return None

    def stop(self) -> None:
        if self.engine:
            self.engine.stop()

    def clips(self, limit: int = 30) -> list[dict]:
        d = Path(self.s.clips_dir)
        if not d.is_dir():
            return []
        files = sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        out = []
        for f in files:
            info = {"file": f.name}
            try:
                info.update(json.loads(f.with_suffix(".json").read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
            info["url"] = "/clips/" + f.name
            info["mtime"] = f.stat().st_mtime
            info["size_mb"] = round(f.stat().st_size / 1e6, 1)
            out.append(info)
        return out

    def state(self) -> dict:
        e = self.engine
        now = time.time()
        st = {
            "state": e.state if e else "stopped",
            "status": e.status if e else "Ready",
            "chat": e.chat_status if e else "",
            "channel": self.s.channel,
            "settings": self.s.to_dict(),
            "checks": self.checks(),
            "log": list(self.logs)[-80:],
            "clips": self.clips(),
            "windows": os.name == "nt",
        }
        if e:
            d = e.detector
            st.update({
                "rate": d.rate_per_min(now), "score": round(d.score, 2), "threshold": round(d.threshold, 2),
                "history": [[round(t - now, 1), s, th] for t, s, th in d.history],
                "pending": e.pending, "session_clips": len(e.made),
                "uptime": int(now - e.started), "in_moment": d.event is not None,
            })
        return st

    def pick_folder(self, current: str) -> str | None:
        if os.name != "nt":
            return None
        cur = current.replace("'", "''")
        ps = ("Add-Type -AssemblyName System.Windows.Forms;"
              "$o=New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
              "$f=New-Object System.Windows.Forms.FolderBrowserDialog;"
              f"$f.SelectedPath='{cur}';$f.ShowNewFolderButton=$true;"
              "if($f.ShowDialog($o) -eq 'OK'){[Console]::Out.Write($f.SelectedPath)}")
        r = run(["powershell", "-NoProfile", "-STA", "-Command", ps], timeout=600)
        return r.stdout.strip() or None


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by serve()

    def log_message(self, *a):  # keep the console quiet
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self.send_error(403)
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = (HERE / "dashboard.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            self._json(self.app.state())
        elif path.startswith("/clips/"):
            self._file(Path(self.app.s.clips_dir) / Path(unquote(path[7:])).name)
        else:
            self.send_error(404)

    def _file(self, f: Path):
        if not f.is_file():
            return self.send_error(404)
        size = f.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            try:
                start = int(a) if a else max(size - int(b), 0)
                end = int(b) if a and b else end
            except ValueError:
                pass
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        try:
            with f.open("rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if not self._host_ok() or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self.send_error(403)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            data = {}
        app, path = self.app, urlparse(self.path).path
        if path == "/api/start":
            if "settings" in data:
                self._apply(data["settings"])
            err = app.start(data.get("channel"))
            self._json({"ok": err is None, "error": err})
        elif path == "/api/stop":
            app.stop()
            self._json({"ok": True})
        elif path == "/api/clip-now":
            if app.engine and app.engine.state == "running":
                app.engine.clip_now()
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "Start recording first"})
        elif path == "/api/settings":
            self._apply(data)
            self._json({"ok": True, "settings": app.s.to_dict()})
        elif path == "/api/pick-folder":
            chosen = app.pick_folder(data.get("current", ""))
            self._json({"ok": bool(chosen), "path": chosen})
        elif path == "/api/open":
            target = data.get("target")
            p = {"clips": app.s.clips_dir, "recordings": app.s.recordings_dir}.get(target)
            if target == "clip":
                p = str(Path(app.s.clips_dir) / Path(data.get("file", "")).name)
            if p and Path(p).exists():
                open_path(p)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "Not found yet"})
        elif path == "/api/quit":
            self._json({"ok": True})
            if app.engine and app.engine.state == "running":
                app.engine.stop(done=app.quit_event.set)
            else:
                app.quit_event.set()
        else:
            self.send_error(404)

    def _apply(self, data: dict) -> None:
        app = self.app
        data = dict(data)
        if data.get("twitch_oauth") == "set":
            data.pop("twitch_oauth")
        app.s.update(data)
        app.s.save()
        if app.engine:
            app.engine.detector.scorer.update_words(app.s.laugh_words, app.s.hype_words, app.s.clip_phrases)
            if app.engine.editor:
                app.engine.editor.transcriber = app.transcriber()


def serve(app: App, port: int) -> ThreadingHTTPServer:
    Handler.app = app
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="web").start()
    return httpd
