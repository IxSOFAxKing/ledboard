"""Alerts: short, styled interruptions (doorbell, washer done, garage open, severe weather...).

Alerts queue by priority:
  low       - waits for the current board to finish
  normal    - interrupts now
  high      - interrupts now, flashing border
  critical  - interrupts now and repeats until dismissed
Each alert has an animated icon, a colored animated border, a title and an optional message.
"""
from __future__ import annotations

import itertools
import math
import threading
import time
from dataclasses import dataclass, field

from . import fonts
from .gfx import WHITE, hex_to_rgb, scale

PRIORITIES = {"low": 0, "normal": 1, "high": 2, "critical": 3}
_ids = itertools.count(1)


@dataclass
class Alert:
    title: str
    message: str = ""
    icon: str = "bell"
    color: tuple = (255, 170, 0)
    priority: int = 1
    seconds: float = 10.0
    repeat_every: float = 0.0          # seconds between repeats (0 = show once)
    until_dismissed: bool = False
    expires: float = 0.0               # epoch; 0 = never
    source: str = "api"
    id: int = field(default_factory=lambda: next(_ids))
    created: float = field(default_factory=time.time)
    shown_count: int = 0
    last_shown: float = 0.0
    dismissed: bool = False

    def public(self):
        return {"id": self.id, "key": getattr(self, "key", None), "title": self.title, "message": self.message, "icon": self.icon,
                "priority": [k for k, v in PRIORITIES.items() if v == self.priority][0],
                "repeating": bool(self.repeat_every), "until_dismissed": self.until_dismissed,
                "shown": self.shown_count, "source": self.source, "created": self.created}


class AlertManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.alerts: list[Alert] = []
        self.current: Alert | None = None
        self.started = 0.0               # monotonic time current alert started

    # -- api ---------------------------------------------------------------------
    def add(self, title, message="", icon="bell", color="#ffaa00", priority="normal", seconds=10,
            repeat_minutes=0, until_dismissed=None, expires_in=None, source="api", replace_key=None):
        pr = PRIORITIES.get(str(priority).lower(), 1) if not isinstance(priority, int) else priority
        if until_dismissed is None:
            until_dismissed = pr >= PRIORITIES["critical"]
        repeat = float(repeat_minutes or 0) * 60
        if until_dismissed and not repeat:
            repeat = 60.0
        a = Alert(title=str(title or "")[:120], message=str(message or "")[:500], icon=icon or "bell",
                  color=hex_to_rgb(color, (255, 170, 0)), priority=pr, seconds=max(3.0, float(seconds or 10)),
                  repeat_every=repeat, until_dismissed=bool(until_dismissed),
                  expires=(time.time() + float(expires_in) if expires_in
                           else (0 if until_dismissed else (time.time() + 86400 if repeat else 0))),
                  source=source)
        with self.lock:
            if replace_key:          # e.g. the same NWS warning updated: replace instead of duplicating
                for old in self.alerts:
                    if getattr(old, "key", None) == replace_key:
                        old.dismissed = True
                a.key = replace_key
            self.alerts.append(a)
        return a

    def dismiss(self, alert_id="all", key=None):
        """Dismiss one alert by id, every alert with `key`, or all of them."""
        with self.lock:
            n = 0
            for a in self.alerts:
                if key is not None:
                    hit = getattr(a, "key", None) == key
                else:
                    hit = alert_id in ("all", None) or a.id == int(alert_id)
                if hit and not a.dismissed:
                    a.dismissed = True
                    n += 1
            if self.current and self.current.dismissed:
                self.current = None
            return n

    def active(self):
        with self.lock:
            self._prune()
            return [a.public() for a in self.alerts]

    # -- scheduling (called from the render loop) --------------------------------------
    def _prune(self):
        now = time.time()
        self.alerts = [a for a in self.alerts if not a.dismissed and not (a.expires and now > a.expires)
                       and not (not a.repeat_every and a.shown_count > 0 and a is not self.current)]

    def _due(self, a, now):
        if a.shown_count == 0:
            return True
        return bool(a.repeat_every) and now - a.last_shown >= a.repeat_every

    def next_due(self, min_priority=0):
        now = time.time()
        with self.lock:
            self._prune()
            due = [a for a in self.alerts if self._due(a, now) and a.priority >= min_priority]
            if not due:
                return None
            return sorted(due, key=lambda a: (-a.priority, a.created))[0]

    def tick(self, at_boundary=False):
        """Returns (alert, seconds since it started) to draw now, or (None, 0)."""
        mono = time.monotonic()
        if self.current:
            if self.current.dismissed or mono - self.started >= self.current.seconds:
                self.current = None
            else:
                # a more urgent alert pre-empts a less urgent one
                urgent = self.next_due(self.current.priority + 1)
                if not urgent:
                    return self.current, mono - self.started
                # interrupted: make it due again so it plays in full after the urgent one
                if not self.current.repeat_every:
                    self.current.shown_count = 0
                else:
                    self.current.last_shown = 0.0
                self.current = None
        nxt = self.next_due(0 if at_boundary else 1)
        if not nxt:
            return None, 0.0
        nxt.shown_count += 1
        nxt.last_shown = time.time()
        self.current, self.started = nxt, mono
        return nxt, 0.0


