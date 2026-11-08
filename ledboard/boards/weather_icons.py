"""Animated weather icons, drawn procedurally so they move smoothly on the LED panel.

Every icon is drawn into a square box (x, y, size) at time t (seconds). Conditions use
Home Assistant's vocabulary: sunny, clear-night, partlycloudy, cloudy, rainy, pouring,
snowy, snowy-rainy, lightning, lightning-rainy, hail, fog, windy, windy-variant,
exceptional.
"""
from __future__ import annotations

import math

SUN = (255, 200, 0)
SUN_CORE = (255, 235, 90)
RAY = (255, 150, 0)
MOON = (230, 230, 190)
CLOUD = (175, 180, 190)
CLOUD_DARK = (95, 100, 115)
CLOUD_STORM = (70, 72, 90)
RAIN = (60, 130, 255)
SNOW = (235, 240, 255)
BOLT = (255, 240, 60)
FOG = (130, 130, 140)
WIND = (150, 200, 230)
STAR = (255, 255, 200)


def _scale(color, f):
    return tuple(max(0, min(255, int(v * f))) for v in color)


class _Pen:
    """Coordinates on a 32-unit grid, scaled to the icon size and offset to (x, y)."""

    def __init__(self, c, x, y, size):
        self.c, self.x, self.y, self.s = c, x, y, size / 32.0

    def p(self, u, v):
        return self.x + u * self.s, self.y + v * self.s

    def ellipse(self, u0, v0, u1, v1, color):
        x0, y0 = self.p(u0, v0)
        x1, y1 = self.p(u1, v1)
        self.c.draw.ellipse((round(x0), round(y0), round(x1), round(y1)), fill=color)

    def rect(self, u0, v0, u1, v1, color):
        x0, y0 = self.p(u0, v0)
        x1, y1 = self.p(u1, v1)
        self.c.draw.rectangle((round(x0), round(y0), round(x1), round(y1)), fill=color)

    def line(self, u0, v0, u1, v1, color, width=1):
        x0, y0 = self.p(u0, v0)
        x1, y1 = self.p(u1, v1)
        self.c.draw.line((round(x0), round(y0), round(x1), round(y1)), fill=color, width=width)

    def polygon(self, pts, color):
        self.c.draw.polygon([tuple(round(v) for v in self.p(u, w)) for u, w in pts], fill=color)

    def pixel(self, u, v, color):
        x, y = self.p(u, v)
        self.c.pixel(round(x), round(y), color)

    def clip(self, u, v):
        """True if (u, v) is inside the icon box."""
        return 0 <= u < 32 and 0 <= v < 32


# ---------------------------------------------------------------- pieces
def _sun(pen, cu, cv, r, t, rays=True):
    if rays:
        n = 8
        for k in range(n):
            a = t * 0.8 + k * 2 * math.pi / n
            pulse = 1.5 + math.sin(t * 3 + k) * 0.8
            r0, r1 = r + 2, r + 3.5 + pulse
            pen.line(cu + math.cos(a) * r0, cv + math.sin(a) * r0, cu + math.cos(a) * r1, cv + math.sin(a) * r1,
                     RAY, width=max(1, round(pen.s * 1.5)))
    pen.ellipse(cu - r, cv - r, cu + r, cv + r, SUN)
    pen.ellipse(cu - r * 0.55, cv - r * 0.55, cu + r * 0.3, cv + r * 0.3, SUN_CORE)


def _moon(pen, cu, cv, r, t, stars=True):
    pen.ellipse(cu - r, cv - r, cu + r, cv + r, MOON)
    pen.ellipse(cu - r + r * 0.75, cv - r - r * 0.25, cu + r + r * 0.75, cv + r - r * 0.25, (0, 0, 0))
    if stars:
        for i, (u, v) in enumerate(((24, 6), (28, 15), (20, 24), (6, 26), (27, 26))):
            b = 0.25 + 0.75 * (math.sin(t * 2.2 + i * 1.7) + 1) / 2
            pen.pixel(u, v, _scale(STAR, b))


