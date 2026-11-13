"""Server status from an Uptime Kuma status page.

Uses Uptime Kuma's public status-page API (no login or API key): in Uptime Kuma, create a
status page (Status Pages -> New Status Page), add the monitors you want, and enter its
address here, e.g. http://192.168.1.10:3001/status/home.

Optionally raises a critical alert (kept on screen) when a monitor goes down and clears it
when the monitor is back up.
"""
from __future__ import annotations

import logging
import math
import re
import time

import requests

from .base import Board, organize
from ..gfx import DIM, GRAY, WHITE, scale

log = logging.getLogger(__name__)

GREEN = (60, 210, 90)
RED = (240, 40, 40)
AMBER = (255, 170, 0)
BLUE = (80, 140, 255)
STATUS = {0: "down", 1: "up", 2: "pending", 3: "maintenance"}
PER_PAGE = 6
LAYOUTS = {
    "grid": "Grid (6 per page: dot, name, response time)",
    "tiles": "Status wall (8 colored tiles per page)",
    "heartbeat": "Heartbeat bars (recent checks per monitor, like Uptime Kuma)",
    "focus": "One at a time (big status, uptime and a response-time graph)",
    "summary": "Summary (ALL SYSTEMS UP, or what's down)",
}
PAGE_SIZE = {"grid": 6, "tiles": 8, "heartbeat": 3, "focus": 1}
COLORS = {"up": GREEN, "down": RED, "pending": AMBER, "maintenance": BLUE}


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
    category = "Home & status"
    description = "Your Uptime Kuma monitors: up/down dots and response times, with an optional alert when one goes down."
    refresh = 60
    DEFAULTS = {"enabled": False, "duration": 10, "refresh": 60, "status_url": "", "slug": "", "monitors": [], "alert_monitors": [],
                "only_when_down": False, "alert_on_down": True, "alert_hold": True, "layout": "grid"}
    FIELDS = [
        {"key": "status_url", "label": "Uptime Kuma status page address", "type": "text",
         "placeholder": "http://192.168.1.10:3001/status/home",
         "help": "In Uptime Kuma: Status Pages → New Status Page, add your monitors, then paste the page's address here."},
        {"key": "monitors", "label": "Show these monitors on the board (comma-separated, blank = all)", "type": "list",
         "help": "Names as they appear in Uptime Kuma (the Status tab lists them). Only these appear in the rotation."},
        {"key": "layout", "label": "Design", "type": "select", "options": list(LAYOUTS), "option_labels": LAYOUTS},
        {"key": "only_when_down", "label": "Only show when something is down", "type": "bool"},
        {"key": "alert_on_down", "label": "Critical alert when a monitor goes down (clears when it's back up)",
         "type": "bool"},
        {"key": "alert_monitors", "label": "Critical alerts for these monitors (comma-separated, blank = all)",
         "type": "list", "show_if": {"alert_on_down": True},
         "help": "Separate from the list above: a monitor can raise an alert without being shown on the board, "
                 "or be shown without raising alerts."},
        {"key": "alert_hold", "label": "Keep the alert on screen until it clears or is dismissed", "type": "bool",
         "show_if": {"alert_on_down": True}},
        {"key": "duration", "label": "Seconds per page", "type": "number"},
        {"key": "refresh", "label": "Check every (seconds)", "type": "number"},
    ]
    FIELDS = organize(FIELDS, [("Basics", "Status page", ["status_url", "monitors"]),
                               ("Screens", "Display", ["layout", "only_when_down"]),
                               ("Screens", "Alerts", ["alert_on_down", "alert_monitors", "alert_hold"]),
                               ("Advanced", "Timing", ["duration", "refresh"])], {})

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.monitors = []            # the ones shown on the board: [{id, name, status, ping, msg}]
        self.all_monitors = []        # every monitor on the status page (alerts can watch any of them)
        self.uptime = None            # average 24 h uptime, 0..1
        self._alerted = set()
        self.session = requests.Session()

    def update(self):
        base, slug = parse_status_url(self.cfg.get("status_url"))
        slug = slug or (self.cfg.get("slug") or "").strip()
        if not (base and slug):
            self.monitors = self.all_monitors = []
            return
        page = self.session.get(f"{base}/api/status-page/{slug}", timeout=10)
        if page.status_code == 404:
            raise RuntimeError(f"Uptime Kuma has no status page '{slug}'")
        page.raise_for_status()
        beats = self.session.get(f"{base}/api/status-page/heartbeat/{slug}", timeout=10)
        beats.raise_for_status()
        pj, bj = page.json(), beats.json()
        wanted = self._names("monitors")
        hb = bj.get("heartbeatList") or {}
        ups = bj.get("uptimeList") or {}
        mons, uptimes = [], []
        for group in pj.get("publicGroupList") or []:
            for m in group.get("monitorList") or []:
                name = m.get("name") or f"#{m.get('id')}"
                mid = str(m.get("id"))
                beats_list = hb.get(mid) or []
                last = (beats_list or [{}])[-1]
                st = STATUS.get(last.get("status"), "pending" if not last else "down")
                history = [(STATUS.get(b.get("status"), "pending"), b.get("ping")) for b in beats_list[-60:]]
                u = ups.get(f"{mid}_24")
                mons.append({"id": mid, "name": name, "status": st, "ping": last.get("ping"), "msg": last.get("msg") or "",
                             "history": history, "uptime": u if isinstance(u, (int, float)) else None})
                if isinstance(u, (int, float)) and (not wanted or name.lower() in wanted):
                    uptimes.append(u)
        self.all_monitors = mons
        self.monitors = [m for m in mons if not wanted or m["name"].lower() in wanted]
        self.uptime = sum(uptimes) / len(uptimes) if uptimes else None
        self._sync_alerts()

    def _names(self, key):
        return {m.strip().lower() for m in self.cfg.get(key) or [] if isinstance(m, str) and m.strip()}

    def _sync_alerts(self):
        alerts = getattr(self.app, "alerts", None)
        if alerts is None:
            return
        on = self.cfg.get("alert_on_down", True)
        watch = self._names("alert_monitors")
        watched = [m for m in self.all_monitors if not watch or m["name"].lower() in watch]
        down_now = {m["id"] for m in watched if m["status"] == "down"}
        for mid in list(self._alerted):                  # taken off the alert list: clear its alert
            if mid not in {m["id"] for m in watched}:
                alerts.dismiss(key=f"uptime:{mid}")
                self._alerted.discard(mid)
        for m in watched:
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

    def _layout(self):
        lay = self.cfg.get("layout", "grid")
        return lay if lay in LAYOUTS else "grid"

    def slides(self):
        if not self.monitors:
            return []
        if self.cfg.get("only_when_down") and not any(m["status"] == "down" for m in self.monitors):
            return []
        lay = self._layout()
        if lay == "summary":
            return ["page:0"]
        n = len(self.monitors)
        if lay == "focus" and self.cfg.get("only_when_down"):
            n = sum(1 for m in self.monitors if m["status"] == "down")      # only the broken ones
        size = PAGE_SIZE[lay]
        return [f"page:{i}" for i in range((n + size - 1) // size)]

    def render(self, c, slide, t):
        lay = self._layout()
        page = int(str(slide).split(":")[1]) if slide else 0
        if lay != "grid":
            {"tiles": self._render_tiles, "heartbeat": self._render_heartbeat, "focus": self._render_focus,
             "summary": self._render_summary}[lay](c, page, t)
            return
        self._render_grid(c, slide, t)

    # -- designs ------------------------------------------------------------------
    @staticmethod
    def _short(name, n):
        name = name.upper()
        return name if len(name) <= n else name[:n]

    def _page(self, page, size, only_down=False):
        mons = self._ordered()
        if only_down:
            mons = [m for m in mons if m["status"] == "down"]
        return mons[page * size:(page + 1) * size], max(1, math.ceil(len(mons) / size))

    def _render_tiles(self, c, page, t):
        W, H = c.width, c.height
        mons, _pages = self._page(page, 8)
        tw, th = W // 4, H // 2
        blink = int(t * 2) % 2 == 0
        for i, m in enumerate(mons):
            x, y = (i % 4) * tw, (i // 4) * th
            col = COLORS.get(m["status"], AMBER)
            down = m["status"] == "down"
            bg = scale(col, 0.85 if (down and blink) else (0.35 if down else 0.22))
            c.draw.rounded_rectangle((x + 1, y + 1, x + tw - 2, y + th - 2), radius=2, fill=bg)
            c.text_centered(y + 2, self._short(m["name"], 7), "4x6", WHITE, x + 1, x + tw - 1)
            if m["status"] == "up":
                sub = f"{round(m['ping'])}MS" if isinstance(m.get("ping"), (int, float)) else "UP"
            else:
                sub = {"down": "DOWN", "pending": "WAIT", "maintenance": "MAINT"}.get(m["status"], "?")
            c.text_centered(y + 8, sub, "4x6", scale(WHITE, 0.75) if not down else WHITE, x + 1, x + tw - 1)

    def _render_heartbeat(self, c, page, t):
        W, H = c.width, c.height
        mons, pages = self._page(page, 3)
        bars_x0, bar_w = 44, 2
        n = (W - bars_x0 - 1) // (bar_w + 1)
        for i, m in enumerate(mons):
            y = 1 + i * 11
            col = COLORS.get(m["status"], AMBER)
            c.draw.ellipse((0, y + 1, 3, y + 4), fill=col)
            c.text(6, y, self._short(m["name"], 9), "4x6", WHITE if m["status"] != "down" else RED)
            up = m.get("uptime")
            c.text(6, y + 6, f"{up * 100:.1f}%" if up is not None else "", "4x6", scale(GRAY, 0.8))
            hist = (m.get("history") or [])[-n:]
            pad = n - len(hist)
            for k in range(n):
                bx = bars_x0 + k * (bar_w + 1)
                if k < pad:
                    c.rect(bx, y + 1, bx + bar_w - 1, y + 8, (30, 30, 34))
                    continue
                st = hist[k - pad][0]
                bc = COLORS.get(st, AMBER)
                newest = k == n - 1
                if newest and st == "down" and int(t * 2) % 2:
                    bc = scale(bc, 0.4)
                c.rect(bx, y + 1, bx + bar_w - 1, y + 8, bc)
        if pages > 1:
            for k in range(pages):
                c.pixel(W - 1 - (pages - 1 - k) * 2, H - 1, WHITE if k == page else DIM)

    def _render_focus(self, c, page, t):
        W, H = c.width, c.height
        mons, _pages = self._page(page, 1, only_down=bool(self.cfg.get("only_when_down")))
        if not mons:
            return
        m = mons[0]
        col = COLORS.get(m["status"], AMBER)
        # status badge: check mark (up), cross (down), pause bars (maintenance), dots (pending)
        cx, cy, r = 13, 15, 12
        pulse = 0.75 + 0.25 * math.sin(t * (6 if m["status"] == "down" else 2))
        c.draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=scale(col, 0.3 * pulse), outline=scale(col, pulse))
        if m["status"] == "up":
            c.draw.line((cx - 6, cy, cx - 2, cy + 4, cx + 6, cy - 5), fill=WHITE, width=2)
        elif m["status"] == "down":
            c.draw.line((cx - 5, cy - 5, cx + 5, cy + 5), fill=WHITE, width=2)
            c.draw.line((cx - 5, cy + 5, cx + 5, cy - 5), fill=WHITE, width=2)
        elif m["status"] == "maintenance":
            c.rect(cx - 4, cy - 5, cx - 2, cy + 5, WHITE)
            c.rect(cx + 2, cy - 5, cx + 4, cy + 5, WHITE)
        else:
            for dx in (-5, 0, 5):
                c.rect(cx + dx - 1, cy - 1, cx + dx + 1, cy + 1, WHITE)
        x0 = 30
        c.scroll_text(x0, W, 0, m["name"], "6x10", WHITE, t)
        if m["status"] == "up":
            line = f"UP · {round(m['ping'])} MS" if isinstance(m.get("ping"), (int, float)) else "UP"
        else:
            line = {"down": "DOWN", "pending": "CHECKING", "maintenance": "MAINTENANCE"}.get(m["status"], "")
        c.text(x0, 11, line, "5x7", col)
        if m.get("uptime") is not None:
            c.text_right(W - 1, 12, f"{m['uptime'] * 100:.2f}%", "4x6", GRAY)
        if m["status"] == "down" and m.get("msg"):
            c.scroll_text(x0, W, H - 7, m["msg"], "4x6", scale(RED, 0.8), t)
            return
        # response-time graph of the recent checks (red ticks where a check failed)
        hist = m.get("history") or []
        pings = [p for _s, p in hist if isinstance(p, (int, float))]
        gx0, gx1, gy0, gy1 = x0, W - 1, 20, H - 1
        c.line(gx0, gy1, gx1, gy1, (35, 35, 40))
        if len(hist) >= 2:
            lo, hi = (min(pings), max(pings)) if pings else (0, 1)
            span = (hi - lo) or 1
            n = len(hist)
            prev = None
            for k, (st, p) in enumerate(hist):
                x = gx0 + round(k / (n - 1) * (gx1 - gx0))
                if st == "down" or not isinstance(p, (int, float)):
                    c.line(x, gy0, x, gy1, scale(RED, 0.7))
                    prev = None
                    continue
                y = gy1 - 1 - round((p - lo) / span * (gy1 - gy0 - 2))
                if prev:
                    c.line(prev[0], prev[1], x, y, scale(GREEN, 0.9))
                prev = (x, y)

    def _render_summary(self, c, page, t):
        W, H = c.width, c.height
        mons = self._ordered()
        down = [m for m in mons if m["status"] == "down"]
        maint = [m for m in mons if m["status"] == "maintenance"]
        cx, cy = 14, 15
        if not down:
            # shield with a check, gently pulsing
            k = 0.8 + 0.2 * math.sin(t * 2)
            pts = [(cx - 10, cy - 11), (cx + 10, cy - 11), (cx + 10, cy + 1), (cx, cy + 12), (cx - 10, cy + 1)]
            c.draw.polygon(pts, fill=scale(GREEN, 0.3 * k), outline=scale(GREEN, k))
            c.draw.line((cx - 5, cy - 1, cx - 1, cy + 3, cx + 6, cy - 5), fill=WHITE, width=2)
            c.text(30, 1, "ALL SYSTEMS", "6x10", WHITE)
            c.text(30, 11, "OPERATIONAL", "6x10", GREEN)
            up = sum(1 for m in mons if m["status"] == "up")
            tail = f"{up}/{len(mons)} UP"
            if maint:
                tail += f" · {len(maint)} MAINT"
            if self.uptime is not None:
                tail += f" · {self.uptime * 100:.2f}%"
            c.scroll_text(30, W, H - 7, tail, "4x6", GRAY, t)
            return
        blink = int(t * 2) % 2 == 0
        pts = [(cx, cy - 12), (cx + 12, cy + 10), (cx - 12, cy + 10)]
        c.draw.polygon(pts, fill=scale(RED, 0.8 if blink else 0.35), outline=RED)
        c.rect(cx - 1, cy - 4, cx, cy + 3, WHITE)
        c.rect(cx - 1, cy + 6, cx, cy + 7, WHITE)
        c.text(30, 1, f"{len(down)} DOWN" if len(down) > 1 else "OUTAGE", "6x10", RED)
        c.scroll_text(30, W, 11, " · ".join(m["name"] for m in down), "6x10", WHITE, t)
        others = len(mons) - len(down)
        c.text(30, H - 7, f"{others} OTHER{'S' if others != 1 else ''} OK", "4x6", GRAY)

    def _render_grid(self, c, slide, t):
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
        s["monitor_names"] = [m["name"] for m in self.all_monitors]
        return s
