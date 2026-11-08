"""Minimal BDF bitmap-font loader and renderer.

Rendering is done pixel-exact (no anti-aliasing), which is what you want on an
LED matrix. Glyphs are cached as 1-bit PIL masks and whole strings are cached
as RGBA images, so drawing text every frame is cheap.
"""
from __future__ import annotations

import os
from functools import lru_cache

from PIL import Image

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")


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


def get(name: str) -> BDFFont:
    """Load a font by short name ('4x6', '5x8', '6x10', '7x13B', 'tom-thumb'...)."""
    if name not in _fonts:
        path = name if os.path.isfile(name) else os.path.join(FONT_DIR, name + ".bdf")
        _fonts[name] = BDFFont(path)
    return _fonts[name]


def available() -> list[str]:
    return sorted(os.path.splitext(f)[0] for f in os.listdir(FONT_DIR) if f.endswith(".bdf"))
