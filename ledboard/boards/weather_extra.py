"""Extra Weather board screens: sun arc, rain soon, wind, week, living scene, UV + air quality,
moon phase, today in 4 parts, compared to yesterday.

Each `render_*(c, w, t, cfg)` draws one 128x32 screen from the normalised weather dict `w`;
each `has_*(w, cfg)` says whether there's enough data (and reason) to show it.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from ..gfx import DIM, GRAY, WHITE, scale
from .weather_icons import LABELS, draw_icon

HI = (255, 170, 60)
LO = (90, 160, 255)
RAIN = (90, 160, 255)
WARM = (255, 150, 60)
COLD = (110, 170, 255)


def _hm(d, ampm=True):
    if d is None:
        return "--"
    s = d.strftime("%I:%M").lstrip("0")
    return s + (d.strftime("%p").upper() if ampm else "")


def _dur(seconds):
    m = max(0, int(seconds // 60))
    h, m = divmod(m, 60)
    return f"{h}H {m:02d}M" if h else f"{m}M"


def _temp_color(v, unit):
    from .weather import temp_color
    return temp_color(v, unit)


def _shadow_text(c, x, y, s, font, col):
    for dx, dy in ((1, 1), (1, 0), (0, 1)):
        c.text(x + dx, y + dy, s, font, (0, 0, 0))
    return c.text(x, y, s, font, col)


# ---------------------------------------------------------------- 1. sun arc
def has_sun(w, cfg):
    return bool(w.get("sunrise") and w.get("sunset"))


def render_sun(c, w, t, cfg):
    W, H = c.width, c.height
    rise, sett = w["sunrise"], w["sunset"]
    now = datetime.now()
    cx, cy, r = 32, 23, 21                     # horizon at y=23: two clear rows above the times
    day = rise <= now <= sett
    frac = (now - rise).total_seconds() / max(1, (sett - rise).total_seconds()) if day else None
    c.line(2, cy, 62, cy, (60, 45, 20))                                   # horizon
    # the sun's path: one smooth, continuous arc (dim), brighter over the part the sun has covered
    box = (cx - r, cy - r, cx + r, cy + r)
    c.draw.arc(box, 180, 360, fill=(60, 50, 28))
    if frac is not None and frac > 0:
        c.draw.arc(box, 180, 180 + 180 * frac, fill=(160, 115, 30))
    if frac is not None:
        ang = math.radians(frac * 180)
        sx, sy = cx - r * math.cos(ang), cy - r * math.sin(ang)
        glow = 0.75 + 0.25 * math.sin(t * 3)
        from PIL import Image, ImageDraw
        layer = Image.new("RGBA", c.image.size)
        d = ImageDraw.Draw(layer)
        for rr, col in ((4, (90, 50, 0)), (3, (200, 120, 0)), (2, (255, 200, 40))):
            d.ellipse((sx - rr, sy - rr, sx + rr, sy + rr), fill=scale(col, glow if rr == 4 else 1) + (255,))
        part = layer.crop((0, 0, c.width, cy))                # the sun sets behind the horizon line
        c.image.paste(part, (0, 0), part)
    else:                                                                 # night: a sliver of moon over the horizon
        c.draw.ellipse((cx - 4, cy - 12, cx + 4, cy - 4), fill=(220, 220, 200))
        c.draw.ellipse((cx - 2, cy - 13, cx + 6, cy - 5), fill=(0, 0, 0))
    c.text(1, H - 6, _hm(rise, False), "4x6", (255, 170, 60))
    c.text_right(63, H - 6, _hm(sett, False), "4x6", (255, 120, 60))
    x0 = 68
    if day:
        left = (sett - now).total_seconds()
        c.text(x0, 1, "SUNSET", "5x7", (255, 140, 60))
        c.text(x0, 9, _hm(sett), "7x13B", WHITE)
        c.text(x0, H - 7, f"{_dur(left)} LEFT", "4x6", GRAY)
    else:
        nxt = w.get("sunrise_next") if now > sett else rise
        nxt = nxt or rise
        c.text(x0, 1, "SUNRISE", "5x7", (255, 190, 80))
        c.text(x0, 9, _hm(nxt), "7x13B", WHITE)
        c.text(x0, H - 7, f"IN {_dur((nxt - now).total_seconds())}", "4x6", GRAY)


# ---------------------------------------------------------------- 2. rain soon
RAIN_MIN = 0.1        # mm per 15 minutes that counts as rain


def has_rain_soon(w, cfg):
    return any((b.get("mm") or 0) >= RAIN_MIN for b in (w.get("rain15") or [])[:8])


def _intensity(mm15):
    mmh = mm15 * 4
    return "HEAVY" if mmh >= 7.6 else ("MODERATE" if mmh >= 2.5 else "LIGHT")


def render_rain_soon(c, w, t, cfg):
    W, H = c.width, c.height
    bins = (w.get("rain15") or [])[:8]
    now = datetime.now()
    snow = (w.get("temp") is not None and
            (w["temp"] <= 32 if w.get("unit") != "celsius" else w["temp"] <= 0)) or "snow" in (w.get("condition") or "")
    word = "SNOW" if snow else "RAIN"
    wet = [i for i, b in enumerate(bins) if (b["mm"] or 0) >= RAIN_MIN]
    if wet and wet[0] == 0:
        dry = next((i for i in range(len(bins)) if (bins[i]["mm"] or 0) < RAIN_MIN), None)
        head = f"{word} ENDS {_hm(bins[dry]['time'])}" if dry is not None else f"{word} FOR 2+ HOURS"
    else:
        mins = max(5, round((bins[wet[0]]["time"] - now).total_seconds() / 60 / 5) * 5) if wet else 0
        head = f"{word} IN {mins} MIN"
    for k, (x, y) in enumerate(((2, 2), (6, 0), (10, 3))):               # falling drops
        dy = int((t * 6 + k * 2) % 4)
        c.line(x, y + dy, x, y + dy + 1, RAIN)
    c.scroll_text(15, W, 0, head, "5x7", (130, 185, 255), t)
    peak = max((b["mm"] or 0) for b in bins) or 1
    x0, base = 1, 24
    bw = (W - 2) // max(1, len(bins))
    grow = min(1.0, t / 1.0)
    for i, b in enumerate(bins):
        v = b["mm"] or 0
        hgt = round(min(1.0, v / max(peak, 0.6)) * 14 * grow)
        x = x0 + i * bw
        if hgt:
            col = (40, 90, 220) if v * 4 < 2.5 else ((70, 150, 255) if v * 4 < 7.6 else (160, 215, 255))
            c.rect(x, base - hgt + 1, x + bw - 2, base, col)
        else:
            c.line(x, base, x + bw - 2, base, (25, 25, 35))
    for i in (0, 2, 4, 6):
        if i < len(bins):
            lab = "NOW" if i == 0 else f"{i * 15}"
            c.text(x0 + i * bw, H - 5, lab, "4x6", GRAY if i == 0 else DIM)
    if bins and peak >= RAIN_MIN:
        ip = max(range(len(bins)), key=lambda i: bins[i]["mm"] or 0)
        c.text_right(W - 1, H - 5, f"{_intensity(peak)} {_hm(bins[ip]['time'], False)}", "4x6", (150, 210, 255))


# ---------------------------------------------------------------- 3. wind
BEAUFORT = [(1, "CALM"), (4, "LIGHT AIR"), (8, "LIGHT BREEZE"), (13, "GENTLE BREEZE"), (19, "MODERATE BREEZE"),
            (25, "FRESH BREEZE"), (32, "STRONG BREEZE"), (39, "NEAR GALE"), (47, "GALE"), (55, "STRONG GALE"),
            (64, "STORM"), (73, "VIOLENT STORM"), (999, "HURRICANE FORCE")]       # upper limits in mph


def has_wind(w, cfg):
    return w.get("wind") is not None


def render_wind(c, w, t, cfg):
    from .weather import compass
    W, H = c.width, c.height
    cx, cy, r = 15, 15, 14
    c.draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(50, 50, 60))
    for ang in range(0, 360, 45):
        x = cx + (r - 1) * math.sin(math.radians(ang))
        y = cy - (r - 1) * math.cos(math.radians(ang))
        c.pixel(round(x), round(y), (220, 60, 60) if ang == 0 else (GRAY if ang % 90 == 0 else DIM))
    deg = w.get("wind_deg")
    speed = w.get("wind") or 0
    unit = w.get("wind_unit") or ""
    if deg is not None and speed >= 1:
        sway = 6 * math.sin(t * 2.2) * min(1.0, speed / 15)
        to = math.radians(deg + 180 + sway)                       # arrow points where the wind is blowing
        tx, ty = cx + 11 * math.sin(to), cy - 11 * math.cos(to)
        bx, by = cx - 9 * math.sin(to), cy + 9 * math.cos(to)
        c.line(round(bx), round(by), round(tx), round(ty), WHITE)
        for sgn in (-1, 1):
            hx, hy = tx - 5 * math.sin(to + sgn * 0.5), ty + 5 * math.cos(to + sgn * 0.5)
            c.line(round(tx), round(ty), round(hx), round(hy), WHITE)
    else:
        c.draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), outline=GRAY)
    x0 = 34
    wv = c.text(x0, 1, str(round(speed)), "7x13B", (140, 220, 255))
    dir_txt = w.get("wind_dir") or (compass(deg) if deg is not None else "")
    c.text(x0 + wv + 2, 6, f"{unit.upper()} {dir_txt}".strip(), "5x7", WHITE)
    if w.get("gust") is not None and w["gust"] > speed + 2:
        c.text(x0, 16, f"GUSTS {round(w['gust'])}", "5x7", (255, 190, 80))
    mph = speed * (0.621 if "km" in unit else 1)
    desc = next(name for lim, name in BEAUFORT if mph < lim)
    c.text(x0, H - 6, desc, "4x6", GRAY)


# ---------------------------------------------------------------- 4. week at a glance
def has_week(w, cfg):
    return sum(1 for d in w.get("daily") or [] if d.get("hi") is not None and d.get("lo") is not None) >= 4


def render_week(c, w, t, cfg):
    W, H = c.width, c.height
    days = [d for d in (w.get("daily") or []) if d.get("hi") is not None and d.get("lo") is not None][:7]
    unit = w.get("unit")
    lo_all, hi_all = min(d["lo"] for d in days), max(d["hi"] for d in days)
    span = (hi_all - lo_all) or 1
    col = W // len(days)
    x_off = (W - col * len(days)) // 2
    grow = min(1.0, t / 1.2)

    def Y(v):
        return 24 - (v - lo_all) / span * 11

    for i, d in enumerate(days):
        x = x_off + i * col
        mid = x + col // 2
        try:
            name = datetime.fromisoformat(d["date"]).strftime("%a")[:2].upper() if i else "TOD"
        except (ValueError, TypeError):
            name = ""
        c.text_centered(0, name, "4x6", WHITE if i == 0 else GRAY, x, x + col)
        y1, y0 = round(Y(d["hi"])), round(Y(d["lo"]))
        top = round(y0 - (y0 - y1) * grow)
        for y in range(top, y0 + 1):
            v = d["lo"] + (d["hi"] - d["lo"]) * (y0 - y) / max(1, y0 - y1)
            c.rect(mid - 1, y, mid, y, _temp_color(v, unit))
        c.text_centered(y1 - 6, str(round(d["hi"])), "4x6", HI, x, x + col)
        c.text_centered(min(26, y0 + 2), str(round(d["lo"])), "4x6", LO, x, x + col)
        if (d.get("precip") or 0) >= 50 or d.get("condition") in ("rainy", "pouring", "snowy", "lightning-rainy"):
            c.line(x + 1, 1, x + 1, 3, RAIN)


# ---------------------------------------------------------------- 5. living scene
SKIES = {
    "day": ((30, 90, 210), (110, 170, 245)),
    "cloudy": ((35, 40, 55), (85, 90, 105)),
    "dusk": ((45, 25, 95), (240, 120, 50)),
    "dawn": ((40, 50, 120), (250, 160, 90)),
    "night": ((3, 5, 20), (16, 24, 60)),
}


def _sky_kind(w, now):
    rise, sett = w.get("sunrise"), w.get("sunset")
    if rise and sett:
        if abs((now - sett).total_seconds()) < 40 * 60:
            return "dusk"
        if abs((now - rise).total_seconds()) < 40 * 60:
            return "dawn"
        day = rise <= now <= sett
    else:
        day = w.get("is_day", True)
    if not day:
        return "night"
    return "cloudy" if w.get("condition") in ("cloudy", "rainy", "pouring", "snowy", "snowy-rainy", "fog",
                                              "lightning", "lightning-rainy", "hail") else "day"


_SKYLINE = None


def _skyline():
    """Building outlines (x, width, top) and window spots, generated once."""
    global _SKYLINE
    if _SKYLINE is None:
        rnd = random.Random(7)
        bld, x = [], 0
        while x < 256:
            wdt, top = rnd.randint(5, 9), rnd.randint(21, 27)
            wins = [(wx, wy) for wy in range(top + 2, 31, 2) for wx in range(x + 1, x + wdt - 1, 2) if rnd.random() < 0.3]
            bld.append((x, wdt, top, wins))
            x += wdt + rnd.choice((0, 0, 1))
        stars = [(rnd.randrange(256), rnd.randrange(18), rnd.choice((0.25, 0.5, 0.9))) for _ in range(60)]
        _SKYLINE = (bld, stars)
    return _SKYLINE


def has_scene(w, cfg):
    return w.get("temp") is not None


def render_scene(c, w, t, cfg):
    W, H = c.width, c.height
    now = datetime.now()
    kind = _sky_kind(w, now)
    top, bot = SKIES[kind]
    for y in range(H):
        k = min(1.0, y / 24)
        c.line(0, y, W - 1, y, tuple(round(a + (b - a) * k) for a, b in zip(top, bot)))
    cond = w.get("condition") or "cloudy"
    bld, stars = _skyline()
    if kind == "night":
        for x, y, b in stars:
            if x < W and cond in ("sunny", "clear-night", "partlycloudy"):
                tw = b * (0.7 + 0.3 * math.sin(t * 2 + x))
                c.pixel(x, y, scale(WHITE, tw))
    # sun / moon along the sky by time of day
    rise, sett = w.get("sunrise"), w.get("sunset")
    if rise and sett and kind != "night":
        f = min(1.0, max(0.0, (now - rise).total_seconds() / max(1, (sett - rise).total_seconds())))
        sx, sy = 8 + f * (W - 16), 24 - math.sin(f * math.pi) * 20
    else:
        sx, sy = W - 22, 7
    if kind == "night":
        if cond in ("sunny", "clear-night", "partlycloudy", "windy"):
            c.draw.ellipse((sx - 4, sy - 4, sx + 4, sy + 4), fill=(235, 235, 205))
            c.draw.ellipse((sx - 1, sy - 5, sx + 7, sy + 3), fill=top)
    elif kind in ("dusk", "dawn"):
        c.draw.ellipse((sx - 8, sy - 8, sx + 8, sy + 8), fill=(255, 170, 70))
        c.draw.ellipse((sx - 6, sy - 6, sx + 6, sy + 6), fill=(255, 200, 100))
    elif kind == "day":
        c.draw.ellipse((sx - 5, sy - 5, sx + 5, sy + 5), fill=(255, 225, 70))
    # clouds drifting
    if cond not in ("sunny", "clear-night"):
        ccol = (245, 245, 252) if kind == "day" else ((125, 128, 140) if kind != "night" else (50, 55, 75))
        n = 2 if cond == "partlycloudy" else 4
        for i in range(n):
            cx = (i * 47 + t * (2 + i)) % (W + 30) - 20
            cy = 3 + (i * 7) % 10
            c.draw.ellipse((cx, cy, cx + 16, cy + 6), fill=ccol)
            c.draw.ellipse((cx + 4, cy - 3, cx + 12, cy + 4), fill=ccol)
    if cond == "fog":
        for y in range(8, 26, 4):
            off = int(t * 4 + y) % 12
            for x in range(-12 + off, W, 12):
                c.line(x, y, x + 7, y, (110, 110, 120))
    # the city (windows lit at night / dusk)
    lit = kind in ("night", "dusk", "dawn", "cloudy")
    for x, wdt, btop, wins in bld:
        if x >= W:
            break
        c.rect(x, btop, min(W - 1, x + wdt - 1), H - 1, (6, 6, 10))
        if lit:
            for wx, wy in wins:
                if wx < W:
                    c.pixel(wx, wy, (150, 120, 50))
    # precipitation
    if cond in ("rainy", "pouring", "lightning-rainy", "snowy-rainy", "hail"):
        n = 110 if cond == "pouring" else 70
        for i in range(n):
            x = (i * 37 + int(t * 30)) % W
            y = (i * 11 + int(t * 70)) % H
            c.pixel(x, y, (110, 150, 230))
            c.pixel((x - 1) % W, (y - 1) % H, (50, 70, 130))
    if cond in ("snowy", "snowy-rainy"):
        for i in range(45):
            x = (i * 29 + int(math.sin(t + i) * 2)) % W
            y = (i * 13 + int(t * 12)) % H
            c.pixel(x, y, (255, 255, 255))
    if cond in ("lightning", "lightning-rainy") and int(t * 10) % 37 == 0:
        c.rect(0, 0, W - 1, 20, (90, 90, 120))
    label = LABELS.get(cond, cond.replace("-", " ").title()).upper()
    if kind == "dusk" and w.get("sunset"):
        label += f" · SUNSET {_hm(w['sunset'], False)}"
    deg = "--" if w.get("temp") is None else f"{round(w['temp'])}°"
    _shadow_text(c, 3, 1, deg, "7x13B", WHITE)
    _shadow_text(c, 3, 15, label, "4x6", (235, 235, 235))


# ---------------------------------------------------------------- 6. UV + air quality
UV_STOPS = [(60, 200, 60), (240, 220, 0), (255, 140, 0), (230, 30, 30), (160, 60, 220)]
AQ_STOPS = [(60, 200, 60), (240, 220, 0), (255, 140, 0), (230, 30, 30), (160, 60, 220), (130, 20, 40)]


def uv_level(v):
    for lim, name, col in ((3, "LOW", UV_STOPS[0]), (6, "MODERATE", UV_STOPS[1]), (8, "HIGH", UV_STOPS[2]),
                           (11, "VERY HIGH", UV_STOPS[3])):
        if v < lim:
            return name, col
    return "EXTREME", UV_STOPS[4]


def aqi_level(v, scale_name):
    if scale_name == "european_aqi":
        table = ((20, "GOOD"), (40, "FAIR"), (60, "MODERATE"), (80, "POOR"), (100, "VERY POOR"), (1e9, "EXT POOR"))
    else:
        table = ((51, "GOOD"), (101, "MODERATE"), (151, "UNHEALTHY SG"), (201, "UNHEALTHY"), (301, "VERY UNHEALTHY"),
                 (1e9, "HAZARDOUS"))
    for i, (lim, name) in enumerate(table):
        if v < lim:
            return name, AQ_STOPS[min(i, len(AQ_STOPS) - 1)]
    return table[-1][1], AQ_STOPS[-1]


def _uv_now(w):
    if w.get("uv_now") is not None:
        return w["uv_now"]
    hour0 = datetime.now().replace(minute=0, second=0, microsecond=0)
    for h in w.get("hours") or []:
        if h["time"] == hour0 and h.get("uv") is not None:
            return h["uv"]
    return None


def has_air(w, cfg):
    return _uv_now(w) is not None or bool(w.get("aqi"))


def render_air(c, w, t, cfg):
    W, H = c.width, c.height
    rows = []
    uv = _uv_now(w)
    if uv is not None:
        name, col = uv_level(uv)
        rows.append(("UV", uv, 11, UV_STOPS, f"{round(uv)} {name}", col))
    aq = w.get("aqi")
    if aq:
        name, col = aqi_level(aq["value"], aq["scale"])
        rows.append(("AIR", aq["value"], 100 if aq["scale"] == "european_aqi" else 300, AQ_STOPS,
                     f"{round(aq['value'])} {name}", col))
    ys = (2, 13) if len(rows) == 2 else (7,)
    for (label, val, vmax, stops, txt, tcol), y in zip(rows, ys):
        c.text(0, y, label, "4x6", GRAY)
        x0, x1 = 16, 66
        for x in range(x0, x1 + 1):
            f = (x - x0) / (x1 - x0)
            colr = stops[min(len(stops) - 1, int(f * len(stops)))]
            c.rect(x, y + 2, x, y + 3, scale(colr, 0.55))
        mx = x0 + round(min(1.0, val / vmax) * (x1 - x0) * min(1.0, t / 1.0))
        c.rect(mx - 1, y, mx + 1, y + 5, WHITE)
        c.scroll_text(70, W, y - 1, txt, "5x7", tcol, t)
    # when UV peaks today (or tomorrow, after dark) and the sunscreen window (UV 3+)
    hours = w.get("hours") or []
    now = datetime.now()
    day_hours = [h for h in hours if h.get("uv") is not None and h["time"].date() == now.date()]
    if day_hours and max(h["uv"] for h in day_hours) < 1 or (day_hours and now.hour >= 17):
        day_hours = [h for h in hours if h.get("uv") is not None and h["time"].date() == now.date() + timedelta(days=1)]
    if day_hours:
        pk = max(day_hours, key=lambda h: h["uv"])
        when = "TMRW " if pk["time"].date() != now.date() else ""
        bits = [f"PEAK {round(pk['uv'])} {when}{pk['time'].strftime('%-I%p')}"]
        strong = [h["time"].hour for h in day_hours if h["uv"] >= 3]
        if strong:
            a, b = min(strong), max(strong) + 1
            bits.append(f"SUNSCREEN {(a - 1) % 12 + 1}-{(b - 1) % 12 + 1}")
        c.scroll_text(0, W, H - 6, " · ".join(bits), "4x6", DIM, t)


# ---------------------------------------------------------------- 7. moon phase
SYNODIC = 29.530588853
NEW_MOON_REF = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)


def moon_phase(when=None):
    """0 = new, 0.25 = first quarter, 0.5 = full, 0.75 = last quarter."""
    when = when or datetime.now(timezone.utc)
    days = (when - NEW_MOON_REF).total_seconds() / 86400
    return (days % SYNODIC) / SYNODIC


def phase_name(p):
    for lim, name in ((0.0339, "NEW MOON"), (0.216, "WAXING CRESCENT"), (0.284, "FIRST QUARTER"),
                      (0.466, "WAXING GIBBOUS"), (0.534, "FULL MOON"), (0.716, "WANING GIBBOUS"),
                      (0.784, "LAST QUARTER"), (0.966, "WANING CRESCENT")):
        if p < lim:
            return name
    return "NEW MOON"


def has_moon(w, cfg):
    return True


def render_moon(c, w, t, cfg):
    W, H = c.width, c.height
    now = datetime.now(timezone.utc)
    p = moon_phase(now)
    illum = (1 - math.cos(2 * math.pi * p)) / 2
    cx, cy, r = 15, 15, 13
    south = str(cfg.get("latitude") or "").startswith("-")
    for y in range(cy - r, cy + r + 1):
        for x in range(cx - r, cx + r + 1):
            dx, dy = (x - cx) / r, (y - cy) / r
            if dx * dx + dy * dy > 1:
                continue
            if south:
                dx = -dx
            half = math.sqrt(max(0.0, 1 - dy * dy))
            term = half * math.cos(2 * math.pi * p)
            lit = dx > term if p < 0.5 else dx < -term
            crater = (x - 10) ** 2 + (y - 11) ** 2 < 5 or (x - 18) ** 2 + (y - 19) ** 2 < 4 or (x - 13) ** 2 + (y - 21) ** 2 < 2
            if lit:
                c.pixel(x, y, (170, 168, 150) if crater else (228, 225, 205))
            else:
                edge = dx * dx + dy * dy > 0.8
                c.pixel(x, y, (45, 45, 58) if edge else (18, 18, 26))
    x0 = 34
    c.scroll_text(x0, W, 1, phase_name(p), "5x7", (230, 225, 200), t)
    c.text(x0, 10, f"{round(illum * 100)}% LIT", "5x7", GRAY)
    to_full = ((0.5 - p) % 1) * SYNODIC
    to_new = ((1.0 - p) % 1) * SYNODIC

    def when_txt(days, name):
        d = datetime.now() + timedelta(days=days)
        if days < 1:
            return f"{name} TONIGHT" if d.date() == datetime.now().date() else f"{name} TOMORROW"
        n = round(days)
        return f"{name} IN {n} DAY{'S' if n != 1 else ''}"

    first, second = ((to_full, "FULL MOON"), (to_new, "NEW MOON")) if to_full < to_new else \
        ((to_new, "NEW MOON"), (to_full, "FULL MOON"))
    c.text(x0, H - 13, when_txt(*first), "4x6", GRAY)
    d2 = datetime.now() + timedelta(days=second[0])
    c.text(x0, H - 6, f"{second[1]} {d2.strftime('%b %-d').upper()}", "4x6", DIM)


# ---------------------------------------------------------------- 8. today in 4 parts
PARTS = (("MORN", 6, 12), ("AFTN", 12, 18), ("EVE", 18, 24), ("NIGHT", 24, 30))
SEVERITY = ["sunny", "clear-night", "partlycloudy", "cloudy", "fog", "windy", "rainy", "snowy-rainy", "snowy",
            "pouring", "hail", "lightning", "lightning-rainy"]


def _parts(w):
    """The current part of the day and the next three, from the hourly forecast."""
    hours = [h for h in w.get("hours") or [] if h.get("temp") is not None]
    if not hours:
        return []
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    h = now.hour
    start = 0 if 6 <= h < 12 else (1 if 12 <= h < 18 else (2 if h >= 18 else 3))
    base = midnight - timedelta(days=1) if h < 6 else midnight       # before 6am we're in last night's 'night'
    out = []
    for k in range(4):
        idx = (start + k) % 4
        day_shift = (start + k) // 4
        name, a, b = PARTS[idx]
        t0 = base + timedelta(days=day_shift, hours=a)
        t1 = base + timedelta(days=day_shift, hours=b)
        sel = [x for x in hours if t0 <= x["time"] < t1 and x["time"] >= now.replace(minute=0, second=0, microsecond=0)]
        if not sel:
            continue
        temps = [x["temp"] for x in sel]
        temp = min(temps) if name == "NIGHT" else max(temps)
        conds = [x.get("condition") for x in sel if x.get("condition")]
        cond = max(conds, key=lambda c_: SEVERITY.index(c_) if c_ in SEVERITY else 3) if conds else "cloudy"
        rain = max((x.get("precip") or 0) for x in sel)
        out.append({"name": name, "temp": temp, "condition": cond, "rain": rain, "is_day": name in ("MORN", "AFTN")})
    return out


def has_parts(w, cfg):
    return len(_parts(w)) >= 3


def render_parts(c, w, t, cfg):
    W, H = c.width, c.height
    parts = _parts(w)[:4]
    col = W // 4
    unit = w.get("unit")
    for i, p in enumerate(parts):
        x = i * col
        c.text_centered(0, p["name"], "4x6", WHITE if i == 0 else GRAY, x, x + col)
        draw_icon(c, p["condition"], x + 1, 8, 16, t + i * 0.9, p["is_day"])
        c.text(x + 18, 10, f"{round(p['temp'])}°", "5x8", _temp_color(p["temp"], unit))
        if p["rain"] >= 30:
            c.text(x + 18, 20, f"{round(p['rain'])}%", "4x6", RAIN)
        if i:
            c.line(x, 2, x, H - 3, (25, 25, 25))
    c.rect(2, H - 2, col - 3, H - 1, (255, 170, 60))                     # 'now' marker


# ---------------------------------------------------------------- 9. compared to yesterday
def _yesterday_diff(w):
    y = w.get("yesterday") or {}
    today = (w.get("daily") or [{}])[0]
    if y.get("hi") is None or today.get("hi") is None:
        return None
    return today["hi"] - y["hi"]


def has_yesterday(w, cfg):
    d = _yesterday_diff(w)
    try:
        thr = float(cfg.get("yesterday_threshold") or 8)
    except (TypeError, ValueError):
        thr = 8
    return d is not None and abs(d) >= thr


def render_yesterday(c, w, t, cfg):
    W, H = c.width, c.height
    d = _yesterday_diff(w)
    warmer = d > 0
    base = WARM if warmer else COLD
    for i in range(3):                                   # chevrons, a light pulse running along them
        k = 1 - ((i - t * 3) % 3) / 3 * 0.7
        col = scale(base, k)
        y = 4 + i * 8 if warmer else 4 + (2 - i) * 8
        if warmer:
            c.line(4, y + 6, 12, y, col)
            c.line(12, y, 20, y + 6, col)
        else:
            c.line(4, y, 12, y + 6, col)
            c.line(12, y + 6, 20, y, col)
    c.text(28, 1, f"{abs(round(d))}° {'WARMER' if warmer else 'COLDER'}", "7x13B", base)
    c.text(28, 16, "THAN YESTERDAY", "5x7", WHITE)
    today = w["daily"][0]["hi"]
    c.text(28, H - 6, f"HIGH {round(today)}° · YESTERDAY {round(w['yesterday']['hi'])}°", "4x6", GRAY)


SCREENS = {
    # key: (setting, label, has, render)
    "sun": ("show_sun", "Sun arc (sunrise, sunset, daylight left)", has_sun, render_sun),
    "rainsoon": ("show_rain_soon", "Rain soon (next 2 hours; only when rain or snow is coming)", has_rain_soon,
                 render_rain_soon),
    "wind": ("show_wind", "Wind (compass, speed, gusts)", has_wind, render_wind),
    "week": ("show_week", "Week at a glance (7-day high/low bars)", has_week, render_week),
    "scene": ("show_scene", "Living scene (city skyline under the real sky and weather)", has_scene, render_scene),
    "air": ("show_air", "UV + air quality", has_air, render_air),
    "moon": ("show_moon", "Moon phase", has_moon, render_moon),
    "parts": ("show_parts", "Today in 4 parts (morning / afternoon / evening / night)", has_parts, render_parts),
    "yesterday": ("show_yesterday", "Compared to yesterday (only on big temperature swings)", has_yesterday,
                  render_yesterday),
}
