"""Plex Media Server "now playing" (movies, TV, music) via /status/sessions, plus a carousel of
recently added movies / episodes (/library/recentlyAdded)."""
from __future__ import annotations

import time
import urllib.parse

import logging

import requests
import urllib3

from .base import Board, organize
from ..gfx import DIM, GRAY, WHITE, Ease, fetch_image, fill_height, fmt_ms

log = logging.getLogger(__name__)
PLEX_ORANGE = (229, 160, 13)


class PlexBoard(Board):
    name = "plex"
    title = "Plex"
    category = "Media"
    description = "Shows active Plex streams, and what was recently added to your server."
    refresh = 10
    DEFAULTS = {
        "enabled": False, "duration": 12, "refresh": 10, "takeover": False, "show_paused": True,
        "url": "http://192.168.1.10:32400", "token": "", "verify_ssl": False,
        "users": [], "show_user": True, "progress_style": "wavy",
        "show_recent": True, "recent_hours": 24, "recent_max": 6, "recent_music": False,
    }
    FIELDS = [
        {"key": "url", "label": "Server URL", "type": "text", "help": "e.g. http://192.168.1.10:32400"},
        {"key": "token", "label": "X-Plex-Token", "type": "password",
         "help": "Find it via 'Get Info' > 'View XML' on any item in Plex Web (the X-Plex-Token= part of the URL)."},
        {"key": "users", "label": "Only show these users (comma-separated, blank = everyone)", "type": "list"},
        {"key": "show_user", "label": "Show who is watching", "type": "bool"},
        {"key": "show_paused", "label": "Show paused streams", "type": "bool"},
        {"key": "takeover", "label": "Take over the display while something is playing", "type": "bool"},
        {"key": "progress_style", "label": "Progress bar style", "type": "select",
         "options": ["wavy", "straight"],
         "option_labels": {"wavy": "Wavy (animated, Material 3 style)", "straight": "Straight"}},
        {"key": "verify_ssl", "label": "Verify HTTPS certificate", "type": "bool"},
        {"key": "duration", "label": "Seconds per stream", "type": "number"},
        {"key": "refresh", "label": "Poll interval (s)", "type": "number"},
        {"key": "show_recent", "label": "Show recently added movies and episodes", "type": "bool",
         "section": "Recently added"},
        {"key": "recent_hours", "label": "Added in the last (hours)", "type": "number", "section": "Recently added",
         "show_if": {"show_recent": True}},
        {"key": "recent_max", "label": "Show at most", "type": "number", "section": "Recently added",
         "show_if": {"show_recent": True}},
        {"key": "recent_music", "label": "Include new music albums", "type": "bool", "section": "Recently added",
         "show_if": {"show_recent": True}},
    ]
    FIELDS = organize(FIELDS, [
        ("Basics", "Connection", ["url", "token", "verify_ssl"]),
        ("Screens", "Now playing", ["takeover", "users", "show_user", "show_paused", "progress_style"]),
        ("Screens", "Recently added", ["show_recent", "recent_hours", "recent_max", "recent_music"]),
        ("Advanced", "Timing", ["duration", "refresh"])], {"duration": "Seconds per screen"})

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.sessions: list[dict] = []
        self._wave = Ease()
        self.fetched_at = 0
        self.recent: list[dict] = []
        self._recent_at = 0.0
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
        if self.cfg.get("show_recent", True):
            sig = (self.cfg.get("recent_hours"), self.cfg.get("recent_max"), self.cfg.get("recent_music"))
            if time.time() - self._recent_at > 600 or sig != getattr(self, "_recent_sig", None):   # every 10 min
                self._recent_sig = sig
                try:
                    self._update_recent()
                except Exception as e:
                    log.warning("Plex recently added unavailable: %s", e)
                self._recent_at = time.time()
        else:
            self.recent = []

    def _update_recent(self):
        r = requests.get(self._base() + "/library/recentlyAdded", timeout=15, verify=bool(self.cfg.get("verify_ssl")),
                         params={"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 40},
                         headers={"X-Plex-Token": self.cfg["token"], "Accept": "application/json"})
        r.raise_for_status()
        try:
            hours = float(self.cfg.get("recent_hours") or 24)
        except (TypeError, ValueError):
            hours = 24
        try:
            limit = max(1, int(self.cfg.get("recent_max") or 6))
        except (TypeError, ValueError):
            limit = 6
        items = self.parse_recent(r.json(), time.time() - hours * 3600, bool(self.cfg.get("recent_music")))[:limit]
        H = self.app.height
        for it in items:
            it["art"] = self._poster(it["thumb"], H) if it["thumb"] else None
        self.recent = items

    def _poster(self, thumb, h):
        """Poster scaled to the full height (2:3 posters come out ~21 px wide; wider art is cropped square)."""
        q = urllib.parse.urlencode({"width": h * 4, "height": h * 4, "minSize": 1, "upscale": 1,
                                    "url": thumb, "X-Plex-Token": self.cfg["token"]})
        img = fetch_image(f"{self._base()}/photo/:/transcode?{q}", h * 4, h * 4,
                          verify=bool(self.cfg.get("verify_ssl")), cache_key="plex4" + thumb)
        return fill_height(img, h, h) if img is not None else None

    @staticmethod
    def parse_recent(j, since, music=False):
        """Recently added items newer than `since` (epoch), newest first."""
        out = []
        for m in (j.get("MediaContainer") or {}).get("Metadata", []) or []:
            added = int(m.get("addedAt") or 0)
            if added < since:
                continue
            kind = m.get("type")
            if kind == "movie":
                bits = [str(m["year"])] if m.get("year") else []
                if m.get("duration"):
                    mins = int(m["duration"]) // 60000
                    bits.append(f"{mins // 60}H {mins % 60:02d}M" if mins >= 60 else f"{mins}M")
                if m.get("contentRating"):
                    bits.append(m["contentRating"])
                item = {"tag": "NEW MOVIE", "title": m.get("title", ""), "sub": " · ".join(bits), "thumb": m.get("thumb")}
            elif kind == "episode":
                s, e = m.get("parentIndex"), m.get("index")
                se = f"S{s} E{e}" if s is not None and e is not None else ""
                item = {"tag": "NEW EPISODE", "title": m.get("grandparentTitle") or m.get("title", ""),
                        "sub": " · ".join(x for x in (se, m.get("title", "")) if x),
                        "thumb": m.get("grandparentThumb") or m.get("parentThumb") or m.get("thumb")}
            elif kind == "season":
                n = m.get("leafCount")
                item = {"tag": "NEW EPISODES", "title": m.get("parentTitle") or m.get("title", ""),
                        "sub": f"SEASON {m.get('index')}" + (f" · {n} EPISODES" if n else "") if m.get("index") is not None
                        else m.get("title", ""),
                        "thumb": m.get("parentThumb") or m.get("thumb")}
            elif kind == "show":
                item = {"tag": "NEW SHOW", "title": m.get("title", ""), "sub": str(m.get("year", "") or ""),
                        "thumb": m.get("thumb")}
            elif kind == "album" and music:
                item = {"tag": "NEW ALBUM", "title": m.get("title", ""), "sub": m.get("parentTitle", ""),
                        "thumb": m.get("thumb")}
            else:
                continue
            item.update(key="new:" + str(m.get("ratingKey") or len(out)), added=added)
            out.append(item)
        out.sort(key=lambda i: -i["added"])
        return out

    def _art(self, thumb):
        H = self.app.height
        q = urllib.parse.urlencode({"width": H * 4, "height": H * 4, "minSize": 1, "upscale": 1,
                                    "url": thumb, "X-Plex-Token": self.cfg["token"]})
        url = f"{self._base()}/photo/:/transcode?{q}"
        img = fetch_image(url, H * 4, H * 4, verify=bool(self.cfg.get("verify_ssl")), cache_key="plex4" + thumb)
        return fill_height(img, H, H) if img is not None else None     # always the full panel height

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
        out = [s["key"] for s in self._visible()]
        if self.cfg.get("show_recent", True):
            out += [r["key"] for r in self.recent]
        return out

    def takeover_slides(self):
        if not self.cfg.get("takeover"):
            return []
        return [s["key"] for s in self._visible() if s["state"] == "playing"]

    def _render_recent(self, c, item, t):
        H, W = c.height, c.width
        x0 = 2
        if item.get("art") is not None:
            c.paste(item["art"], 0, 0)
            x0 = item["art"].width + 3
        c.text(x0, 0, item["tag"], "4x6", PLEX_ORANGE)
        c.scroll_text(x0, W, 7, item["title"], "6x10", WHITE, t)
        c.scroll_text(x0, W, 18, item["sub"].upper(), "4x6", GRAY, t)
        ago = max(0, time.time() - item["added"])
        when = "JUST ADDED" if ago < 3600 else (f"ADDED {int(ago // 3600)}H AGO" if ago < 86400 else
                                                 f"ADDED {int(ago // 86400)}D AGO")
        c.text(x0, H - 6, when, "4x6", DIM)
        n = len(self.recent)
        if n > 1:                                             # page dots
            i = next((k for k, r in enumerate(self.recent) if r is item), 0)
            for k in range(n):
                c.pixel(W - 1 - (n - 1 - k) * 2, H - 1, WHITE if k == i else DIM)

    def render(self, c, slide, t):
        if isinstance(slide, str) and slide.startswith("new:"):
            item = next((r for r in self.recent if r["key"] == slide), None)
            if item:
                self._render_recent(c, item, t)
            return
        s = next((x for x in self.sessions if x["key"] == slide), None)
        if not s:
            return
        H, W = c.height, c.width
        if s["art"] is not None:
            c.paste(s["art"], 0, 0)
            x0 = s["art"].width + 2
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
        wavy = self.cfg.get("progress_style", "wavy") == "wavy"
        ty = H - 12 if wavy else H - 10          # the wave needs a few more rows
        c.scroll_text(x0, W, ty, label, "4x6", GRAY, t)
        if s["duration"]:
            remaining = "-" + fmt_ms(s["duration"] - offset)
            rw = c.text_width(remaining, "4x6")
            if c.text_width(label, "4x6") + rw + 4 < W - x0:
                c.text_right(W - 1, ty, remaining, "4x6", GRAY)
            frac = offset / s["duration"]
            if wavy:
                c.wavy_progress(x0, W - 1, H - 4, frac, PLEX_ORANGE, amp=self._wave.value(slide, s["state"] == "playing"))
            else:
                c.progress(x0, H - 3, W - 1, frac, PLEX_ORANGE)

    def status(self):
        s = super().status()
        s["sessions"] = [{k: v for k, v in x.items() if k not in ("art", "thumb")} for x in self.sessions]
        s["recent"] = [{k: v for k, v in x.items() if k not in ("art", "thumb")} for x in self.recent]
        return s
