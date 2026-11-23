"""Animated weather radar for the Weather board: the last hour of radar over a dark map, centered on you.

Radar:
  * NEXRAD (US): Iowa State University's Iowa Environmental Mesonet composite of the National Weather
    Service radars, a new frame every 5 minutes, in the classic radar colors. Free, no key.
  * RainViewer (worldwide): free radar mosaic API (a frame every 10 minutes).
Map: CARTO's dark map tiles (no labels), made a little brighter so coasts and roads show on LEDs.

Everything is downloaded in the board's update thread and turned into ready-made frames (map + radar), so
drawing a frame is just a paste.
"""
from __future__ import annotations

import io
import logging
import math
import time

import requests
from PIL import Image, ImageEnhance

log = logging.getLogger(__name__)

IEM_TILE = "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/{layer}/{z}/{x}/{y}.png"
RAINVIEWER_MAPS = "https://api.rainviewer.com/public/weather-maps.json"
CARTO_TILE = "https://a.basemaps.cartocdn.com/{style}/{z}/{x}/{y}.png"
ESRI_TILE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
MAPS = {"satellite": "Satellite (real colors: land, forests, lakes; Esri World Imagery)",
        "streets": "Streets in color (roads, parks, water; CARTO Voyager, no labels)",
        "dark": "Dark (faint coasts and roads only; CARTO Dark Matter)",
        "none": "No map (radar on black)"}
UA = {"User-Agent": "LEDBoard weather radar (Raspberry Pi LED matrix)"}

SOURCES = {"auto": "Automatic (NEXRAD in the US, RainViewer elsewhere)",
           "nexrad": "NEXRAD, US only (Iowa State Mesonet; every 5 min, classic radar colors)",
           "rainviewer": "RainViewer, worldwide (every 10 min)"}
AREAS = {"40": "About 40 miles across (your town)", "100": "About 100 miles across",
         "250": "About 250 miles across (default)", "500": "About 500 miles across",
         "1000": "About 1,000 miles across (the region)"}
MAX_ZOOM = {"nexrad": 9, "rainviewer": 7}
FRAMES = 7                      # the last hour: 7 frames, 10 minutes apart


def in_conus(lat, lon):
    return 24 <= lat <= 50 and -126 <= lon <= -66


