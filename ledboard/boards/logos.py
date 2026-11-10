"""Team logos prepared for an LED matrix.

* Equal size: in a matchup, both logos are scaled to the same visual area (equal
  bounding-box area) instead of each simply filling its box, so a round logo no longer
  looks bigger than a wide one.
* LED colors: tiny logos come out muddy on a matrix -- neighbouring colors blend into
  in-between shades when shrunk, dark team colors (e.g. Packers green #203731, navy) sit
  near black, and soft edges turn into dim fringe pixels. 'Enhanced' logos snap every
  pixel to the logo's own key colors (taken from the full-size image), lift dark colors
  and add a little saturation (hue preserved) while keeping black black, and use crisp
  edges.
"""
from __future__ import annotations

import colorsys
import math
import weakref

from PIL import Image

from ..gfx import trim_alpha

# Derived data (trimmed copy, palette) is stored on the source image object itself, and the
# render cache checks it is still the same object -- never keyed by id(), which Python reuses
# after an image is freed (that could hand back another team's logo).
_cache: dict = {}


def _source(board, team):
    url = team.get("logo_url") if team else None
    if not url:
        return None
    return getattr(board, "logo_src", {}).get(url) or board.logos.get(url)


def _trim(src):
    t = getattr(src, "_ledboard_trimmed", None)
    if t is None:
        t = src._ledboard_trimmed = trim_alpha(src)
    return t


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
    cached = getattr(src, "_ledboard_palette", None)
    if cached is not None:
        return cached
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
    src._ledboard_palette = pal
    return pal


def led_color(c):
    """Make a logo color read well on LEDs: lift dark tones, a touch more saturation, same hue."""
    r, g, b = (x / 255 for x in c)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v < 0.18 and s < 0.6:                  # black / near-black stays black
        return (0, 0, 0)
    v = v ** 0.6                              # gamma lift: 0.22 -> 0.40, 0.5 -> 0.66, 1.0 -> 1.0
    if s > 0.15:
        s = min(1.0, s * 1.2)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (round(r * 255), round(g * 255), round(b * 255))


def _enhance(img, pal, led=True):
    """Snap to the palette (crisp edges, no muddy in-between shades); `led` also applies LED colors."""
    if not pal:
        return img
    out_cols = [led_color(p) for p in pal] if led else list(pal)
    whiteish = [min(p) >= 200 for p in pal]
    data = []
    for r, g, b, a in img.getdata():
        if a < 110:
            data.append((0, 0, 0, 0))
            continue
        light = min(r, g, b) > 175
        best, bd = 0, 1 << 30
        for i, (pr, pg, pb) in enumerate(pal):
            d = (r - pr) * (r - pr) * 2 + (g - pg) * (g - pg) * 4 + (b - pb) * (b - pb) * 3
            if whiteish[i] and not light:
                d = d * 5 // 2        # edge blends go to the color, not white: keeps white details thin, not blotchy
            if d < bd:
                best, bd = i, d
        data.append(out_cols[best] + (255,))
    out = Image.new("RGBA", img.size)
    out.putdata(data)
    return out


CRISP_MIN = 18      # boxes at least this big get the crisp palette treatment; smaller ones stay smooth


def _lift(x):
    """Brighten dark colors but leave blacks black: no lift below ~10%, full lift above ~20%."""
    lifted = 255 * (x / 255) ** 0.6
    k = min(1.0, max(0.0, (x - 26) / 26))
    return round(x + (lifted - x) * k)


_LIFT = [_lift(x) for x in range(256)]
_SAT = [min(255, round(x * 1.2)) for x in range(256)]
_ALPHA = [0 if x < 30 else min(255, round(x * 1.35)) for x in range(256)]  # keep thin strokes visible


def _soft_enhance(img):
    """For tiny logos: keep the smooth anti-aliased shapes (they carry the detail at 6-16 px)
    and only make the colors LED-friendly."""
    alpha = img.getchannel("A").point(_ALPHA)
    h, sat, val = img.convert("RGB").convert("HSV").split()
    out = Image.merge("HSV", (h, sat.point(_SAT), val.point(_LIFT))).convert("RGB")
    out.putalpha(alpha)
    return out


