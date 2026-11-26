"""Extra layouts for the Now Playing (Spotify) board. 'classic' is drawn in spotify.py.

Everything is made from the album art the board already has (32 x 32), so no extra downloads:
the blurred backdrop, the album's colors and the spinning record label all come from it.
"""
from __future__ import annotations

import colorsys
import math

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from ..gfx import GRAY, WHITE, fmt_ms, readable, scale

GREEN = (30, 215, 96)

LAYOUTS = {
    "classic": "Classic (album art, title, artist, progress bar)",
    "backdrop": "Backdrop (the album art, blurred, fills the board behind the text)",
    "colors": "Album colors (title, artist and progress bar take the album's colors)",
    "vinyl": "Vinyl (the album art spins as a record while it plays)",
    "visualizer": "Visualizer (animated equalizer bars in the album's colors)",
    "bigtitle": "Big title (a large scrolling song title across the whole board)",
}


# -- made from the album art (cached per picture) -------------------------------------
def _cache(board):
    cache = board.__dict__.setdefault("_layout_cache", {})
    if len(cache) > 60:
        cache.clear()
    return cache


def palette(board, art):
    """(main, accent): the album's most colorful bright colors, made readable on LEDs; Spotify green and
    white when there's no art or it's black and white."""
    if art is None:
        return GREEN, WHITE
    key = ("pal", id(art))
    hit = _cache(board).get(key)
    if hit and hit[0] is art:
        return hit[1]
    rgb = Image.new("RGB", art.size, (0, 0, 0))
    rgb.paste(art, (0, 0), art if art.mode == "RGBA" else None)
    counts = rgb.quantize(colors=8).convert("RGB").getcolors(4096) or []
    scored = []
    for n, col in counts:
        h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in col))
        if v < 0.12:
            continue
        scored.append((n * (0.25 + s) * (0.3 + v), h, s, col))
    scored.sort(reverse=True)
    colorful = [x for x in scored if x[2] > 0.25]
    if not colorful:
        out = (readable(scored[0][3], 0.85) if scored else WHITE, GREEN)
    else:
        main = colorful[0]
        accent = next((x for x in colorful[1:] if min(abs(x[1] - main[1]), 1 - abs(x[1] - main[1])) > 0.08), None)
        acc = readable(accent[3], 0.95) if accent else WHITE
        if sum(acc) < 330 or (acc[2] > acc[0] + acc[1] and acc[2] > 150 and max(acc[0], acc[1]) < 120):
            acc = tuple(round(v + (255 - v) * 0.45) for v in acc)   # deep blues / dark colors: lighten them
        out = (readable(main[3], 0.85), acc)
    _cache(board)[key] = (art, out)
    return out


def backdrop(board, art, W, H):
    """The album art stretched across the board, blurred and dimmed."""
    if art is None:
        return None
    key = ("bg", id(art), W, H)
    hit = _cache(board).get(key)
    if hit is not None and hit[0] is art:
        return hit[1]
    rgb = Image.new("RGB", art.size, (0, 0, 0))
    rgb.paste(art, (0, 0), art if art.mode == "RGBA" else None)
    big = rgb.resize((W, W), Image.BILINEAR)
    top = (W - H) // 2
    band = big.crop((0, top, W, top + H)).filter(ImageFilter.GaussianBlur(4))
    band = ImageEnhance.Color(band).enhance(1.4)
    band = ImageEnhance.Brightness(band).enhance(0.42)
    _cache(board)[key] = (art, band)
    return band


# -- shared bits ---------------------------------------------------------------------
def _shadow(c, x0, x1, y, text, font, color, t):
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1), (-1, 1), (1, -1), (-1, -1)):
        c.scroll_text(x0 + dx, x1 + dx, y + dy, text, font, (0, 0, 0), t)
    c.scroll_text(x0, x1, y, text, font, color, t)


