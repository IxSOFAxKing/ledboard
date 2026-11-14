"""Live state updates from Home Assistant over its WebSocket API (no extra packages needed).

HAStateStream connects to <ha url>/api/websocket, logs in with the long-lived token, and calls
on_state(entity_id, state) the moment one of the watched entities changes (state or attributes).
It reconnects by itself (2 s, 4 s, ... up to a minute) if Home Assistant restarts or the network drops.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlparse

log = logging.getLogger(__name__)


class WSClosed(ConnectionError):
    pass


class WSClient:
    """A minimal WebSocket client: text messages, ping / pong, close. Enough for Home Assistant."""

    def __init__(self, url, verify=True, timeout=15):
        u = urlparse(url)
        secure = u.scheme in ("wss", "https")
        host = u.hostname
        port = u.port or (443 if secure else 80)
        sock = socket.create_connection((host, port), timeout=timeout)
        if secure:
            ctx = ssl.create_default_context()
            if not verify:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise WSClosed("connection closed during the handshake")
            head += chunk
            if len(head) > 65536:
                raise WSClosed("bad handshake")
        head, self._buf = head.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101 " not in status + " ":
            raise WSClosed(f"Home Assistant refused the live connection ({status})")
        self.sock = sock
        self._send_lock = threading.Lock()

    def settimeout(self, t):
        self.sock.settimeout(t)

    def _frame(self, opcode, payload=b""):
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._send_lock:
            self.sock.sendall(head + mask + body)

    def send(self, obj):
        self._frame(0x1, json.dumps(obj).encode())

    def _exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise WSClosed("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self):
        """The next text message, decoded from JSON (answers pings on the way)."""
        parts = []
        while True:
            b0, b1 = self._exact(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._exact(8))[0]
            mask = self._exact(4) if b1 & 0x80 else None
            data = self._exact(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:
                raise WSClosed("Home Assistant closed the connection")
            if opcode == 0x9:
                self._frame(0xA, data)
                continue
            if opcode == 0xA:
                continue
            parts.append(data)
            if fin:
                return json.loads(b"".join(parts).decode())

    def close(self):
        try:
            self._frame(0x8)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def ws_url(base):
    """'http://ha:8123' -> 'ws://ha:8123/api/websocket'."""
    u = urlparse(base.rstrip("/"))
    scheme = "wss" if u.scheme == "https" else "ws"
    return f"{scheme}://{u.netloc}{u.path}/api/websocket"


class HAStateStream(threading.Thread):
    def __init__(self, base, token, verify, entities, on_state, keep_running=lambda: True, name="ha-live"):
        super().__init__(name=name, daemon=True)
        self.base, self.token, self.verify = base, token, verify
        self.entities = list(entities)
        self.on_state = on_state
        self.keep_running = keep_running
        self.connected = False
        self.error = None
        self._stop = threading.Event()
        self._ws = None

    def stop(self):
        self._stop.set()
        ws = self._ws
        if ws:
            ws.close()

    def run(self):
        delay = 2
        while not self._stop.is_set():
            if not self.keep_running():
                self._stop.wait(5)
                continue
            try:
                self._session()
                delay = 2
            except Exception as e:                     # network drop, HA restart, bad token...
                self.error = str(e) or type(e).__name__
                if not self._stop.is_set():
                    log.info("Home Assistant live updates: %s (retrying in %d s)", self.error, delay)
            finally:
                self.connected = False
                if self._ws:
                    self._ws.close()
                    self._ws = None
            self._stop.wait(delay)
            delay = min(60, delay * 2)

    def _session(self):
        ws = self._ws = WSClient(ws_url(self.base), self.verify)
        msg = ws.recv()
        if msg.get("type") == "auth_required":
            ws.send({"type": "auth", "access_token": self.token})
            msg = ws.recv()
        if msg.get("type") != "auth_ok":
            raise ConnectionError("Home Assistant rejected the token" if msg.get("type") == "auth_invalid"
                                  else f"unexpected reply {msg.get('type')!r}")
        # only the watched entities' changes (state or attributes); fall back to all state changes,
        # filtered here, if this login isn't allowed to subscribe to triggers
        ws.send({"id": 1, "type": "subscribe_trigger",
                 "trigger": {"platform": "state", "entity_id": self.entities}})
        mode = "trigger"
        self._events_id = None
        watched = set(self.entities)
        next_ping, msg_id = time.monotonic() + 30, 2
        ws.settimeout(10)
        while not self._stop.is_set() and self.keep_running():
            try:
                msg = ws.recv()
            except socket.timeout:
                msg = None
            if time.monotonic() >= next_ping:          # keep the connection (and any proxy) awake
                ws.send({"id": msg_id, "type": "ping"})
                msg_id += 1
                next_ping = time.monotonic() + 30
            if not msg:
                continue
            if msg.get("type") == "result" and msg.get("id") == 1:
                if msg.get("success"):
                    self.connected, self.error = True, None
                    log.info("Home Assistant live updates on for %s", ", ".join(self.entities))
                elif mode == "trigger":
                    mode = "events"
                    self._events_id = msg_id
                    ws.send({"id": msg_id, "type": "subscribe_events", "event_type": "state_changed"})
                    msg_id += 1
                else:
                    raise ConnectionError((msg.get("error") or {}).get("message") or "subscription refused")
                continue
            if msg.get("type") == "result" and self._events_id is not None and msg.get("id") == self._events_id:
                if not msg.get("success"):
                    raise ConnectionError((msg.get("error") or {}).get("message") or "subscription refused")
                self.connected, self.error = True, None
                log.info("Home Assistant live updates on (all state changes) for %s", ", ".join(self.entities))
                continue
            if msg.get("type") != "event":
                continue
            ev = msg.get("event") or {}
            if mode == "trigger":
                st = ((ev.get("variables") or {}).get("trigger") or {}).get("to_state")
            else:
                st = (ev.get("data") or {}).get("new_state")
            if isinstance(st, dict) and st.get("entity_id") in watched:
                try:
                    self.on_state(st["entity_id"], st)
                except Exception as e:                  # a bad state never kills the stream
                    log.warning("live update for %s failed: %s", st.get("entity_id"), e)