def _despeckle(img):
    """Remove lone pixels left over from shrinking busy logos (thin outlines, script lettering, tiny
    details): an opaque pixel whose color none of its 4 neighbours share takes the most common color
    around it (or goes transparent if it's mostly surrounded by nothing)."""
    w, h = img.size
    src = img.load()
    out = img.copy()
    dst = out.load()
    for y in range(h):
        for x in range(w):
            p = src[x, y]
            if p[3] == 0:
                continue
            n4 = [src[x + dx, y + dy] for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)) if 0 <= x + dx < w and 0 <= y + dy < h]
            if any(q[:3] == p[:3] and q[3] for q in n4):
                continue
            n8 = [src[x + dx, y + dy] for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                  if (dx or dy) and 0 <= x + dx < w and 0 <= y + dy < h]
            clear = sum(1 for q in n8 if q[3] == 0)
            if clear >= 5:
                dst[x, y] = (0, 0, 0, 0)
                continue
            counts = {}
            for q in n8:
                if q[3]:
                    counts[q] = counts.get(q, 0) + 1
            if counts:
                dst[x, y] = max(counts, key=counts.get)
    return out


def _strip_white_rim(img):
    """Drop a white outline: white pixels on the logo's edge (next to transparency) that border a
    colored part. Dark-background logos add these outlines; at LED size they turn into blotchy white
    edges. All-white logos / white letters with no color next to them are left alone."""
    w, h = img.size
    src = img.load()
    out = img.copy()
    dst = out.load()

    def white(p):
        return p[3] and min(p[:3]) >= 200

    def colored(p):
        return p[3] and max(p[:3]) - min(p[:3]) > 40

    for y in range(h):
        for x in range(w):
            p = src[x, y]
            if not white(p):
                continue
            n4 = [src[x + dx, y + dy] if 0 <= x + dx < w and 0 <= y + dy < h else (0, 0, 0, 0)
                  for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
            if not any(q[3] == 0 for q in n4):
                continue
            n8 = [src[x + dx, y + dy] for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                  if (dx or dy) and 0 <= x + dx < w and 0 <= y + dy < h]
            if any(colored(q) for q in n8):
                dst[x, y] = (0, 0, 0, 0)
    return out


def logo_image(board, team, w, h=None, partner=None):
    """`team`'s logo inside a w x h box (centered, transparent padding).

    With `partner` (the other team in the matchup), both logos get the same visual area.
    Board settings:
      logo_enhance  LED-friendly colors (default on)
      logo_edges    "auto" (crisp when big, smooth when small), "smooth" or "crisp"
    """
    h = h or w
    src = _source(board, team)
    if src is None:
        return None
    psrc = _source(board, partner) if partner else None
    cfg = getattr(board, "cfg", {})
    enhance = bool(cfg.get("logo_enhance", True))
    edges = cfg.get("logo_edges", "auto")
    clean = cfg.get("logo_detail", "full") == "clean"
    key = (team.get("logo_url"), (partner or {}).get("logo_url"), w, h, enhance, edges, clean)
    hit = _cache.get(key)
    if hit is not None and hit[0]() is src and hit[1]() is psrc:
        return hit[2]
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
    crisp = edges == "crisp" or (edges == "auto" and min(w, h) >= CRISP_MIN)
    if clean:
        # simplified: the logo's 4 main colors, hard edges, and no stray pixels
        img = _despeckle(_strip_white_rim(_enhance(img, palette(t)[:4], led=enhance)))
    elif crisp:
        img = _enhance(img, palette(t), led=enhance)   # hard edges in the logo's exact colors
    elif enhance:
        img = _soft_enhance(img)                       # smooth (anti-aliased) edges, brighter colors
    # smooth + not enhanced: the plain anti-aliased resize
    box = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    box.paste(img, ((w - size[0]) // 2, (h - size[1]) // 2), img)
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = (weakref.ref(src), (lambda: None) if psrc is None else weakref.ref(psrc), box)
    return box
