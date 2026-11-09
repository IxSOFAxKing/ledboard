"""Animated transitions between two frames (old -> new) for board / slide changes."""
from __future__ import annotations

import random

from PIL import Image, ImageDraw

STYLES = {
    "off": "Off (instant cut)",
    "slide": "Slide (new board pushes in from the right)",
    "push-up": "Push up (like a ticker)",
    "wipe": "Wipe (a bright line sweeps across)",
    "fade": "Fade (through black)",
    "dissolve": "Dissolve (pixels switch over randomly)",
    "random": "Random (a different one each time)",
}
CHOICES = [s for s in STYLES if s not in ("off", "random")]

_dissolve_masks: dict = {}


def _ease(f):
    f = max(0.0, min(1.0, f))
    return f * f * (3 - 2 * f)


def _dissolve_rank(size):
    """An 'L' image of random ranks (0-255), the same pattern every time for a given size."""
    m = _dissolve_masks.get(size)
    if m is None:
        rnd = random.Random(42)
        m = Image.new("L", size)
        m.putdata([rnd.randrange(256) for _ in range(size[0] * size[1])])
        _dissolve_masks[size] = m
    return m


def compose(style, old, new, f):
    """Frame `f` (0..1) of the transition from `old` to `new` (both RGB, same size)."""
    W, H = new.size
    e = _ease(f)
    if f >= 1.0:
        return new
    if style == "slide":
        dx = round(W * e)
        out = Image.new("RGB", (W, H))
        out.paste(old, (-dx, 0))
        out.paste(new, (W - dx, 0))
        return out
    if style == "push-up":
        dy = round(H * e)
        out = Image.new("RGB", (W, H))
        out.paste(old, (0, -dy))
        out.paste(new, (0, H - dy))
        return out
    if style == "wipe":
        x = round(W * e)
        out = old.copy()
        if x > 0:
            out.paste(new.crop((0, 0, x, H)), (0, 0))
        if 0 < x < W:
            ImageDraw.Draw(out).line((x, 0, x, H - 1), fill=(255, 255, 255))
        return out
    if style == "fade":                       # out through black, then in: reads better on LEDs than a blend
        k = abs(1 - 2 * e)
        src = old if e < 0.5 else new
        lut = [round(v * k) for v in range(256)] * 3
        return src.point(lut)
    if style == "dissolve":
        cut = round(e * 256)
        mask = _dissolve_rank((W, H)).point(lambda v: 255 if v < cut else 0)
        return Image.composite(new, old, mask)
    return new


class Transition:
    def __init__(self, style, old, started, duration):
        if style == "random":
            style = random.choice(CHOICES)
        self.style, self.old, self.started, self.duration = style, old, started, max(0.05, duration)

    def frame(self, new, now):
        """Composited frame, or None when the transition is over."""
        f = (now - self.started) / self.duration
        if f >= 1.0:
            return None
        return compose(self.style, self.old, new, f)
