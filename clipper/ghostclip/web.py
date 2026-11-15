"""The local dashboard: a small web server on 127.0.0.1 that the browser talks to.
Runs up to MAX_STREAMS clipping sessions (one per channel) side by side."""
from __future__ import annotations

import importlib.util
import json
import mimetypes
import os
import socket
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import framing, games
from .analytics import Analytics, clean_handle
from .instagram import Instagram
from .captions import Transcriber
from .engine import Engine
from .settings import MAX_STREAMS, ChannelView, Settings, app_dir, clean_channel
from .utils import find_tool, open_path, run

HERE = Path(__file__).resolve().parent
CHUNK = 2 * 1024 * 1024


class App:
    def __init__(self, settings: Settings, source_cmd=None, chat_source=None):
        self.s = settings
        self.engines: dict[str, Engine] = {}
        self.logs: deque = deque(maxlen=400)
        self.log_file = app_dir() / "ghostclip.log"
        self._transcriber: Transcriber | None = None
        self.source_cmd = source_cmd
        self.chat_source = chat_source
        self.quit_event = threading.Event()
        self.lock = threading.RLock()
        self.instagram = Instagram(self.log)
        self.thumb_lock = threading.Lock()
        self._clips_cache = None
        self._checks_cache = None
        self.analytics = Analytics(self.s, self.instagram, self.log)

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
        """One speech model shared by every stream (it's big)."""
        if not self.s.captions:
            return None
        t = self._transcriber
        key = (self.s.whisper_model, self.s.whisper_device, self.s.language)
        if t is None or (t.model_name, t.device, t.language) != key:
            self._transcriber = Transcriber(*key, log=self.log)
        return self._transcriber

    def checks(self) -> dict:
        """What's installed. Looking takes ~20 ms (it searches the disk) and the dashboard asks
        every second, so the answer is kept for 15 seconds (or until the ffmpeg setting changes)."""
        cached = self._checks_cache
        if cached and cached[0] == self.s.ffmpeg_path and time.time() - cached[1] < 15:
            return cached[2]
        has = lambda m: importlib.util.find_spec(m) is not None  # noqa: E731
        out = {"ffmpeg": bool(self.ffmpeg()), "streamlink": has("streamlink"),
               "whisper": has("faster_whisper"), "facecam": framing.available()}
        self._checks_cache = (self.s.ffmpeg_path, time.time(), out)
        return out

    def running(self) -> list[Engine]:
        return [e for e in self.engines.values() if e.state != "stopped"]

    # -- actions ---------------------------------------------------------------
    def set_channels(self, channels: list[str]) -> None:
        with self.lock:
            self.s.update({"channels": channels})
            # keep channels that are still recording even if their box was cleared
            for ch, e in self.engines.items():
                if e.state != "stopped" and ch not in self.s.channels and len(self.s.channels) < MAX_STREAMS:
                    self.s.channels.append(ch)
            self.s.save()

    def start(self, channel: str) -> str | None:
        channel = clean_channel(channel)
        with self.lock:
            if not channel:
                return "Type a Twitch channel name first"
            e = self.engines.get(channel)
            if e and e.state != "stopped":
                return None
            if len(self.running()) >= MAX_STREAMS:
                return f"Already recording {MAX_STREAMS} streams"
            ff = self.ffmpeg()
            if not ff:
                return "ffmpeg was not found. Run setup_windows.bat again, or set its path in Settings."
            if not self.source_cmd and not self.checks()["streamlink"]:
                return "streamlink is not installed. Run setup_windows.bat again."
            for d in (self.s.clips_dir, self.s.recordings_dir):
                try:
                    Path(d).mkdir(parents=True, exist_ok=True)
                except OSError as ex:
                    return f"Can't use the folder {d}: {ex}"
            if channel not in self.s.channels:
                self.s.channels = (self.s.channels + [channel])[-MAX_STREAMS:]
            self.s.channel = channel
            self.s.save()
            view = ChannelView(self.s, channel)
            eng = Engine(view, ff, self.transcriber(), lambda m, c=channel: self.log(f"[{c}] {m}"),
                         source_cmd=self.source_cmd, chat_source=self.chat_source)
            self.engines[channel] = eng
            eng.start()
            return None

    def stop(self, channel: str) -> None:
        e = self.engines.get(clean_channel(channel))
        if e:
            e.stop()

    def stop_all(self, done=None) -> None:
        live = self.running()
        if not live:
            if done:
                done()
            return
        left = [len(live)]
        lock = threading.Lock()

        def one_done():
            with lock:
                left[0] -= 1
                if left[0] == 0 and done:
                    done()

        for e in live:
            if e.state == "running":
                e.stop(done=one_done)
            else:   # already stopping: wait for it in the background
                threading.Thread(target=lambda e=e: (self._wait(e), one_done()), daemon=True).start()

    @staticmethod
    def _wait(e: Engine) -> None:
        while e.state != "stopped":
            time.sleep(0.5)

    def thumbnail(self, clip_name: str) -> Path | None:
        """A small still of the clip (made once), so the dashboard doesn't load every video."""
        clip = Path(self.s.clips_dir) / clip_name
        if clip.suffix.lower() != ".mp4" or not clip.is_file():
            return None
        thumbs = Path(self.s.clips_dir) / ".thumbs"
        out = thumbs / (clip.stem + ".jpg")
        if out.is_file() and out.stat().st_mtime >= clip.stat().st_mtime:
            return out
        ff = self.ffmpeg()
        if not ff:
            return None
        with self.thumb_lock:
            if not out.is_file():
                thumbs.mkdir(exist_ok=True)
                run([ff, "-hide_banner", "-loglevel", "error", "-y", "-ss", "1", "-i", str(clip), "-frames:v", "1",
                     "-vf", "scale=360:-2", "-q:v", "5", str(out)], timeout=30)
        return out if out.is_file() else None

    def clips(self, limit: int = 40) -> list[dict]:
        d = Path(self.s.clips_dir)
        try:
            key = (str(d), d.stat().st_mtime_ns)
        except OSError:
            return []
        if self._clips_cache and self._clips_cache[0] == key and time.time() - self._clips_cache[2] < 5:
            return self._clips_cache[1]
        out = self._list_clips(d, limit)
        self._clips_cache = (key, out, time.time())
        return out

    def _list_clips(self, d: Path, limit: int) -> list[dict]:
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
            info["thumb"] = "/thumbs/" + f.name
            out.append(info)
        return out

    def stream_state(self, channel: str, now: float) -> dict:
        e = self.engines.get(channel)
        choice = self.s.games.get(channel, "auto")
        category = e.category if e else ""
        st = {"channel": channel, "state": e.state if e else "stopped", "status": e.status if e else "Not started",
              "game": choice, "game_name": games.resolve(choice, category)[0], "category": category,
              "title": e.title if e else ""}
        if e:
            d = e.detector
            feed = [[m[3], m[4][:200], m[1] > 0] for m in list(d.recent)[-14:]]
            st.update({
                "feed": feed, "recording": e.recorder.recording,
                "chat": e.chat_status, "rate": d.rate_per_min(now), "score": round(d.score, 2),
                "threshold": round(d.threshold, 2), "pending": e.pending, "clips": len(e.made),
                "uptime": int(now - e.started), "in_moment": d.event is not None,
                "history": [[round(t - now, 1), s, th] for t, s, th in list(d.history)[-120:]],
            })
        return st

    def state(self) -> dict:
        now = time.time()
        names = list(self.s.channels)
        for ch, e in self.engines.items():
            if e.state != "stopped" and ch not in names:
                names.append(ch)
        return {
            "streams": [self.stream_state(ch, now) for ch in names],
            "max_streams": MAX_STREAMS,
            "settings": self.s.to_dict(),
            "checks": self.checks(),
            "log": list(self.logs)[-100:],
            "clips": self.clips(),
            "windows": os.name == "nt",
            "instagram": self.instagram.state(),
            "analytics": self.analytics.summary(self.s.clips_dir),
            "games": games.choices(),
        }

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
        elif path.startswith("/thumbs/"):
            thumb = self.app.thumbnail(Path(unquote(path[8:])).name)
            if thumb:
                self._file(thumb, cache=True)
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def _file(self, f: Path, cache: bool = False):
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
            # Send videos in small pieces: the browser asks for the next piece when it needs it,
            # so a video never ties up one of the browser's few connections to the app.
            end = min(end, size - 1, start + CHUNK - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if cache:
            self.send_header("Cache-Control", "max-age=86400")
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
        if "settings" in data:
            self._apply(data["settings"])
        if "channels" in data:
            app.set_channels(data["channels"])
        ch = data.get("channel", "")
        if path == "/api/start":
            err = app.start(ch)
            self._json({"ok": err is None, "error": err})
        elif path == "/api/start-all":
            errs = [f"{c}: {e}" for c in app.s.channels if (e := app.start(c))]
            self._json({"ok": not errs, "error": "; ".join(errs) or None})
        elif path == "/api/stop":
            app.stop(ch)
            self._json({"ok": True})
        elif path == "/api/stop-all":
            app.stop_all()
            self._json({"ok": True})
        elif path == "/api/clip-now":
            e = app.engines.get(clean_channel(ch))
            if e and e.state == "running":
                e.clip_now()
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "Start recording first"})
        elif path in ("/api/settings", "/api/channels"):
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
        elif path in ("/api/add", "/api/remove"):
            c = clean_channel(ch)
            with app.lock:
                chans = [x for x in app.s.channels if x != c]
                if path == "/api/add" and c:
                    if len(chans) >= MAX_STREAMS:
                        return self._json({"ok": False, "error": f"You can have up to {MAX_STREAMS} streams"})
                    chans.append(c)
                app.set_channels(chans)
            self._json({"ok": True})
        elif path == "/api/game":
            c = clean_channel(ch)
            if c:
                app.s.games[c] = str(data.get("game") or "auto")
                app.s.save()
            self._json({"ok": True})
        elif path == "/api/analytics/refresh":
            err = app.analytics.refresh()
            self._json({"ok": err is None, "error": err})
        elif path == "/api/ig/open":
            app.instagram.open_window(data.get("url") or None)
            self._json({"ok": True})
        elif path == "/api/ig/post":
            clip = Path(app.s.clips_dir) / Path(data.get("file", "")).name
            if not clip.is_file():
                return self._json({"ok": False, "error": "Clip not found"})
            caption = data.get("caption") or ""
            if not caption:
                try:
                    caption = json.loads(clip.with_suffix(".json").read_text(encoding="utf-8")).get("post", "")
                except (OSError, ValueError):
                    caption = ""
            app.instagram.post(clip, caption)
            self._json({"ok": True})
        elif path == "/api/quit":
            self._json({"ok": True})
            app.log("Quitting: finishing clips first...")
            app.instagram.close()
            app.stop_all(done=app.quit_event.set)
        else:
            self.send_error(404)

    def _apply(self, data: dict) -> None:
        app = self.app
        data = dict(data)
        for secret in ("twitch_oauth", "youtube_api_key"):
            if data.get(secret) == "set":
                data.pop(secret)
        for k in ("ig_username", "tiktok_handle", "youtube_handle"):
            if k in data:
                data[k] = clean_handle(str(data[k]))
        data.pop("channels", None)
        app.s.update(data)
        app.s.save()
        for e in app.engines.values():
            e.detector.scorer.update_words(app.s.laugh_words, app.s.hype_words, app.s.clip_phrases)
            e.editor.transcriber = app.transcriber()


class _Server(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR (Python's default here) lets a second GhostClip bind the same
    # port, so the "already running, just open the dashboard" check never fires and the old
    # copy keeps answering. Take the port exclusively instead.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(app: App, port: int) -> ThreadingHTTPServer:
    Handler.app = app
    httpd = _Server(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="web").start()
    return httpd
