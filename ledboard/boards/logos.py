"""Team logos prepared for an LED matrix.

* Equal size: in a matchup, both logos are scaled to the same visual area (equal
  bounding-box area) instead of each simply filling its box, so a round logo no longer
  looks bigger than a wide one.
* LED colors: tiny logos come out muddy on a matrix -- neighbouring colors blend into
  in-between shades when shrunk, dark team colors (e.g. Packers green #203731, navy) sit
  near black, and soft edges turn into dim fringe pixels. 'Enhanced' logos snap every
  pixel to the logo's own key colors (taken from the full-size image), lift dark colors
  and add a little saturation (hue preserved), show black detail as dark grey so it
  doesn't vanish on the black background, and use crisp edges.
"""
from __future__ import annotations

import colorsys
import math

from PIL import Image

from ..gfx import trim_alpha

_trimmed: dict = {}
_palettes: dict = {}
_cache: dict = {}


def _source(board, team):
    url = team.get("logo_url") if team else None
    if not url:
        return None
    return getattr(board, "logo_src", {}).get(url) or board.logos.get(url)


def _trim(src):
    key = id(src)
    if key not in _trimmed:
        if len(_trimmed) > 400:
            _trimmed.clear()
        _trimmed[key] = trim_alpha(src)
    return _trimmed[key]


def _fit(img, w, h):
    a = img.width / img.height
    return (w, w / a) if a >= w / h else (h * a, h)


def _seg_dist(c, a, b):
    """Distance from color c to the line segment a-b (in RGB)."""
    ab = [y - x for x, y in zip(a, b)]
    ac = [y - x for x, y in zip(a, c)]
    den = sum(v * v for v in ab) or 1
    t = max(0.0, min(1.0, sum(x * y for x, y in zip(ac, ab)) / den))
    return math.sqrt(sum((a[i] + ab[i] * t - c[i]) ** 2 for i in range(3)))


def palette(src, n=6):
    """The logo's real colors, strongest first.

    Shrinking blends neighbouring colors at their edges (green + gold -> olive, gold + white
    -> cream). Those blends are dropped: a candidate that sits on the line between two
    stronger colors is an edge blend, not a team color.
    """
    key = id(src)
    if key in _palettes:
        return _palettes[key]
    rgba = src.convert("RGBA")
    px = [p[:3] for p in rgba.getdata() if p[3] >= 250]
    pal = []
    if px:
        tmp = Image.new("RGB", (len(px), 1))
        tmp.putdata(px)
        q = tmp.quantize(colors=12, method=Image.Quantize.MEDIANCUT, kmeans=2)
        raw = q.getpalette()
        cands = [(count, tuple(raw[i * 3: i * 3 + 3])) for count, i in sorted(q.getcolors(), reverse=True)]
        kept = []                                          # (count, color)
        for count, col in cands:
            if count < len(px) * 0.01:
                continue
            if any(sum((a - b) ** 2 for a, b in zip(col, p)) < 30 ** 2 for _, p in kept):
                continue                                   # near-duplicate of a stronger color
            kept.append((count, col))
        # drop blends: a color lying between two other colors that are both more common
        def is_blend(k):
            cnt, col = kept[k]
            others = [(c2, p) for i, (c2, p) in enumerate(kept) if i != k and c2 > cnt]
            return any(_seg_dist(col, a[1], b[1]) < 24 for i, a in enumerate(others) for b in others[i + 1:])
        pal = [col for k, (cnt, col) in enumerate(kept) if not is_blend(k)][:n]
    _palettes[key] = pal
    return pal


def led_color(c):
    """Make a logo color read well on LEDs: lift dark tones, a touch more saturation, same hue."""
    r, g, b = (x / 255 for x in c)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v < 0.18 and s < 0.6:                  # black / near-black detail -> visible dark grey
        return (55, 55, 60)
    v = v ** 0.6                              # gamma lift: 0.22 -> 0.40, 0.5 -> 0.66, 1.0 -> 1.0
    if s > 0.15:
        s = min(1.0, s * 1.2)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (round(r * 255), round(g * 255), round(b * 255))


def _enhance(img, pal):
    """Snap to the palette (crisp edges, no muddy in-between shades) and apply LED colors."""
    if not pal:
        return img
    out_cols = [led_color(p) for p in pal]
    data = []
    for r, g, b, a in img.getdata():
        if a < 110:
            data.append((0, 0, 0, 0))
            continue
        best, bd = 0, 1 << 30
        for i, (pr, pg, pb) in enumerate(pal):
            d = (r - pr) * (r - pr) * 2 + (g - pg) * (g - pg) * 4 + (b - pb) * (b - pb) * 3
            if d < bd:
                best, bd = i, d
        data.append(out_cols[best] + (255,))
    out = Image.new("RGBA", img.size)
    out.putdata(data)
    return out


def logo_image(board, team, w, h=None, partner=None):
    """`team`'s logo inside a w x h box (centered, transparent padding).

    With `partner` (the other team in the matchup), both logos get the same visual area.
    Colors are LED-enhanced unless the board's 'logo_enhance' setting is off.
    """
    h = h or w
    src = _source(board, team)
    if src is None:
        return None
    psrc = _source(board, partner) if partner else None
    enhance = bool(getattr(board, "cfg", {}).get("logo_enhance", True))
    key = (id(src), id(psrc), w, h, enhance)
    if key in _cache:
        return _cache[key]
    t = _trim(src)
    fw, fh = _fit(t, w, h)
    if psrc is not None:
        pw, ph = _fit(_trim(psrc), w, h)
        target = min(math.sqrt(fw * fh), math.sqrt(pw * ph))      # largest area both can reach
        a = t.width / t.height
        fw = math.sqrt(target * target * a)
        fh = fw / a
        if fw > w or fh > h:                                       # safety: stay inside the box
            k = min(w / fw, h / fh)
            fw, fh = fw * k, fh * k
    size = (max(1, round(fw)), max(1, round(fh)))
    img = t.resize(size, Image.LANCZOS)
    if enhance:
        img = _enhance(img, palette(t))
    box = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    box.paste(img, ((w - size[0]) // 2, (h - size[1]) // 2), img)
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = box
    return box
