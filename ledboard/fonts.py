"""Minimal BDF bitmap-font loader and renderer.

Rendering is done pixel-exact (no anti-aliasing), which is what you want on an
LED matrix. Glyphs are cached as 1-bit PIL masks and whole strings are cached
as RGBA images, so drawing text every frame is cheap.
"""
from __future__ import annotations

import math
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
TTF_DIR = os.path.join(FONT_DIR, "ttf")

# Scalable fonts (all SIL Open Font License, see fonts/ttf/LICENSES.txt):
# key: (label, file, variable-font weight to use or None). True pixel fonts are listed in
# PIXEL_GRID: they only look clean at multiples of their design size.
SCALABLE = {
    "bebas": ("Bebas Neue - tall condensed", "BebasNeue-Regular.ttf", None),
    "anton": ("Anton - heavy condensed", "Anton-Regular.ttf", None),
    "oswald": ("Oswald - condensed", "Oswald[wght].ttf", "SemiBold"),
    "bigshoulders": ("Big Shoulders - stadium", "BigShouldersDisplay[wght].ttf", "Bold"),
    "teko": ("Teko - sporty", "Teko[wght].ttf", "SemiBold"),
    "orbitron": ("Orbitron - futuristic", "Orbitron[wght].ttf", "Bold"),
    "dseg7": ("DSEG7 - 7-segment digital clock", "DSEG7Classic-Bold.ttf", None),
    "dseg14": ("DSEG14 - 14-segment display", "DSEG14Classic-Bold.ttf", None),
    "doto": ("Doto - dot matrix", "Doto[ROND,wght].ttf", "Black"),
    "pixelify": ("Pixelify Sans - pixel", "PixelifySans[wght].ttf", "SemiBold"),
    "pressstart": ("Press Start 2P - 8-bit arcade", "PressStart2P-Regular.ttf", None),
    "vt323": ("VT323 - retro terminal", "VT323-Regular.ttf", None),
    "silkscreen": ("Silkscreen - tiny pixel", "Silkscreen-Regular.ttf", None),
    "sharetech": ("Share Tech Mono - tech", "ShareTechMono-Regular.ttf", None),
    "righteous": ("Righteous - rounded retro", "Righteous-Regular.ttf", None),
}


PIXEL_GRID = {"pressstart": 8, "silkscreen": 8}


class TTFFont:
    """A scalable font rendered at one pixel size, with the same interface as BDFFont.

    `ref` is the set of characters whose ink defines the line box, so lines are as tight
    as possible (digits only for a clock, letters with descenders for a date).
    `smooth` keeps anti-aliased edges; otherwise pixels are on/off for crisp LEDs.
    """

    def __init__(self, key, size, ref="Ay0g", smooth=False):
        label, file, weight = SCALABLE[key]
        self.name = f"{key}:{size}"
        self.font = ImageFont.truetype(os.path.join(TTF_DIR, file), size)
        if weight:
            try:
                self.font.set_variation_by_name(weight)
            except Exception:
                pass
        self.smooth = smooth
        boxes = [self.font.getbbox(ch) for ch in ref if ch.strip()]
        self.top = min(b[1] for b in boxes)
        bottom = max(b[3] for b in boxes)
        self.height = max(1, bottom - self.top)
        self.ascent = self.font.getmetrics()[0] - self.top
        self.descent = self.height - self.ascent

    def width(self, text: str) -> int:
        if not text:
            return 0
        b = self.font.getbbox(text)
        return max(1, math.ceil(max(self.font.getlength(text), b[2])) - min(0, b[0]))

    def render(self, text: str, color) -> Image.Image:
        return _render_ttf(self, text, tuple(color))


@lru_cache(maxsize=512)
def _render_ttf(font: "TTFFont", text: str, color) -> Image.Image:
    w = max(1, font.width(text))
    b = font.font.getbbox(text) if text else (0, 0, 0, 0)
    mask = Image.new("L", (w, font.height), 0)
    d = ImageDraw.Draw(mask)
    d.fontmode = "L" if font.smooth else "1"
    d.text((-min(0, b[0]), -font.top), text, font=font.font, fill=255)
    img = Image.new("RGBA", (w, font.height), color + (0,))
    img.putalpha(mask)
    return img