def _px(lat, lon, z):
    """Web Mercator pixel position (256 px tiles) of a point at zoom z."""
    n = 256 * 2 ** z
    lat = max(-85.0, min(85.0, lat))
    r = math.radians(lat)
    return (lon + 180) / 360 * n, (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n


def view(lat, lon, width_km, W, H, max_zoom):
    """(zoom, left, top, source width, source height) of the area to show."""
    km_per_px0 = 40075.0 * math.cos(math.radians(lat)) / 256   # at zoom 0
    z = int(math.floor(math.log2(max(1e-6, km_per_px0 * 256 / width_km))))   # >= 256 px across
    z = max(2, min(max_zoom, z))
    sw = width_km / (km_per_px0 / 2 ** z)
    sh = sw * H / W
    cx, cy = _px(lat, lon, z)
    return z, cx - sw / 2, cy - sh / 2, sw, sh


class Radar:
    def __init__(self):
        self.frames = []          # [(label, RGB image)] oldest first
        self.rain = False         # is there any rain / snow in view in the newest frame
        self.source = None
        self.updated = 0.0
        self.error = None
        self._tiles = {}          # url -> image (map tiles and RainViewer's timestamped tiles never change)
        self._base = (None, None)

    # -- downloading ------------------------------------------------------------------
    def _tile(self, url, keep=True):
        if keep and url in self._tiles:
            return self._tiles[url]
        try:
            r = requests.get(url, headers=UA, timeout=10)
            if r.status_code == 404:
                img = None
            else:
                r.raise_for_status()
                if not r.headers.get("Content-Type", "image").startswith("image"):
                    raise OSError(f"not a picture ({r.headers.get('Content-Type')})")
                img = Image.open(io.BytesIO(r.content)).convert("RGBA")
        except (requests.RequestException, OSError) as e:
            log.debug("radar tile %s: %s", url, e)
            return None
        if keep:
            if len(self._tiles) > 400:
                self._tiles.clear()
            self._tiles[url] = img
        return img

    def _stitch(self, url_for, v, W, H, keep=True, empty_ok=True):
        """Download the tiles covering the view and turn them into one W x H picture (RGBA)."""
        z, left, top, sw, sh = v
        n = 2 ** z
        tx0, tx1 = int(math.floor(left / 256)), int(math.floor((left + sw) / 256))
        ty0, ty1 = int(math.floor(top / 256)), int(math.floor((top + sh) / 256))
        big = Image.new("RGBA", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256), (0, 0, 0, 0))
        got = 0
        for ty in range(ty0, ty1 + 1):
            if not 0 <= ty < n:
                continue
            for tx in range(tx0, tx1 + 1):
                img = self._tile(url_for(z, tx % n, ty), keep)
                if img is not None:
                    big.paste(img, ((tx - tx0) * 256, (ty - ty0) * 256))
                    got += 1
        if not got and not empty_ok:
            return None
        ox, oy = left - tx0 * 256, top - ty0 * 256
        crop = big.crop((round(ox), round(oy), round(ox + sw), round(oy + sh)))
        return crop.resize((W, H), Image.LANCZOS if sw >= W else Image.BILINEAR)

    def _basemap(self, v, W, H, lat, lon, style="satellite"):
        key = (round(lat, 3), round(lon, 3), v[0], round(v[3]), W, H, style)
        if self._base[0] == key:
            return self._base[1]
        if style == "none":
            self._base = (key, Image.new("RGB", (W, H), (0, 0, 0)))
            return self._base[1]
        if style == "satellite":
            url_for = lambda z, x, y: ESRI_TILE.format(z=z, x=x, y=y)          # noqa: E731
        else:
            name = "rastertiles/voyager_nolabels" if style == "streets" else "dark_nolabels"
            url_for = lambda z, x, y: CARTO_TILE.format(style=name, z=z, x=x, y=y)   # noqa: E731
        img = self._stitch(url_for, v, W, H, empty_ok=False)
        if img is None:
            base = Image.new("RGB", (W, H), (0, 0, 0))
        elif style == "satellite":
            # real colors, darker and a little richer, so the radar's colors stand out on top
            base = ImageEnhance.Color(img.convert("RGB")).enhance(1.35)
            base = ImageEnhance.Brightness(base).enhance(0.5)
        elif style == "streets":
            # a light map turned into a night map: plain land goes black, water and parks keep their colors
            # (richer), and roads (lighter than the land) become a soft gray
            from PIL import ImageChops
            h, sat, val = img.convert("RGB").convert("HSV").split()
            ground = sorted(val.getdata())[len(val.getdata()) // 2]
            sat = sat.point(lambda x: min(255, x * 4))
            colored = sat.point(lambda x: 0 if x < 30 else int(20 + x * 0.42))
            roads = val.point(lambda x: 70 if x > ground + 4 else 0)
            base = Image.merge("HSV", (h, sat, ImageChops.lighter(colored, roads))).convert("RGB")
        else:
            base = img.convert("RGB")
            # CARTO's dark map is nearly black: make the land black and stretch what stands out from it
            # (coasts, rivers, roads) up to about 70, so it shows faintly on LEDs under the radar
            vals = sorted(base.convert("L").getdata())
            ground, hi = vals[len(vals) // 2], vals[int(len(vals) * 0.985)]
            if hi - ground < 4:
                base = Image.new("RGB", (W, H), (0, 0, 0))
            else:
                k = min(8.0, 70.0 / (hi - ground))
                base = Image.eval(base, lambda p: max(0, min(255, int((p - ground) * k))))
        self._base = (key, base)
        return base

    @staticmethod
    def _overlay(base, radar):
        """The radar on the map; light rain made a bit more solid so it shows on LEDs."""
        out = base.copy()
        if radar is not None:
            a = radar.getchannel("A").point(lambda p: 0 if p < 12 else min(255, int(p * 1.6) + 40))
            out.paste(radar.convert("RGB"), (0, 0), a)
        return out

    @staticmethod
    def _has_rain(radar):
        if radar is None:
            return False
        return sum(1 for p in radar.getchannel("A").getdata() if p > 60) >= 3

    def refresh(self, lat, lon, width_km, source, W, H, map_style="satellite"):
        lat, lon = float(lat), float(lon)
        if source == "auto":
            source = "nexrad" if in_conus(lat, lon) else "rainviewer"
        v = view(lat, lon, width_km, W, H, MAX_ZOOM[source])
        base = self._basemap(v, W, H, lat, lon, map_style)
        layers = []          # (label, url_for, keep)
        if source == "nexrad":
            # 'nexrad-n0q-900913' is the newest composite; '-m10m' etc. are 10, 20... minutes before it
            for mins in range((FRAMES - 1) * 10, -1, -10):
                layer = "nexrad-n0q-900913" + (f"-m{mins:02d}m" if mins else "")
                stamp = int(time.time() // 300)                    # these change every 5 minutes
                layers.append(("NOW" if not mins else f"-{mins}M",
                               lambda z, x, y, layer=layer, stamp=stamp: IEM_TILE.format(layer=layer, z=z, x=x, y=y)
                               + f"?_={stamp}", True))
        else:
            r = requests.get(RAINVIEWER_MAPS, headers=UA, timeout=10)
            r.raise_for_status()
            j = r.json()
            host = j.get("host") or "https://tilecache.rainviewer.com"
            past = ((j.get("radar") or {}).get("past") or [])[-FRAMES:]
            if not past:
                raise RuntimeError("RainViewer sent no radar frames")
            newest = past[-1].get("time") or time.time()
            for f in past:
                ago = round((newest - (f.get("time") or newest)) / 60)
                path = f.get("path") or ""
                layers.append(("NOW" if not ago else f"-{ago}M",
                               lambda z, x, y, path=path: f"{host}{path}/256/{z}/{x}/{y}/{self.rv_color}/1_1.png", True))
        frames, last, got_any = [], None, False
        for label, url_for, keep in layers:
            radar = self._stitch(url_for, v, W, H, keep, empty_ok=False)
            got_any = got_any or radar is not None
            frames.append((label, self._overlay(base, radar)))
            last = radar
        if not got_any:
            if source == "rainviewer" and self.rv_color != 2:
                self.rv_color = 2              # that color scheme isn't offered (any more): RainViewer's default
                return self.refresh(lat, lon, width_km, "rainviewer", W, H, map_style)
            raise RuntimeError(f"couldn't download the {'NEXRAD' if source == 'nexrad' else 'RainViewer'} radar")
        self.frames, self.rain, self.source = frames, self._has_rain(last), source
        self.updated, self.error = time.time(), None

    rv_color = 4          # RainViewer's 'The Weather Channel' colors (green / yellow / red); 2 if not offered


# -- the screen --------------------------------------------------------------------------------
STEP, HOLD = 0.4, 1.6     # seconds per frame, and on the newest one


def has_radar(w, cfg):
    r = w.get("radar")
    return bool(r and r.frames) and (r.rain or not cfg.get("radar_only_rain"))


def render_radar(c, w, t, cfg):
    r = w.get("radar")
    if not r or not r.frames:
        return
    W, H = c.width, c.height
    n = len(r.frames)
    cycle = STEP * (n - 1) + HOLD
    k = int(min(n - 1, (t % cycle) / STEP))
    label, img = r.frames[k]
    c.paste(img, 0, 0)
    # you are here: a blinking dot with a dark ring
    cx, cy = W // 2, H // 2
    c.rect(cx - 1, cy - 1, cx + 1, cy + 1, (0, 0, 0))
    on = (t * 2) % 2 < 1.3
    c.pixel(cx, cy, (255, 255, 255) if on else (255, 60, 60))
    # time label, top left
    lw = c.text_width(label, "4x6")
    c.rect(0, 0, lw + 1, 6, (0, 0, 0))
    c.text(1, 0, label, "4x6", (255, 255, 255) if label == "NOW" else (170, 170, 170))
    # timeline: one dot per frame, bottom right
    x = W - 2 * n
    c.rect(x - 1, H - 3, W - 1, H - 1, (0, 0, 0))
    for i in range(n):
        c.pixel(x + i * 2, H - 2, (255, 255, 255) if i == k else (70, 70, 70))
    if not r.rain and k == n - 1:
        msg = "NO RAIN NEARBY"
        mw = c.text_width(msg, "4x6")
        mx = (W - mw) // 2
        c.rect(mx - 1, H - 7, mx + mw, H - 1, (0, 0, 0))
        c.text(mx, H - 6, msg, "4x6", (120, 160, 200))