def _bar(board, c, x0, x1, H, n, slide, frac, color, track=None):
    """The progress bar in the chosen style along the bottom; returns the row for the times above it."""
    if board.cfg.get("progress_style", "wavy") == "wavy":
        kw = {"track": track} if track else {}
        c.wavy_progress(x0, x1, H - 4, frac, color, amp=board._wave.value(slide, n["playing"]), **kw)
        return H - 12
    kw = {"bg": track} if track else {}
    c.progress(x0, H - 3, x1, frac, color, **kw)
    return H - 10


def _times(c, x0, x1, y, n, progress, color=GRAY, shadow=False):
    left, right = fmt_ms(progress), fmt_ms(n["duration"]) if n["duration"] else ""
    if shadow:
        _shadow(c, x0, x1, y, left, "4x6", color, 0)
        if right:
            rw = c.text_width(right, "4x6")
            _shadow(c, x1 - rw, x1, y, right, "4x6", color, 0)
    else:
        c.text(x0, y, left, "4x6", color)
        if right:
            c.text_right(x1, y, right, "4x6", color)
    if not n["playing"]:
        if shadow:
            pw = c.text_width("PAUSED", "4x6")
            px = x0 + (x1 - x0 - pw) // 2
            c.rect(px - 1, y - 1, px + pw, y + 6, (0, 0, 0))
        c.text_centered(y, "PAUSED", "4x6", (220, 140, 0), x0, x1)


# -- layouts ---------------------------------------------------------------------------
def render_backdrop(board, c, n, progress, frac, slide, t):
    W, H = c.width, c.height
    bg = backdrop(board, n.get("art"), W, H)
    if bg is not None:
        c.paste(bg, 0, 0)
    art = n.get("art")
    if art is not None:
        c.rect(0, 0, H, H - 1, (0, 0, 0))
        c.paste(art, 0, 0)
    x0 = H + 3
    title, artist = board._colors()
    _shadow(c, x0, W, 1, n["title"], "6x10", title, t)
    _shadow(c, x0, W, 11, n["artist"], "5x8", artist, t)
    ty = _bar(board, c, x0, W - 1, H, n, slide, frac, board.bar_color(n, WHITE), track=(70, 70, 70))
    _times(c, x0, W - 1, ty, n, progress, (220, 220, 220), shadow=True)


def render_colors(board, c, n, progress, frac, slide, t):
    W, H = c.width, c.height
    main, accent = palette(board, n.get("art"))
    for x in range(H, W):                                 # a faint wash of the album's color
        k = 0.16 * (1 - (x - H) / (W - H)) ** 1.5
        if k > 0.01:
            c.line(x, 0, x, H - 1, scale(main, k))
    c.paste(n.get("art"), 0, 0)
    x0 = H + 3
    c.scroll_text(x0, W, 1, n["title"], "6x10", main, t)
    c.scroll_text(x0, W, 11, n["artist"], "5x8", accent, t)
    bar = board.bar_color(n, main)
    ty = _bar(board, c, x0, W - 1, H, n, slide, frac, bar, track=scale(bar, 0.18))
    _times(c, x0, W - 1, ty, n, progress)


def _label(board, art, size):
    """The album art as a round record label (with a spindle hole), cached per picture."""
    key = ("label", id(art), size)
    hit = _cache(board).get(key)
    if hit is not None and hit[0] is art:
        return hit[1]
    img = art.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((0, 0, size - 1, size - 1), fill=255)
    m = size // 2
    d.ellipse((m - 1, m - 1, m, m), fill=0)
    img.putalpha(mask)
    _cache(board)[key] = (art, img)
    return img


