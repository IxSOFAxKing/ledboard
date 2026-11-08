"""Drawing helpers on top of a PIL image sized to the LED matrix."""
from __future__ import annotations

import colorsys
import hashlib
import io
import logging
import os
import threading

import requests
from PIL import Image, ImageDraw

from . import fonts

log = logging.getLogger(__name__)

WHITE = (255, 255, 255)
GRAY = (120, 120, 120)
DIM = (60, 60, 60)
YELLOW = (255, 200, 0)
RED = (230, 30, 30)
GREEN = (30, 220, 60)


def hex_to_rgb(value, default=WHITE):
    if not value:
        return default
    if isinstance(value, (list, tuple)):
        return tuple(int(v) for v in value[:3])
    v = str(value).lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def readable(color, min_value=0.55):
    """Brighten a color so it shows up on a black LED background."""
    r, g, b = (c / 255 for c in color)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v < min_value:
        v = min_value
    if s < 0.15 and v < 0.8:
        v = 0.8  # dark grays / blacks -> light gray
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def scale(color, factor):
    return tuple(max(0, min(255, int(c * factor))) for c in color)


def fmt_ms(ms):
    s = max(0, int(ms // 1000))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Canvas:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.image = Image.new("RGB", (width, height))
        self.draw = ImageDraw.Draw(self.image)

    def clear(self):
        self.draw.rectangle((0, 0, self.width, self.height), fill=(0, 0, 0))

    # -- text -------------------------------------------------------------
    def text(self, x, y, text, font="5x8", color=WHITE, clip=None):
        """Draw text with its top-left at (x, y). Returns the text width."""
        f = fonts.get(font) if isinstance(font, str) else font
        img = f.render(str(text), color)
        if clip:
            cx0, cx1 = clip
            left = max(0, cx0 - x)
            right = min(img.width, cx1 - x)
            if right <= left:
                return img.width
            self.image.paste(img.crop((left, 0, right, img.height)), (x + left, y), img.crop((left, 0, right, img.height)))
        else:
            self.image.paste(img, (x, y), img)
        return img.width

    def text_width(self, text, font="5x8"):
        f = fonts.get(font) if isinstance(font, str) else font
        return f.width(str(text))

    def text_centered(self, y, text, font="5x8", color=WHITE, x0=0, x1=None):
        x1 = self.width if x1 is None else x1
        w = self.text_width(text, font)
        self.text(x0 + (x1 - x0 - w) // 2, y, text, font, color)

    def text_right(self, x_right, y, text, font="5x8", color=WHITE):
        w = self.text_width(text, font)
        self.text(x_right - w, y, text, font, color)

    def scroll_text(self, x0, x1, y, text, font, color, t, speed=18.0, align="left", pause=1.5, gap=24):
        """Draw text in the region [x0, x1). If it doesn't fit, marquee-scroll it.

        `t` is seconds since the slide started; scrolling begins after `pause`.
        """
        text = str(text)
        w = self.text_width(text, font)
        avail = x1 - x0
        if w <= avail:
            if align == "center":
                x = x0 + (avail - w) // 2
            elif align == "right":
                x = x1 - w
            else:
                x = x0
            self.text(x, y, text, font, color)
            return
        cycle = w + gap
        offset = int(max(0.0, t - pause) * speed) % cycle
        self.text(x0 - offset, y, text, font, color, clip=(x0, x1))
        if w - offset < avail:
            self.text(x0 - offset + cycle, y, text, font, color, clip=(x0, x1))

    # -- shapes -------------------------------------------------------------
    def rect(self, x0, y0, x1, y1, color, fill=True):
        if fill:
            self.draw.rectangle((x0, y0, x1, y1), fill=color)
        else:
            self.draw.rectangle((x0, y0, x1, y1), outline=color)

    def line(self, x0, y0, x1, y1, color):
        self.draw.line((x0, y0, x1, y1), fill=color)

    def pixel(self, x, y, color):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.image.putpixel((x, y), color)

    def progress(self, x0, y, x1, frac, color, bg=DIM, height=2):
        frac = max(0.0, min(1.0, frac or 0.0))
        self.rect(x0, y, x1, y + height - 1, bg)
        end = x0 + int((x1 - x0) * frac)
        if end > x0:
            self.rect(x0, y, end, y + height - 1, color)

    def paste(self, img, x, y):
        if img is None:
            return
        if img.mode == "RGBA":
            self.image.paste(img, (x, y), img)
        else:
            self.image.paste(img, (x, y))


# -- image fetching -------------------------------------------------------------

CACHE_DIR = os.environ.get("LEDBOARD_CACHE", os.path.expanduser("~/.cache/ledboard"))
_img_cache: dict[str, Image.Image] = {}
_img_lock = threading.Lock()


def fit_image(img, w, h, bg=None):
    """Resize to fit inside w x h, keeping aspect ratio, centered on transparent canvas."""
    img = img.convert("RGBA")
    img.thumbnail((w, h), Image.LANCZOS)
    out = Image.new("RGBA", (w, h), bg or (0, 0, 0, 0))
    out.paste(img, ((w - img.width) // 2, (h - img.height) // 2), img)
    return out


def fetch_image(url, w, h, headers=None, verify=True, cache_key=None, disk=True):
    """Download an image and fit it to w x h. Cached in memory and on disk.

    Call this from a board's update() thread, never from render().
    """
    if not url:
        return None
    key = hashlib.sha1(f"{cache_key or url}|{w}x{h}".encode()).hexdigest()
    with _img_lock:
        if key in _img_cache:
            return _img_cache[key]
    path = os.path.join(CACHE_DIR, key + ".png")
    img = None
    if disk and os.path.exists(path):
        try:
            img = Image.open(path).convert("RGBA")
        except Exception:
            img = None
    if img is None:
        try:
            r = requests.get(url, headers=headers or {}, timeout=10, verify=verify)
            r.raise_for_status()
            img = fit_image(Image.open(io.BytesIO(r.content)), w, h)
            if disk:
                os.makedirs(CACHE_DIR, exist_ok=True)
                img.save(path)
        except Exception as e:
            log.warning("image fetch failed %s: %s", url.split("?")[0], e)
            return None
    with _img_lock:
        if len(_img_cache) > 300:
            _img_cache.clear()
        _img_cache[key] = img
    return img
