"""More 'living scene' weather screens: the same real sky (time of day, sun / moon, stars, clouds) and
live weather (rain, snow, fog, lightning) as the city scene, over different landscapes."""
from __future__ import annotations

import math
import random

from ..gfx import WHITE, scale
from .weather_extra import _shadow_text, has_scene, scene_label, scene_precip, scene_sky


def _shade(day, ctx, night_k=0.18, dusk_k=0.45):
    """A landscape color for the time of day: full by day, darker at dusk / dawn, near-black at night."""
    k = {"night": night_k, "dusk": dusk_k, "dawn": dusk_k, "cloudy": 0.7}.get(ctx["kind"], 1.0)
    return scale(day, k)


def _wind_sway(w, t, phase=0.0):
    speed = w.get("wind") or 0
    if "km" in (w.get("wind_unit") or ""):
        speed *= 0.621
    amp = min(2.0, speed / 10)                     # calm: still; 20+ mph: two pixels of sway
    return round(math.sin(t * (1.5 + speed / 15) + phase) * amp)


# ---------------------------------------------------------------- countryside
def render_countryside(c, w, t, cfg):
    W, H = c.width, c.height
    ctx = scene_sky(c, w, t, horizon=22)
    snow = ctx["snowy"]
    back = _shade((200, 205, 215) if snow else (45, 115, 55), ctx)
    front = _shade((175, 180, 195) if snow else (30, 90, 38), ctx)
    for x in range(W):                                           # two layers of rolling hills
        yb = round(20 + 2.5 * math.sin(x / 14 + 1.2) + 1.5 * math.sin(x / 5.3))
        c.line(x, yb, x, H - 1, back)
    for x in range(W):
        yf = round(26 + 2 * math.sin(x / 9 + 0.4))
        c.line(x, yf, x, H - 1, front)
    # farmhouse: red barn-style house, window lit after dark
    hx, hy = 92, 17
    body = _shade((160, 35, 28), ctx, 0.25)
    c.rect(hx, hy, hx + 11, hy + 7, body)
    roof = _shade((80, 30, 25), ctx, 0.3) if not snow else _shade((230, 230, 240), ctx, 0.35)
    c.draw.polygon([(hx - 1, hy), (hx + 5, hy - 5), (hx + 12, hy)], fill=roof)
    c.rect(hx + 5, hy + 4, hx + 6, hy + 7, scale(body, 0.5))      # door
    win = (255, 200, 90) if ctx["lit"] else _shade((90, 120, 150), ctx)
    c.rect(hx + 2, hy + 2, hx + 3, hy + 3, win)
    c.rect(hx + 8, hy + 2, hx + 9, hy + 3, win)
    # trees, swaying with the real wind
    leaf = _shade((210, 220, 230) if snow else (35, 130, 45), ctx, 0.22)
    trunk = _shade((90, 60, 35), ctx, 0.3)
    for i, (tx, base) in enumerate(((60, 23), (74, 22), (113, 22), (122, 23))):
        sway = _wind_sway(w, t, i * 1.7)
        c.line(tx, base - 4, tx, base + 2, trunk)
        c.draw.ellipse((tx - 3 + sway, base - 9, tx + 3 + sway, base - 3), fill=leaf)
    # fence along the front hill
    post = _shade((140, 110, 80), ctx, 0.3)
    for x in range(48, 90, 6):
        y = round(26 + 2 * math.sin(x / 9 + 0.4))
        c.line(x, y - 3, x, y, post)
    c.line(48, 24, 89, 24, scale(post, 0.8))
    scene_precip(c, ctx["cond"], t)
    scene_label(c, w, ctx)


