"""Read Twitch chat anonymously over IRC (no login or token needed)."""
from __future__ import annotations

import random
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Callable

HOST, PORT = "irc.chat.twitch.tv", 6697


@dataclass
class ChatMessage:
    ts: float          # local wall-clock time the message arrived
    user: str
    text: str
    emotes: int = 0    # number of native Twitch emotes in the message


def parse_privmsg(line: str) -> tuple[str, str, int] | None:
    """Parse one IRC line. Returns (user, text, emote_count) for chat messages."""
    tags = {}
    if line.startswith("@"):
        raw, _, line = line.partition(" ")
        for kv in raw[1:].split(";"):
            k, _, v = kv.partition("=")
            tags[k] = v
    if " PRIVMSG #" not in line:
        return None
    prefix, _, rest = line.partition(" PRIVMSG #")
    _, _, text = rest.partition(" :")
    user = tags.get("display-name") or prefix.lstrip(":").split("!")[0]
    emotes = 0
    if tags.get("emotes"):
        # format: id:0-4,6-10/id2:12-16
        emotes = sum(len(e.split(":", 1)[1].split(",")) for e in tags["emotes"].split("/") if ":" in e)
    if text.startswith("\x01ACTION "):
        text = text[8:].rstrip("\x01")
    return user, text, emotes


class TwitchChat(threading.Thread):
    def __init__(self, channel: str, on_message: Callable[[ChatMessage], None],
                 on_status: Callable[[str], None] = lambda s: None):
        super().__init__(daemon=True, name="chat")
        self.channel = channel.lower()
        self.on_message = on_message
        self.on_status = on_status
        self._halt = threading.Event()   # not "_stop": that name belongs to threading.Thread
        self.connected = False

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        backoff = 2
        while not self._halt.is_set():
            try:
                self._session()
                backoff = 2
            except (OSError, ssl.SSLError) as e:
                self.connected = False
                self.on_status(f"Chat disconnected ({e.__class__.__name__}), reconnecting in {backoff}s")
            if self._halt.wait(backoff):
                break
            backoff = min(backoff * 2, 60)

    def _session(self) -> None:
        ctx = ssl.create_default_context()
        with socket.create_connection((HOST, PORT), timeout=15) as raw:
            with ctx.wrap_socket(raw, server_hostname=HOST) as sock:
                sock.settimeout(1.0)
                nick = f"justinfan{random.randint(10000, 99999)}"
                sock.sendall(
                    f"CAP REQ :twitch.tv/tags twitch.tv/commands\r\nPASS SCHMOOPIIE\r\nNICK {nick}\r\nJOIN #{self.channel}\r\n".encode()
                )
                buf = b""
                last_rx = time.time()
                while not self._halt.is_set():
                    try:
                        data = sock.recv(65536)
                    except socket.timeout:
                        if time.time() - last_rx > 360:
                            raise OSError("chat timed out")
                        continue
                    if not data:
                        raise OSError("connection closed")
                    last_rx = time.time()
                    buf += data
                    *lines, buf = buf.split(b"\r\n")
                    for raw_line in lines:
                        line = raw_line.decode("utf-8", "replace")
                        if line.startswith("PING"):
                            sock.sendall(("PONG" + line[4:] + "\r\n").encode())
                        elif " RECONNECT" in line:
                            raise OSError("server asked to reconnect")
                        elif " JOIN #" in line and not self.connected:
                            self.connected = True
                            self.on_status(f"Reading chat in #{self.channel}")
                        else:
                            parsed = parse_privmsg(line)
                            if parsed:
                                user, text, emotes = parsed
                                self.on_message(ChatMessage(last_rx, user, text, emotes))