# ---------------------------------------------------------------- drawing
def _border(c, a, t):
    """Marching lights around the edge; high/critical flash the whole border."""
    W, H = c.width, c.height
    col = a.color
    if a.priority >= PRIORITIES["high"] and int(t * 4) % 2 == 0:
        c.rect(0, 0, W - 1, 0, col); c.rect(0, H - 1, W - 1, H - 1, col)
        c.rect(0, 0, 0, H - 1, col); c.rect(W - 1, 0, W - 1, H - 1, col)
        return
    perim = [(x, 0) for x in range(W)] + [(W - 1, y) for y in range(1, H)] + \
            [(x, H - 1) for x in range(W - 2, -1, -1)] + [(0, y) for y in range(H - 2, 0, -1)]
    off = int(t * 40)
    for i, (x, y) in enumerate(perim):
        k = (i + off) % 8
        if k < 3:
            c.pixel(x, y, scale(col, 1.0 if k == 0 else 0.55 if k == 1 else 0.25))


def render_alert(c, a, t):
    W, H = c.width, c.height
    c.clear()
    _border(c, a, t)
    draw_alert_icon(c, a.icon, 3, 3, H - 6, t, a.color)
    x0 = H + 1
    title = a.title.upper()
    # title as large as fits on one line (scrolls if still too long)
    tf = None
    for key, px in (("anton", 14), ("anton", 12), (None, "7x13B"), (None, "6x10")):
        f = fonts.scalable(key, px, ref="ABCDEFGHIJKLMNOPQRSTUVWXYZ") if key else fonts.get(px)
        if f.width(title) <= W - x0 - 3:
            tf = f
            break
    tf = tf or fonts.get("6x10")
    ty = 3 if a.message else (H - tf.height) // 2
    c.scroll_text(x0, W - 2, ty, title, tf, WHITE, t, speed=30, pause=1.0)
    if a.message:
        c.scroll_text(x0, W - 2, H - 11, a.message, "5x8", scale(a.color, 1.0), t, speed=28, pause=1.2)


# ---------------------------------------------------------------- icons (24x24-ish, animated)
ICONS = ("bell", "doorbell", "washer", "dryer", "garage", "door", "warning", "info", "check", "printer", "mail",
         "package", "car", "lock", "unlock", "storm", "tornado", "fire", "water", "phone", "trash", "calendar",
         "timer", "thermometer")


