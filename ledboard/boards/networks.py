"""Small pixel-art TV network logos (7 px tall) for the info line.

They're drawn in code so they work offline and stay sharp at LED size; an unknown network
returns None and the caller falls back to plain text.
"""
from __future__ import annotations

import math
import re

from PIL import Image, ImageDraw

from .. import fonts

H = 7
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
ESPN_RED = (235, 20, 20)
FOX_BLUE = (40, 120, 255)

_cache: dict = {}


# ---------------------------------------------------------------- building blocks
def _text(s, color, font="5x7", bold=False, italic=False):
    """Rendered text trimmed to its ink, optionally fake-bold (double struck) and slanted."""
    img = fonts.get(font).render(s, color)
    box = img.getbbox()
    if not box:
        return Image.new("RGBA", (1, H))
    img = img.crop((box[0], 0, box[2], img.height))
    if bold and len(s) == 1:                  # double striking runs whole words together at 5x7
        b = Image.new("RGBA", (img.width + 1, img.height))
        b.paste(img, (0, 0), img)
        b.paste(img, (1, 0), img)
        img = b
    if italic:
        img = _slant(img)
    return img


def _slant(img):
    """Shift rows right toward the top (a 1-in-3 italic)."""
    h = img.height
    extra = (h - 1) // 3
    out = Image.new("RGBA", (img.width + extra, h))
    for y in range(h):
        row = img.crop((0, y, img.width, y + 1))
        out.paste(row, ((h - 1 - y) // 3, y), row)
    return out


def _bitmap(rows, palette):
    """Image from strings: each char is a palette key ('.' = transparent)."""
    w = max(len(r) for r in rows)
    img = Image.new("RGBA", (w, len(rows)))
    for y, r in enumerate(rows):
        for x, ch in enumerate(r):
            if ch in palette:
                img.putpixel((x, y), palette[ch] + (255,))
    return img


def _box(inner, bg, pad=1, radius=1):
    """`inner` on a filled (rounded) box, H tall."""
    w = inner.width + 2 * pad
    img = Image.new("RGBA", (w, H))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, w - 1, H - 1), radius=radius, fill=bg + (255,))
    img.paste(inner, (pad, (H - inner.height + 1) // 2), inner)
    return img


def _ring(inner):
    """`inner` inside a rounded white outline (ABC-style)."""
    w = inner.width + 4
    img = Image.new("RGBA", (w, H))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, w - 1, H - 1), radius=3, outline=WHITE + (255,))
    img.paste(inner, (2, (H - inner.height + 1) // 2), inner)
    return img


def _row(*parts, gap=1):
    """Join images left to right, vertically centered in H."""
    parts = [p for p in parts if p is not None]
    w = sum(p.width for p in parts) + gap * (len(parts) - 1)
    img = Image.new("RGBA", (w, H))
    x = 0
    for p in parts:
        img.paste(p, (x, (H - p.height + 1) // 2), p)
        x += p.width + gap
    return img


# ---------------------------------------------------------------- icons
def _peacock_fan():
    """NBC-style six-color fan."""
    cols = [(252, 183, 22), (244, 121, 32), (238, 23, 59), (102, 45, 145), (0, 137, 208), (0, 166, 81)]
    w = 11
    img = Image.new("RGBA", (w, H))
    cx, cy = 5, 7.2
    for y in range(H):
        for x in range(w):
            dx, dy = x - cx, cy - y
            d = math.hypot(dx, dy)
            if 2.3 <= d <= 6.6 and dy > 0:
                ang = math.degrees(math.atan2(dy, -dx))         # 0 = left, 180 = right
                i = min(5, max(0, int(ang / 30)))
                img.putpixel((x, y), cols[i] + (255,))
    for x, y in ((5, 5), (5, 6), (4, 6), (6, 6)):              # white body
        img.putpixel((x, y), WHITE + (255,))
    return img


CBS_EYE = ["...WWW...",
           ".WW...WW.",
           "W..WWW..W",
           "W.WWBWW.W",
           "W..WWW..W",
           ".WW...WW.",
           "...WWW..."]

APPLE = ["...W.",
         "..W..",
         ".W.W.",
         "WWWWW",
         "WWWW.",
         "WWWWW",
         ".W.W."]


def _espn(suffix="", suffix_col=ESPN_RED):
    return _row(_text("ESPN", ESPN_RED, bold=True, italic=True),
                _text(suffix, suffix_col, bold=True, italic=True) if suffix else None, gap=0)


def _prime():
    blue = (0, 168, 225)
    word = fonts.get("tom-thumb").render("prime", blue)
    img = Image.new("RGBA", (word.width + 1, H))
    img.paste(word, (0, 0), word)
    d = ImageDraw.Draw(img)
    d.line((3, 6, word.width - 2, 6), fill=blue + (255,))     # the smile
    img.putpixel((word.width - 1, 5), blue + (255,))
    return img


def _peacock():
    word = _text("peacock", WHITE, font="tom-thumb")
    dots = Image.new("RGBA", (1, H))
    for y, c in ((0, (252, 183, 22)), (2, (238, 23, 59)), (4, (102, 45, 145)), (6, (0, 137, 208))):
        dots.putpixel((0, y), c + (255,))
    return _row(word, dots, gap=1)


BUILDERS = [
    (r"ESPN\+|ESPNPLUS", lambda: _espn("+", (255, 200, 0))),
    (r"ESPN2", lambda: _espn("2")),
    (r"ESPNU", lambda: _espn("U")),
    (r"ESPNEWS", lambda: _espn("EWS")),
    (r"ESPN", lambda: _espn()),
    (r"ABC", lambda: _ring(_text("abc", WHITE, font="4x6"))),
    (r"CBSSN|CBSSPORTSNET", lambda: _row(_bitmap(CBS_EYE, {"W": WHITE, "B": BLACK}), _text("SN", WHITE, bold=True))),
    (r"CBS", lambda: _row(_bitmap(CBS_EYE, {"W": WHITE, "B": BLACK}), _text("CBS", WHITE, bold=True))),
    (r"PARAMOUNT", lambda: _text("P+", (40, 110, 255), bold=True)),
    (r"NBCSN|NBCSPORTS", lambda: _row(_peacock_fan(), _text("NBCS", WHITE, bold=True))),
    (r"NBC", lambda: _row(_peacock_fan(), _text("NBC", WHITE, bold=True))),
    (r"PEACOCK", _peacock),
    (r"FS1", lambda: _row(_text("FS", WHITE), _text("1", FOX_BLUE, bold=True), gap=0)),
    (r"FS2", lambda: _row(_text("FS", WHITE), _text("2", FOX_BLUE, bold=True), gap=0)),
    (r"FOX", lambda: _text("FOX", WHITE)),
    (r"NFLNET|NFLN$|NFLNETWORK", lambda: _row(_box(_text("NFL", WHITE, font="4x6"), (1, 51, 160)), _text("NET", WHITE))),
    (r"NFL\+|NFLPLUS", lambda: _row(_box(_text("NFL", WHITE, font="4x6"), (1, 51, 160)), _text("+", (213, 10, 10), bold=True))),
    (r"REDZONE", lambda: _text("REDZONE", (230, 30, 30), font="4x6")),
    (r"PRIME|AMAZON", _prime),
    (r"APPLE", lambda: _row(_bitmap(APPLE, {"W": WHITE}), _text("tv", WHITE, bold=True))),
    (r"NETFLIX", lambda: _text("NETFLIX", (229, 9, 20), font="4x6")),
    (r"TNT", lambda: _text("TNT", WHITE, bold=True)),
    (r"TBS", lambda: _text("tbs", (80, 150, 255), bold=True)),
    (r"TRUTV", lambda: _text("truTV", WHITE)),
    (r"USA", lambda: _box(_text("USA", WHITE, font="4x6"), (20, 60, 200))),
    (r"MAX$|^MAX|HBOMAX", lambda: _text("max", (60, 110, 255), bold=True)),
    (r"MLBN|MLBNETWORK|MLBNET", lambda: _row(_box(_text("MLB", WHITE, font="4x6"), (0, 45, 114)), _text("N", (213, 0, 50), bold=True))),
    (r"MLBTV", lambda: _row(_box(_text("MLB", WHITE, font="4x6"), (0, 45, 114)), _text("TV", WHITE))),
    (r"NHLN|NHLNETWORK", lambda: _row(_box(_text("NHL", WHITE, font="4x6"), (60, 60, 60)), _text("N", WHITE, bold=True))),
    (r"NBATV", lambda: _row(_box(_text("NBA", WHITE, font="4x6"), (29, 66, 138)), _text("TV", (200, 16, 46), bold=True))),
    (r"SECN|SECNETWORK", lambda: _row(_text("SEC", (255, 200, 0), bold=True), _text("N", WHITE))),
    (r"ACCN|ACCNETWORK", lambda: _row(_text("ACC", WHITE, bold=True), _text("N", (0, 120, 255)))),
    (r"BTN|BIGTEN", lambda: _text("B1G", (80, 140, 255), bold=True)),
]
_COMPILED = [(re.compile(p), b) for p, b in BUILDERS]


def normalize(name):
    return re.sub(r"[^A-Z0-9+]", "", (name or "").upper())


def network_logo(name):
    """Pixel logo (RGBA, 7 px tall) for a TV network name, or None if we don't have one."""
    key = normalize(name)
    if not key:
        return None
    if key in _cache:
        return _cache[key]
    img = None
    for rx, build in _COMPILED:
        if rx.search(key):
            img = build()
            break
    _cache[key] = img
    return img
