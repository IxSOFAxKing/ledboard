"""Plex Media Server "now playing" (movies, TV, music) via /status/sessions."""
from __future__ import annotations

import time
import urllib.parse

import requests
import urllib3

from .base import Board
from ..gfx import GRAY, WHITE, fetch_image, fmt_ms

PLEX_ORANGE = (229, 160, 13)


class PlexBoard(Board):
    name = "plex"
    title = "Plex"
    description = "Shows active Plex streams. Skipped when nothing is playing."
    refresh = 10
    DEFAULTS = {
        "enabled": False, "duration": 12, "refresh": 10, "takeover": False, "show_paused": True,
        "url": "http://192.168.1.10:32400", "token": "", "verify_ssl": False,
        "users": [], "show_user": True,
    }
    FIELDS = [
        {"key": "url", "label": "Server URL", "type": "text", "help": "e.g. http://192.168.1.10:32400"},
        {"key": "token", "label": "X-Plex-Token", "type": "password",
         "help": "Find it via 'Get Info' > 'View XML' on any item in Plex Web (the X-Plex-Token= part of the URL)."},
        {"key": "users", "label": "Only show these users (comma-separated, blank = everyone)", "type": "list"},
        {"key": "show_user", "label": "Show who is watching", "type": "bool"},
        {"key": "show_paused", "label": "Show paused streams", "type": "bool"},
        {"key": "takeover", "label": "Take over the display while something is playing", "type": "bool"},
        {"key": "verify_ssl", "label": "Verify HTTPS certificate", "type": "bool"},
        {"key": "duration", "label": "Seconds per stream", "type": "number"},
        {"key": "refresh", "label": "Poll interval (s)", "type": "number"},
    ]

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.sessions: list[dict] = []
        self.fetched_at = 0
        if not cfg.get("verify_ssl"):
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _base(self):
        return (self.cfg.get("url") or "").rstrip("/")

    def update(self):
        if not (self._base() and self.cfg.get("token")):
            self.sessions = []
            return
        r = requests.get(self._base() + "/status/sessions", timeout=10,
                         verify=bool(self.cfg.get("verify_ssl")),
                         headers={"X-Plex-Token": self.cfg["token"], "Accept": "application/json"})
        r.raise_for_status()
        sessions = self.parse(r.json())
        users = {u.strip().lower() for u in self.cfg.get("users", []) if u.strip()}
        if users:
            sessions = [s for s in sessions if s["user"].lower() in users]
        for s in sessions:
            s["art"] = self._art(s["thumb"]) if s["thumb"] else None
        self.sessions = sessions
        self.fetched_at = time.time()

    def _art(self, thumb):
        H = self.app.height
        q = urllib.parse.urlencode({"width": H * 2, "height": H * 2, "minSize": 1, "upscale": 1,
                                    "url": thumb, "X-Plex-Token": self.cfg["token"]})
        url = f"{self._base()}/photo/:/transcode?{q}"
        return fetch_image(url, H, H, verify=bool(self.cfg.get("verify_ssl")), cache_key="plex" + thumb)

    @staticmethod
    def parse(j):
        out = []
        for m in (j.get("MediaContainer") or {}).get("Metadata", []) or []:
            kind = m.get("type")
            if kind == "episode":
                title = m.get("grandparentTitle", "")
                s, e = m.get("parentIndex"), m.get("index")
                sub = (f"S{s}E{e} " if s is not None and e is not None else "") + m.get("title", "")
                thumb = m.get("grandparentThumb") or m.get("thumb")
            elif kind == "track":
                title = m.get("title", "")
                sub = m.get("grandparentTitle") or m.get("originalTitle") or ""
                thumb = m.get("parentThumb") or m.get("thumb")
            elif kind == "movie":
                title = m.get("title", "")
                sub = str(m.get("year", "")) if m.get("year") else ""
                thumb = m.get("thumb")
            else:
                title = m.get("title", "")
                sub = m.get("grandparentTitle", "")
                thumb = m.get("thumb")
            player = m.get("Player") or {}
            out.append({
                "key": str(m.get("sessionKey") or (m.get("Session") or {}).get("id") or m.get("ratingKey")),
                "type": kind, "title": title, "subtitle": sub, "thumb": thumb,
                "user": (m.get("User") or {}).get("title", ""),
                "device": player.get("title") or player.get("product") or "",
                "state": player.get("state", "playing"),
                "duration": int(m.get("duration") or 0), "offset": int(m.get("viewOffset") or 0),
            })
        return out

    def _visible(self):
        return [s for s in self.sessions if s["state"] == "playing" or self.cfg.get("show_paused")]

    def slides(self):
        return [s["key"] for s in self._visible()]

    def takeover_slides(self):
        if not self.cfg.get("takeover"):
            return []
        return [s["key"] for s in self._visible() if s["state"] == "playing"]

    def render(self, c, slide, t):
        s = next((x for x in self.sessions if x["key"] == slide), None)
        if not s:
            return
        H, W = c.height, c.width
        if s["art"] is not None:
            c.paste(s["art"], 0, 0)
            x0 = H + 2
        else:
            c.text(2, (H - 13) // 2, "PLEX", "5x8", PLEX_ORANGE)
            x0 = 26
        c.scroll_text(x0, W, 1, s["title"], "6x10", WHITE, t)
        c.scroll_text(x0, W, 11, s["subtitle"], "5x8", PLEX_ORANGE, t)
        offset = s["offset"]
        if s["state"] == "playing":
            offset += (time.time() - self.fetched_at) * 1000
        offset = min(offset, s["duration"] or offset)
        if s["state"] != "playing":
            label = "PAUSED"
        elif self.cfg.get("show_user", True) and s["user"]:
            label = s["user"]
        else:
            label = fmt_ms(offset)
        c.scroll_text(x0, W, H - 10, label, "4x6", GRAY, t)
        if s["duration"]:
            remaining = "-" + fmt_ms(s["duration"] - offset)
            rw = c.text_width(remaining, "4x6")
            if c.text_width(label, "4x6") + rw + 4 < W - x0:
                c.text_right(W - 1, H - 10, remaining, "4x6", GRAY)
            c.progress(x0, H - 3, W - 1, offset / s["duration"], PLEX_ORANGE)

    def status(self):
        s = super().status()
        s["sessions"] = [{k: v for k, v in x.items() if k not in ("art", "thumb")} for x in self.sessions]
        return s