def _cloud(pen, u, v, w, color, shade=None):
    """Cloud whose top-left is (u, v), width w (in 32-unit space), height ~ w/2."""
    h = w * 0.5
    pen.ellipse(u, v + h * 0.35, u + w * 0.45, v + h, color)
    pen.ellipse(u + w * 0.2, v, u + w * 0.7, v + h * 0.85, color)
    pen.ellipse(u + w * 0.5, v + h * 0.25, u + w, v + h, color)
    pen.rect(u + w * 0.2, v + h * 0.6, u + w * 0.8, v + h, color)
    if shade:
        pen.rect(u + w * 0.15, v + h * 0.9, u + w * 0.85, v + h, shade)


def _rain(pen, t, heavy=False, u0=6, u1=28, v0=19, v1=32):
    n = 9 if heavy else 5
    speed = 22 if heavy else 15
    span = v1 - v0
    for i in range(n):
        u = u0 + (i * 37 % (u1 - u0))
        v = v0 + (t * speed + i * 7.3) % span
        pen.line(u, v, u - 1, v + (3 if heavy else 2), RAIN)


def _snow(pen, t, u0=6, u1=28, v0=19, v1=32, n=6):
    span = v1 - v0
    for i in range(n):
        v = v0 + (t * 5 + i * 5.1) % span
        u = u0 + (i * 41 % (u1 - u0)) + math.sin(t * 2 + i) * 1.5
        pen.pixel(u, v, SNOW)
        if pen.s >= 0.9:
            for du, dv in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                pen.pixel(u + du, v + dv, _scale(SNOW, 0.45))


def _hail(pen, t, u0=7, u1=27, v0=19, v1=32):
    for i in range(5):
        phase = (t * 1.6 + i * 0.37) % 1.0
        v = v0 + phase * (v1 - v0)
        if phase > 0.85:                      # little bounce at the bottom
            v = v1 - 2 - (1 - (phase - 0.85) / 0.15) * 2
        u = u0 + (i * 29 % (u1 - u0))
        pen.rect(u, v, u + 1.2, v + 1.2, SNOW)


def _bolt(pen, t, u=14, v=15):
    # double flash every 3 seconds
    ph = t % 3.0
    if ph < 0.12 or 0.25 < ph < 0.35:
        pen.polygon([(u + 3, v), (u - 1, v + 8), (u + 2, v + 8), (u, v + 16), (u + 7, v + 6), (u + 3, v + 6), (u + 6, v)], BOLT)
        return True
    return False


def _fog(pen, t):
    for i, v in enumerate((9, 14, 19, 24)):
        off = (t * (3 + i) + i * 5) % 12
        u = -12 + off
        while u < 32:
            pen.line(max(0, u), v, min(31, u + 8), v, FOG)
            u += 12


def _wind(pen, t):
    for i, v in enumerate((9, 16, 23)):
        off = (t * 10 + i * 9) % 44 - 12
        pts = []
        for k in range(14):
            u = off + k
            if 0 <= u < 32:
                pts.append((u, v + math.sin((u + t * 6) / 3.0) * 1.5))
        for (a, b), (c2, d) in zip(pts, pts[1:]):
            pen.line(a, b, c2, d, WIND)


# ---------------------------------------------------------------- public
CONDITIONS = ("sunny", "clear-night", "partlycloudy", "cloudy", "rainy", "pouring", "snowy", "snowy-rainy",
              "lightning", "lightning-rainy", "hail", "fog", "windy", "windy-variant", "exceptional")