def draw_alert_icon(c, icon, x, y, s, t, color):
    """Draw `icon` animated at time t inside the s x s box at (x, y)."""
    d = c.draw
    k = s / 24.0

    def P(u, v):
        return x + u * k, y + v * k

    def box(u0, v0, u1, v1):
        a, b = P(u0, v0)
        e, f = P(u1, v1)
        return (round(a), round(b), round(e), round(f))

    col = color
    light = scale(color, 0.45)
    icon = icon if icon in ICONS else "bell"
    if icon in ("bell", "doorbell"):
        ang = math.sin(t * 9) * (0.35 if int(t * 1.2) % 2 == 0 else 0.05)        # rings in bursts
        cx, cy = 12, 3
        pts = [(-7, 17), (-6, 9), (-3, 4), (3, 4), (6, 9), (7, 17), (8, 18), (-8, 18)]
        rot = [(cx + u * math.cos(ang) - (v - cy) * math.sin(ang) * 0.3, v) for u, v in pts]
        d.polygon([P(u, v) for u, v in rot], fill=col)
        d.ellipse(box(10, 1, 14, 5), fill=col)
        clap = 12 + math.sin(t * 9 + 0.6) * 3 * (1 if int(t * 1.2) % 2 == 0 else 0.1)
        d.ellipse(box(clap - 2, 18, clap + 2, 22), fill=WHITE)
        if icon == "doorbell":
            for i, r in enumerate((3, 6)):
                if int(t * 3 + i) % 2:
                    d.arc(box(19 - r, 6 - r, 19 + r, 6 + r), 300, 60, fill=light)
    elif icon in ("washer", "dryer"):
        d.rounded_rectangle(box(2, 1, 22, 23), radius=max(1, round(2 * k)), fill=scale(col, 0.35))
        d.rectangle(box(4, 3, 9, 4), fill=WHITE)
        d.ellipse(box(5, 7, 19, 21), fill=(10, 10, 10), outline=WHITE)
        ang = t * (8 if icon == "washer" else 5)
        for i in range(3):
            a = ang + i * 2.1
            d.line([P(12, 14), P(12 + math.cos(a) * 5, 14 + math.sin(a) * 5)], fill=col, width=max(1, round(k)))
    elif icon in ("garage", "door"):
        if icon == "garage":
            d.polygon([P(1, 9), P(12, 2), P(23, 9)], fill=col)
            d.rectangle(box(3, 9, 21, 23), fill=scale(col, 0.3))
            lift = (math.sin(t * 2) + 1) / 2 * 10
            for v in range(10, 23, 3):
                if v > 9 + lift:
                    d.rectangle(box(4, v, 20, v + 1), fill=WHITE)
        else:
            d.rectangle(box(5, 1, 19, 23), fill=scale(col, 0.35))
            swing = (math.sin(t * 2.5) + 1) / 2
            d.polygon([P(5, 1), P(5 + 14 * (1 - swing * 0.6), 3), P(5 + 14 * (1 - swing * 0.6), 21), P(5, 23)], fill=col)
            d.ellipse(box(14, 11, 16, 13), fill=WHITE)
    elif icon == "warning":
        b = 0.55 + 0.45 * (math.sin(t * 6) + 1) / 2
        d.polygon([P(12, 1), P(23, 22), P(1, 22)], fill=scale(col, b))
        d.rectangle(box(11, 8, 13, 15), fill=(0, 0, 0))
        d.rectangle(box(11, 17, 13, 19), fill=(0, 0, 0))
    elif icon == "info":
        r = 11 + math.sin(t * 3) * 0.6
        d.ellipse(box(12 - r, 12 - r, 12 + r, 12 + r), fill=col)
        d.rectangle(box(11, 10, 13, 19), fill=WHITE)
        d.rectangle(box(11, 5, 13, 7), fill=WHITE)
    elif icon == "check":
        d.ellipse(box(1, 1, 23, 23), fill=col)
        prog = min(1.0, (t % 3.0) / 0.8)                     # the tick draws itself
        pts = [P(6, 12), P(10, 17), P(18, 7)]
        if prog < 0.4:
            q = prog / 0.4
            d.line([pts[0], (pts[0][0] + (pts[1][0] - pts[0][0]) * q, pts[0][1] + (pts[1][1] - pts[0][1]) * q)],
                   fill=WHITE, width=max(2, round(2 * k)))
        else:
            q = (prog - 0.4) / 0.6
            d.line([pts[0], pts[1], (pts[1][0] + (pts[2][0] - pts[1][0]) * q, pts[1][1] + (pts[2][1] - pts[1][1]) * q)],
                   fill=WHITE, width=max(2, round(2 * k)))
    elif icon == "printer":
        d.rectangle(box(1, 2, 3, 22), fill=GRAY_)
        d.rectangle(box(21, 2, 23, 22), fill=GRAY_)
        d.rectangle(box(1, 2, 23, 3), fill=GRAY_)
        d.rectangle(box(3, 20, 21, 22), fill=(60, 150, 255))
        hx = 12 + math.sin(t * 3) * 6
        d.rectangle(box(1, 7, 23, 7), fill=GRAY_)
        d.rectangle(box(hx - 3, 5, hx + 3, 10), fill=col)
        h = 2 + (t * 2) % 7
        d.rectangle(box(8, 20 - h, 16, 19), fill=(255, 120, 30))
    elif icon in ("mail", "package"):
        bob = math.sin(t * 4) * 1.5
        if icon == "mail":
            d.rectangle(box(1, 6 + bob, 23, 20 + bob), fill=col)
            d.line([P(1, 6 + bob), P(12, 14 + bob), P(23, 6 + bob)], fill=WHITE, width=max(1, round(k)))
        else:
            d.polygon([P(12, 2 + bob), P(23, 7 + bob), P(12, 12 + bob), P(1, 7 + bob)], fill=scale(col, 1.0))
            d.polygon([P(1, 7 + bob), P(12, 12 + bob), P(12, 23 + bob), P(1, 18 + bob)], fill=scale(col, 0.7))
            d.polygon([P(23, 7 + bob), P(12, 12 + bob), P(12, 23 + bob), P(23, 18 + bob)], fill=scale(col, 0.5))
    elif icon == "car":
        off = ((t * 14) % 34) - 5
        d.rounded_rectangle(box(off - 6, 10, off + 10, 17), radius=max(1, round(2 * k)), fill=col)
        d.polygon([P(off - 3, 10), P(off, 6), P(off + 6, 6), P(off + 8, 10)], fill=col)
        for wx in (off - 3, off + 6):
            d.ellipse(box(wx - 2, 16, wx + 2, 20), fill=WHITE)
        d.line([P(0, 21), P(24, 21)], fill=light)
    elif icon in ("lock", "unlock"):
        open_ = icon == "unlock"
        lift = 3 if open_ else (abs(math.sin(t * 3)) * 1.2)
        d.arc(box(6, 1 - lift, 18, 13 - lift), 180, 360, fill=WHITE, width=max(2, round(2 * k)))
        d.line([P(6, 7 - lift), P(6, 12)], fill=WHITE, width=max(2, round(2 * k)))
        if not open_:
            d.line([P(18, 7 - lift), P(18, 12)], fill=WHITE, width=max(2, round(2 * k)))
        d.rounded_rectangle(box(3, 11, 21, 23), radius=max(1, round(2 * k)), fill=col)
        d.rectangle(box(11, 15, 13, 19), fill=(0, 0, 0))
    elif icon == "storm":
        from .boards.weather_icons import draw_icon
        draw_icon(c, "lightning-rainy", x - 2, y - 2, s + 4, t, True)
    elif icon == "tornado":
        for i in range(7):
            v = 2 + i * 3
            w = 11 - i * 1.4
            sx = 12 + math.sin(t * 4 + i * 0.7) * (1 + i * 0.4)
            d.line([P(sx - w, v), P(sx + w, v)], fill=scale(col, 1 - i * 0.08), width=max(1, round(k)))
    elif icon == "fire":
        for i in range(5):
            fl = math.sin(t * 9 + i * 1.3) * 2
            u = 4 + i * 4
            h = 10 + (i % 2) * 5 + fl
            d.polygon([P(u - 3, 23), P(u + fl * 0.3, 23 - h), P(u + 3, 23)], fill=(255, 90 + i * 25, 0))
    elif icon == "water":
        for i in range(3):
            v = ((t * 14) + i * 8) % 24
            u = 6 + i * 6
            d.polygon([P(u, v - 4), P(u - 2.5, v + 1), P(u + 2.5, v + 1)], fill=(60, 140, 255))
            d.ellipse(box(u - 2.5, v - 1, u + 2.5, v + 3), fill=(60, 140, 255))
    elif icon == "phone":
        jig = math.sin(t * 40) * 1.2 if int(t * 1.5) % 2 == 0 else 0
        d.rounded_rectangle(box(7 + jig, 1, 17 + jig, 23), radius=max(1, round(2 * k)), fill=col)
        d.rectangle(box(9 + jig, 4, 15 + jig, 18), fill=(10, 10, 10))
    elif icon == "trash":
        lid = abs(math.sin(t * 2.5)) * 4
        d.rectangle(box(5, 7, 19, 23), fill=col)
        d.rectangle(box(3, 4 - lid, 21, 5 - lid), fill=WHITE)
        for u in (9, 12, 15):
            d.line([P(u, 10), P(u, 20)], fill=(0, 0, 0))
    elif icon == "calendar":
        d.rectangle(box(2, 4, 22, 23), fill=WHITE)
        d.rectangle(box(2, 4, 22, 8), fill=col)
        day = time.localtime().tm_mday
        c.text_centered(round(y + 10 * k), str(day), "5x7", (0, 0, 0), round(x + 2 * k), round(x + 22 * k))
    elif icon == "timer":
        d.ellipse(box(2, 3, 22, 23), outline=col, width=max(1, round(2 * k)))
        a = t * 2
        d.line([P(12, 13), P(12 + math.sin(a) * 7, 13 - math.cos(a) * 7)], fill=WHITE, width=max(1, round(k)))
        d.rectangle(box(10, 0, 14, 2), fill=col)
    elif icon == "thermometer":
        lvl = 8 + (math.sin(t * 2) + 1) * 4
        d.rounded_rectangle(box(9, 1, 15, 18), radius=max(1, round(3 * k)), outline=WHITE)
        d.ellipse(box(7, 15, 17, 23), fill=col)
        d.rectangle(box(11, 18 - lvl, 13, 18), fill=col)


GRAY_ = (110, 110, 120)