def render_vinyl(board, c, n, progress, frac, slide, t):
    W, H = c.width, c.height
    cx = cy = H // 2
    r = H // 2 - 1
    d = c.draw
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(14, 14, 16), outline=(55, 55, 60))
    for gr in (r - 3, r - 5, r - 7):                      # grooves
        d.ellipse((cx - gr, cy - gr, cx + gr, cy + gr), outline=(30, 30, 34))
    spin = progress / 1000 * 200                         # degrees: follows the song, stops when paused
    a = math.radians(-spin * 0.5)                         # a glint that sweeps slowly over the grooves
    gx, gy = cx + (r - 3) * math.cos(a), cy + (r - 3) * math.sin(a)
    d.line((cx + (r - 8) * math.cos(a), cy + (r - 8) * math.sin(a), gx, gy), fill=(90, 90, 100))
    art = n.get("art")
    size = 18
    if art is not None:
        label = _label(board, art, size).rotate(-spin, resample=Image.BICUBIC)
        c.paste(label, cx - size // 2, cy - size // 2)
    else:
        d.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=scale(GREEN, 0.6))
        c.pixel(cx, cy, (0, 0, 0))
    x0 = H + 3
    title, artist = board._colors()
    c.scroll_text(x0, W, 1, n["title"], "6x10", title, t)
    c.scroll_text(x0, W, 11, n["artist"], "5x8", artist, t)
    ty = _bar(board, c, x0, W - 1, H, n, slide, frac, board.bar_color(n, GREEN))
    _times(c, x0, W - 1, ty, n, progress)


def render_visualizer(board, c, n, progress, frac, slide, t):
    W, H = c.width, c.height
    c.paste(n.get("art"), 0, 0)
    x0 = H + 3
    main, accent = palette(board, n.get("art"))
    title, artist = board._colors()
    c.scroll_text(x0, W, 0, n["title"], "6x10", title, t)
    c.scroll_text(x0, W, 10, n["artist"], "5x8", artist, t)
    # equalizer: bars from y_top to the second-to-last row; the last row is a thin progress line
    level = board._wave.value(f"{slide}|eq", n["playing"])   # eases down to a flat line when paused
    y_top, y_bot = 19, H - 3
    span = y_bot - y_top + 1
    bars = (W - x0) // 3
    beat = progress / 1000
    for i in range(bars):
        f = i / max(1, bars - 1)
        v = (0.5 + 0.5 * math.sin(beat * 7.1 + i * 0.9)) * 0.55 + (0.5 + 0.5 * math.sin(beat * 3.3 + i * 2.3)) * 0.45
        v *= 1.0 - 0.35 * f                                # a bit more bass than treble
        hgt = max(1, round((0.12 + 0.88 * v * level) * span))
        x = x0 + i * 3
        col = tuple(round(a + (b - a) * f) for a, b in zip(main, accent))
        c.rect(x, y_bot - hgt + 1, x + 1, y_bot, col)
        if hgt > 2:
            c.pixel(x, y_bot - hgt + 1, scale(col, 1.3))
    c.line(x0, H - 1, W - 1, H - 1, (35, 35, 35))
    end = x0 + int((W - 1 - x0) * frac)
    if end > x0:
        c.line(x0, H - 1, end, H - 1, board.bar_color(n, main))
    if not n["playing"]:
        c.text_right(W - 1, y_top, "PAUSED", "4x6", (220, 140, 0))


def render_bigtitle(board, c, n, progress, frac, slide, t):
    W, H = c.width, c.height
    title, artist = board._colors()
    c.scroll_text(0, W, 0, n["title"], "9x15B", title, t, speed=24)
    c.scroll_text(0, W, 16, n["artist"], "5x8", artist, t)
    label = fmt_ms(progress) + (f" / {fmt_ms(n['duration'])}" if n["duration"] else "")
    if not n["playing"]:
        label = "PAUSED  " + label
    c.text_right(W - 1, H - 7, label, "4x6", GRAY if n["playing"] else (220, 140, 0))
    lw = c.text_width(label, "4x6")
    c.progress(0, H - 4, W - lw - 5, frac, board.bar_color(n, GREEN), height=2)


RENDERERS = {"backdrop": render_backdrop, "colors": render_colors, "vinyl": render_vinyl,
             "visualizer": render_visualizer, "bigtitle": render_bigtitle}