# ---------------------------------------------------------------- lakeside
def render_lake(c, w, t, cfg):
    W, H = c.width, c.height
    hz = 19
    ctx = scene_sky(c, w, t, horizon=hz)
    # far shore: a dark tree line on the horizon
    rnd = random.Random(5)
    shore = _shade((25, 60, 35), ctx, 0.25)
    x = 0
    while x < W:
        wdt, hgt = rnd.randint(3, 6), rnd.randint(1, 4)
        c.draw.polygon([(x, hz), (x + wdt // 2, hz - hgt), (x + wdt, hz)], fill=shore)
        x += wdt - 1
    c.line(0, hz, W - 1, hz, shore)
    # water: the sky's colors, darker and flipped
    top, bot = ctx["top"], ctx["bot"]
    for y in range(hz + 1, H):
        k = (y - hz) / (H - hz)
        col = tuple(round((b + (a - b) * k) * 0.45) for a, b in zip(top, bot))
        c.line(0, y, W - 1, y, col)
    # sun / moon reflection: a shimmering column of dashes
    if ctx["sun_shown"]:
        sx = ctx["sun"][0]
        refl = (255, 200, 90) if ctx["kind"] != "night" else (200, 200, 180)
        for y in range(hz + 2, H, 2):
            half = max(1, 5 - (y - hz) // 3)
            jitter = round(math.sin(t * 3 + y) * 1.5)
            if (y + int(t * 6)) % 3:
                c.line(round(sx) - half + jitter, y, round(sx) + half + jitter, y, scale(refl, 0.75))
    # gentle waves drifting across
    wave = scale(tuple(min(255, v + 60) for v in ctx["bot"]), 0.6)
    for i in range(9):
        wx = (i * 31 + int(t * (4 + i % 3))) % (W + 10) - 5
        wy = hz + 3 + (i * 5) % (H - hz - 4)
        c.line(wx, wy, wx + 3, wy, wave)
    # a small sailboat drifting along
    bx = int((t * 2 + 30) % (W + 20)) - 10
    hull = _shade((120, 70, 40), ctx, 0.35)
    sail = _shade((240, 240, 235), ctx, 0.35)
    c.rect(bx, hz + 4, bx + 6, hz + 5, hull)
    c.draw.polygon([(bx + 3, hz - 3), (bx + 3, hz + 3), (bx + 7, hz + 3)], fill=sail)
    c.line(bx + 3, hz - 3, bx + 3, hz + 3, hull)
    scene_precip(c, ctx["cond"], t, y1=hz)
    if ctx["cond"] in ("rainy", "pouring", "lightning-rainy", "snowy-rainy", "hail"):   # ripples on the water
        for i in range(14):
            rx = (i * 41 + int(t * 7) * 13) % W
            ry = hz + 2 + (i * 7 + int(t * 5)) % (H - hz - 2)
            c.line(rx - 1, ry, rx + 1, ry, (120, 150, 200))
    scene_label(c, w, ctx)


# ---------------------------------------------------------------- mountains
def _ridge(x, seed, base, amp):
    """Jagged mountain ridge height at column x."""
    return base - abs(((x + seed) % 34) - 17) * amp / 17 - 3 * math.sin((x + seed) / 7)


def render_mountains(c, w, t, cfg):
    W, H = c.width, c.height
    ctx = scene_sky(c, w, t, horizon=24)
    snow = ctx["snowy"]
    far = _shade((95, 110, 140), ctx, 0.2)
    near = _shade((60, 70, 85), ctx, 0.15)
    cap = _shade((240, 242, 250), ctx, 0.35)
    snowline = 15                                       # only the high peaks wear snow
    for x in range(W):                                  # far range with snowcaps
        y = round(_ridge(x, 5, 24, 14))
        c.line(x, y, x, H - 1, far)
        if y < snowline:
            c.line(x, y, x, min(snowline, y + 3), cap)
    for x in range(W):                                  # near range (capped only when it's snowing)
        y = round(_ridge(x * 1.3, 20, 28, 10))
        c.line(x, y, x, H - 1, near)
        if snow:
            c.pixel(x, y, cap)
    # pine forest along the bottom
    pine = _shade((25, 85, 40), ctx, 0.2)
    tip = cap if snow else pine
    rnd = random.Random(9)
    x = 0
    while x < W:
        hgt = rnd.randint(5, 9)
        sway = _wind_sway(w, t, x)
        c.draw.polygon([(x - 2, H - 1), (x + sway, H - 1 - hgt), (x + 2, H - 1)], fill=pine)
        c.pixel(x + sway, H - 1 - hgt, tip)
        x += rnd.randint(3, 5)
    scene_precip(c, ctx["cond"], t)
    scene_label(c, w, ctx)


# ---------------------------------------------------------------- cozy window
def render_window(c, w, t, cfg):
    W, H = c.width, c.height
    gx0, gy0, gx1, gy1 = 43, 2, W - 4, H - 6           # the glass
    ctx = scene_sky(c, w, t, horizon=24)
    # outside: soft hills under the sky
    hill = _shade((200, 205, 215) if ctx["snowy"] else (40, 100, 50), ctx, 0.15)
    for x in range(gx0, gx1 + 1):
        c.line(x, round(22 + 2 * math.sin(x / 10)), x, gy1, hill)
    scene_precip(c, ctx["cond"], t, y1=gy1)
    # the room around the window (everything outside the glass)
    wall, wood = (28, 20, 16), (150, 95, 50)
    c.rect(0, 0, W - 1, gy0 - 1, wall)
    c.rect(0, gy1 + 1, W - 1, H - 1, wall)
    c.rect(0, 0, gx0 - 1, H - 1, wall)
    c.rect(gx1 + 1, 0, W - 1, H - 1, wall)
    c.draw.rectangle((gx0 - 2, gy0 - 2, gx1 + 2, gy1 + 2), outline=wood, width=2)   # frame
    mx, my = (gx0 + gx1) // 2, (gy0 + gy1) // 2
    c.line(mx, gy0, mx, gy1, wood)                                                 # mullions
    c.line(gx0, my, gx1, my, wood)
    c.rect(gx0 - 4, gy1 + 3, gx1 + 4, gy1 + 4, scale(wood, 1.2))                     # sill
    # snow piling up on the sill
    if ctx["cond"] in ("snowy", "snowy-rainy"):
        for x in range(gx0 - 3, gx1 + 4):
            if math.sin(x / 3) > -0.3:
                c.pixel(x, gy1 + 2, (235, 235, 245))
    # raindrops running down the glass
    if ctx["cond"] in ("rainy", "pouring", "lightning-rainy", "snowy-rainy"):
        for i in range(10):
            dx = gx0 + 2 + (i * 37) % (gx1 - gx0 - 3)
            if abs(dx - mx) < 1:
                continue
            dy = gy0 + int((t * (2 + i % 3) + i * 7) % (gy1 - gy0))
            c.pixel(dx, dy, (170, 200, 255))
            if dy - 1 > gy0:
                c.pixel(dx, dy - 1, (90, 120, 180))
    # frost creeping in from the corners when it's freezing out
    temp = w.get("temp")
    if temp is not None and temp <= (32 if w.get("unit") != "celsius" else 0):
        rnd = random.Random(3)
        for cxc, cyc, sx_, sy_ in ((gx0, gy0, 1, 1), (gx1, gy0, -1, 1), (gx0, gy1, 1, -1), (gx1, gy1, -1, -1)):
            for _ in range(28):
                a, b = rnd.randint(0, 7), rnd.randint(0, 7)
                if a + b < 8:
                    c.pixel(cxc + sx_ * a, cyc + sy_ * b, (200, 225, 245) if rnd.random() < 0.6 else (140, 170, 210))
    # the reading on the wall to the left
    deg = "--" if temp is None else f"{round(temp)}°"
    _shadow_text(c, 3, 2, deg, "7x13B", WHITE)
    from .weather_icons import LABELS
    label = LABELS.get(ctx["cond"], ctx["cond"].replace("-", " ").title()).upper()
    c.scroll_text(3, gx0 - 4, 16, label, "4x6", (220, 200, 170), t)


SCENE_SCREENS = {
    "countryside": ("show_countryside", "Countryside scene (rolling hills, a farmhouse, trees in the real wind)",
                    has_scene, render_countryside),
    "lake": ("show_lake", "Lakeside scene (sky reflected in the water, waves, a sailboat, rain ripples)",
             has_scene, render_lake),
    "mountains": ("show_mountains", "Mountain scene (snowcapped peaks and a pine forest)", has_scene, render_mountains),
    "window": ("show_window", "Cozy window (the weather outside; rain on the glass, frost when it's freezing)",
               has_scene, render_window),
}