class Glyph:
    __slots__ = ("width", "height", "xoff", "yoff", "advance", "mask")

    def __init__(self, width, height, xoff, yoff, advance, rows):
        self.width, self.height = width, height
        self.xoff, self.yoff, self.advance = xoff, yoff, advance
        self.mask = None
        if width > 0 and height > 0:
            m = Image.new("1", (width, height), 0)
            px = m.load()
            for y, bits in enumerate(rows):
                for x in range(width):
                    if bits & (1 << (len_bits(rows, width) - 1 - x)):
                        px[x, y] = 1
            self.mask = m


def len_bits(rows, width):
    # BDF rows are padded to whole bytes
    return ((width + 7) // 8) * 8


class BDFFont:
    def __init__(self, path: str):
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.glyphs: dict[int, Glyph] = {}
        self.ascent = 0
        self.descent = 0
        self._parse(path)
        self.height = self.ascent + self.descent
        self._fallback = self.glyphs.get(ord("?"))

    def _parse(self, path):
        with open(path, "r", encoding="latin-1") as f:
            lines = f.read().splitlines()
        i, n = 0, len(lines)
        enc = adv = None
        bbx = (0, 0, 0, 0)
        font_bbx = (0, 0, 0, 0)
        while i < n:
            parts = lines[i].split()
            i += 1
            if not parts:
                continue
            key = parts[0]
            if key == "FONT_ASCENT":
                self.ascent = int(parts[1])
            elif key == "FONT_DESCENT":
                self.descent = int(parts[1])
            elif key == "FONTBOUNDINGBOX":
                font_bbx = tuple(int(p) for p in parts[1:5])
            elif key == "STARTCHAR":
                enc, adv, bbx = None, None, font_bbx
            elif key == "ENCODING":
                enc = int(parts[1])
            elif key == "DWIDTH":
                adv = int(parts[1])
            elif key == "BBX":
                bbx = tuple(int(p) for p in parts[1:5])
            elif key == "BITMAP":
                rows = []
                while i < n and lines[i].strip() != "ENDCHAR":
                    rows.append(int(lines[i].strip() or "0", 16))
                    i += 1
                if enc is not None and enc >= 0:
                    w, h, xo, yo = bbx
                    self.glyphs[enc] = Glyph(w, h, xo, yo, adv if adv is not None else w, rows)
        if not self.ascent:
            self.ascent = font_bbx[1] + font_bbx[3]
            self.descent = -font_bbx[3]

    def glyph(self, ch: str) -> Glyph | None:
        g = self.glyphs.get(ord(ch))
        if g is None:
            g = self._fallback
        return g

    def width(self, text: str) -> int:
        return sum(g.advance for g in (self.glyph(c) for c in text) if g)

    def render(self, text: str, color) -> Image.Image:
        return _render_cached(self, text, tuple(color))


@lru_cache(maxsize=512)
def _render_cached(font: BDFFont, text: str, color) -> Image.Image:
    w = max(1, font.width(text))
    img = Image.new("RGBA", (w, font.height), (0, 0, 0, 0))
    solid = Image.new("RGBA", (w, font.height), color + (255,))
    x = 0
    for ch in text:
        g = font.glyph(ch)
        if not g:
            continue
        if g.mask is not None:
            top = font.ascent - (g.height + g.yoff)
            img.paste(solid.crop((0, 0, g.width, g.height)), (x + g.xoff, top), g.mask)
        x += g.advance
    return img


_fonts: dict[str, BDFFont] = {}


def get(name: str):
    """Load a font by name.

    Bitmap fonts: '4x6', '5x8', '6x10', '7x13B', '10x20', 'tom-thumb', ...
    Scalable fonts: 'ttf:<key>:<pixel size>[:<ref chars>][:smooth]', e.g. 'ttf:bebas:24:0123456789:'.
    """
    if name not in _fonts:
        if name.startswith("ttf:"):
            parts = name.split(":")
            key, size = parts[1], int(parts[2])
            smooth = parts[-1] == "smooth"
            ref = ":".join(parts[3:-1] if smooth else parts[3:]) or "Ay0g"
            _fonts[name] = TTFFont(key, size, ref=ref, smooth=smooth)
        else:
            path = name if os.path.isfile(name) else os.path.join(FONT_DIR, name + ".bdf")
            _fonts[name] = BDFFont(path)
    return _fonts[name]


def scalable(key, size, ref="Ay0g", smooth=False):
    """Shortcut for get('ttf:...')."""
    return get(f"ttf:{key}:{int(size)}:{ref}" + (":smooth" if smooth else ""))


def available() -> list[str]:
    return sorted(os.path.splitext(f)[0] for f in os.listdir(FONT_DIR) if f.endswith(".bdf"))
