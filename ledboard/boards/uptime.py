"""Server status from an Uptime Kuma status page.

Uses Uptime Kuma's public status-page API (no login or API key): in Uptime Kuma, create a
status page (Status Pages -> New Status Page), add the monitors you want, and enter its
address here, e.g. http://192.168.1.10:3001/status/home.

Optionally raises a critical alert (kept on screen) when a monitor goes down and clears it
when the monitor is back up.
"""
from __future__ import annotations

import logging
import re

import requests

from .base import Board, organize
from ..gfx import GRAY, WHITE

log = logging.getLogger(__name__)

GREEN = (60, 210, 90)
RED = (240, 40, 40)
AMBER = (255, 170, 0)
BLUE = (80, 140, 255)
STATUS = {0: "down", 1: "up", 2: "pending", 3: "maintenance"}
PER_PAGE = 6


def parse_status_url(text):
    """('http://host:3001', 'slug') from a status page address (or a base URL + separate slug)."""
    t = (text or "").strip().rstrip("/")
    m = re.match(r"(https?://[^/]+)(?:/status/([^/?#]+))?", t)
    if not m:
        return None, None
    return m.group(1), m.group(2)


class UptimeBoard(Board):
    name = "uptime"
    title = "Server Status"
    description = "Your Uptime Kuma monitors: up/down dots and response times, with an optional alert when one goes down."
    refresh = 60
    DEFAULTS = {"enabled": False, "duration": 10, "refresh": 60, "status_url": "", "slug": "", "monitors": [],
                "only_when_down": False, "alert_on_down": True, "alert_hold": True}
    FIELDS = [
        {"key": "status_url", "label": "Uptime Kuma status page address", "type": "text",
         "placeholder": "http://192.168.1.10:3001/status/home",
         "help": "In Uptime Kuma: Status Pages → New Status Page, add your monitors, then paste the page's address here."},
        {"key": "monitors", "label": "Only these monitors (comma-separated, blank = all)", "type": "list"},
        {"key": "only_when_down", "label": "Only show when something is down", "type": "bool"},
        {"key": "alert_on_down", "label": "Critical alert when a monitor goes down (clears when it's back up)",
         "type": "bool"},
        {"key": "alert_hold", "label": "Keep the alert on screen until it clears or is dismissed", "type": "bool",
         "show_if": {"alert_on_down": True}},
        {"key": "duration", "label": "Seconds per page", "type": "number"},
        {"key": "refresh", "label": "Check every (seconds)", "type": "number"},
    ]
    FIELDS = organize(FIELDS, [('Status page', ['status_url', 'monitors']), ('When to show', ['only_when_down']), ('Alerts', ['alert_on_down', 'alert_hold']), ('Timing', ['duration', 'refresh'])], {})

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.monitors = []            # [{id, name, status, ping, msg}]
        self.uptime = None            # average 24 h uptime, 0..1
        self._alerted = set()
        self.session = requests.Session()

    def update(self):
        base, slug = parse_status_url(self.cfg.get("status_url"))
        slug = slug or (self.cfg.get("slug") or "").strip()
        if not (base and slug):
            self.monitors = []
            return
        page = self.session.get(f"{base}/api/status-page/{slug}", timeout=10)
        if page.status_code == 404:
            raise RuntimeError(f"Uptime Kuma has no status page '{slug}'")
        page.raise_for_status()
        beats = self.session.get(f"{base}/api/status-page/heartbeat/{slug}", timeout=10)
        beats.raise_for_status()
        pj, bj = page.json(), beats.json()
        wanted = {m.strip().lower() for m in self.cfg.get("monitors") or [] if m.strip()}
        hb = bj.get("heartbeatList") or {}
        ups = bj.get("uptimeList") or {}
        mons, uptimes = [], []
        for group in pj.get("publicGroupList") or []:
            for m in group.get("monitorList") or []:
                name = m.get("name") or f"#{m.get('id')}"
                if wanted and name.lower() not in wanted:
                    continue
                mid = str(m.get("id"))
                last = (hb.get(mid) or [{}])[-1]
                st = STATUS.get(last.get("status"), "pending" if not last else "down")
                mons.append({"id": mid, "name": name, "status": st, "ping": last.get("ping"), "msg": last.get("msg") or ""})
                u = ups.get(f"{mid}_24")
                if isinstance(u, (int, float)):
                    uptimes.append(u)
        self.monitors = mons
        self.uptime = sum(uptimes) / len(uptimes) if uptimes else None
        self._sync_alerts()

    def _sync_alerts(self):
        alerts = getattr(self.app, "alerts", None)
        if alerts is None:
            return
        on = self.cfg.get("alert_on_down", True)
        down_now = {m["id"] for m in self.monitors if m["status"] == "down"}
        for m in self.monitors:
            key = f"uptime:{m['id']}"
            if on and m["id"] in down_now and m["id"] not in self._alerted:
                alerts.add(f"{m['name']} DOWN", m["msg"] or "Not responding", icon="warning", color="#ff2020",
                           priority="critical", replace_key=key, hold=bool(self.cfg.get("alert_hold", True)),
                           source="uptime")
                self._alerted.add(m["id"])
            elif m["id"] not in down_now and m["id"] in self._alerted:
                alerts.dismiss(key=key)
                self._alerted.discard(m["id"])
        if not on:
            for mid in list(self._alerted):
                alerts.dismiss(key=f"uptime:{mid}")
            self._alerted.clear()

    # -- slides ---------------------------------------------------------------
    def _ordered(self):
        rank = {"down": 0, "pending": 1, "maintenance": 2, "up": 3}
        return sorted(self.monitors, key=lambda m: rank.get(m["status"], 1))

    def slides(self):
        if not self.monitors:
            return []
        if self.cfg.get("only_when_down") and not any(m["status"] == "down" for m in self.monitors):
            return []
        pages = (len(self.monitors) + PER_PAGE - 1) // PER_PAGE
        return [f"page:{i}" for i in range(pages)]

    def render(self, c, slide, t):
        W, H = c.width, c.height
        page = int(str(slide).split(":")[1]) if slide else 0
        mons = self._ordered()
        down = sum(1 for m in mons if m["status"] == "down")
        if down:
            c.text(1, 0, f"{down} DOWN", "5x7", RED)
        else:
            up = sum(1 for m in mons if m["status"] == "up")
            c.text(1, 0, f"{up}/{len(mons)} UP", "5x7", GREEN if up == len(mons) else AMBER)
        right = f"UPTIME {self.uptime * 100:.2f}%" if self.uptime is not None else ""
        pages = (len(mons) + PER_PAGE - 1) // PER_PAGE
        if pages > 1:
            right = (right + "  " if right else "") + f"{page + 1}/{pages}"
        c.text_right(W - 1, 0, right, "4x6", GRAY)
        blink = int(t * 2) % 2 == 0
        for i, m in enumerate(mons[page * PER_PAGE:(page + 1) * PER_PAGE]):
            col, row = i % 2, i // 2
            x, y = col * 64, 9 + row * 8
            st = m["status"]
            dot = {"up": GREEN, "down": RED if blink else (70, 10, 10), "pending": AMBER, "maintenance": BLUE}[st]
            c.draw.ellipse((x + 1, y, x + 5, y + 4), fill=dot)
            if st == "up":
                rt = f"{round(m['ping'])}ms" if isinstance(m.get("ping"), (int, float)) else "UP"
                rcol = GRAY
            else:
                rt = {"down": "DOWN", "pending": "WAIT", "maintenance": "MAINT"}[st]
                rcol = dot if st != "down" else RED
            rw = c.text_width(rt, "4x6")
            c.text(x + 8, y, m["name"].upper(), "4x6", WHITE if st != "down" else RED, clip=(x + 8, x + 62 - rw - 2))
            c.text_right(x + 62, y, rt, "4x6", rcol)

    def status(self):
        s = super().status()
        s["monitors"] = [{k: m[k] for k in ("name", "status", "ping")} for m in self.monitors]
        return s