def draw_icon(c, condition, x, y, size, t, is_day=True):
    """Draw an animated icon for `condition` in the size x size box at (x, y)."""
    pen = _Pen(c, x, y, size)
    cond = condition or "cloudy"
    if cond == "sunny" and not is_day:
        cond = "clear-night"
    drift = math.sin(t * 0.7) * 1.5          # gentle cloud drift

    if cond == "sunny":
        _sun(pen, 16, 16, 7, t)
    elif cond == "clear-night":
        _moon(pen, 13, 15, 8, t)
    elif cond == "partlycloudy":
        if is_day:
            _sun(pen, 11, 11, 6, t)
        else:
            _moon(pen, 11, 11, 6, t, stars=False)
        _cloud(pen, 9 + drift, 14, 21, CLOUD, CLOUD_DARK)
    elif cond == "cloudy":
        _cloud(pen, 2 - drift, 6, 18, CLOUD_DARK)
        _cloud(pen, 8 + drift, 11, 22, CLOUD, CLOUD_DARK)
    elif cond in ("rainy", "pouring"):
        _rain(pen, t, heavy=cond == "pouring")
        _cloud(pen, 4 + drift * 0.5, 4, 24, CLOUD_DARK if cond == "pouring" else CLOUD, CLOUD_DARK)
    elif cond == "snowy":
        _snow(pen, t)
        _cloud(pen, 4 + drift * 0.5, 4, 24, CLOUD, CLOUD_DARK)
    elif cond == "snowy-rainy":
        _rain(pen, t, u0=6, u1=17)
        _snow(pen, t, u0=17, u1=28, n=3)
        _cloud(pen, 4 + drift * 0.5, 4, 24, CLOUD, CLOUD_DARK)
    elif cond in ("lightning", "lightning-rainy"):
        if cond == "lightning-rainy":
            _rain(pen, t, heavy=True)
        flash = _bolt(pen, t)
        _cloud(pen, 4 + drift * 0.5, 3, 24, (150, 150, 165) if flash else CLOUD_STORM, CLOUD_DARK)
    elif cond == "hail":
        _hail(pen, t)
        _cloud(pen, 4 + drift * 0.5, 4, 24, CLOUD_DARK)
    elif cond == "fog":
        _cloud(pen, 6, 2, 20, CLOUD_DARK)
        _fog(pen, t)
    elif cond in ("windy", "windy-variant"):
        if cond == "windy-variant":
            _cloud(pen, 10 + drift, 1, 18, CLOUD_DARK)
        _wind(pen, t)
    else:  # exceptional / unknown: a pulsing warning triangle
        b = 0.6 + 0.4 * (math.sin(t * 4) + 1) / 2
        pen.polygon([(16, 5), (28, 27), (4, 27)], _scale((255, 120, 0), b))
        pen.rect(15, 12, 17, 20, (0, 0, 0))
        pen.rect(15, 23, 17, 25, (0, 0, 0))


# WMO weather interpretation codes (Open-Meteo) -> Home Assistant condition names
WMO = {
    0: "sunny", 1: "sunny", 2: "partlycloudy", 3: "cloudy", 45: "fog", 48: "fog",
    51: "rainy", 53: "rainy", 55: "rainy", 56: "snowy-rainy", 57: "snowy-rainy",
    61: "rainy", 63: "rainy", 65: "pouring", 66: "snowy-rainy", 67: "snowy-rainy",
    71: "snowy", 73: "snowy", 75: "snowy", 77: "snowy", 80: "rainy", 81: "rainy", 82: "pouring",
    85: "snowy", 86: "snowy", 95: "lightning-rainy", 96: "hail", 99: "hail",
}

LABELS = {
    "sunny": "Sunny", "clear-night": "Clear", "partlycloudy": "Partly cloudy", "cloudy": "Cloudy",
    "rainy": "Rain", "pouring": "Heavy rain", "snowy": "Snow", "snowy-rainy": "Sleet",
    "lightning": "Thunder", "lightning-rainy": "Thunderstorms", "hail": "Hail", "fog": "Fog",
    "windy": "Windy", "windy-variant": "Windy", "exceptional": "Severe weather",
}
