"""Ambient animations: fireplace, Matrix rain, starfield, plasma, lava lamp, Game of Life.

Built to be cheap on a Pi Zero 2 W: each animation advances its simulation at most
~15 times a second (often on a reduced grid that PIL scales up in C), and the frame in
between reuses the last image.
"""
from __future__ import annotations

import math
import random
import time

from PIL import Image

from .base import Board

STEP = 1 / 15.0
ANIMATIONS = {"fire": "Fireplace", "matrix": "Matrix rain", "starfield": "Starfield warp",
              "plasma": "Plasma", "lava": "Lava lamp", "life": "Game of Life"}


class _Anim:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w, h))
        self.t = 0.0

    def step(self, dt):
        self.t += dt


class Fire(_Anim):
    """Classic 'doom fire' on a half-resolution grid."""
    def __init__(self, w, h):
        super().__init__(w, h)
        self.gw, self.gh = w // 2, h // 2
        self.cells = bytearray(self.gw * self.gh)
        pal = []
        for i in range(37):
            k = i / 36
            r = min(255, int(255 * min(1, k * 2.2)))
            g = int(255 * max(0, min(1, (k - 0.35) * 1.8)))
            b = int(255 * max(0, (k - 0.85) * 4))
            pal += [r, g, b]
        self.palette = pal + [0] * (768 - len(pal))
        for x in range(self.gw):
            self.cells[(self.gh - 1) * self.gw + x] = 36

    def step(self, dt):
        super().step(dt)
        gw, gh, c = self.gw, self.gh, self.cells
        rnd = random.random
        for x in range(gw):                       # flicker the fuel line
            c[(gh - 1) * gw + x] = 36 if rnd() > 0.1 else 30
        for y in range(1, gh):
            row, up = y * gw, (y - 1) * gw
            for x in range(gw):
                v = c[row + x]
                if v == 0:
                    c[up + x] = 0
                    continue
                r = int(rnd() * 3.0)
                nx = x - r + 1
                if 0 <= nx < gw:
                    c[up + nx] = max(0, v - (r & 1))
        im = Image.frombytes("P", (gw, gh), bytes(c))
        im.putpalette(self.palette)
        self.img = im.convert("RGB").resize((self.w, self.h), Image.NEAREST)


class Matrix(_Anim):
    CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ$+-*/=<>"

    def __init__(self, w, h):
        super().__init__(w, h)
        from .. import fonts
        self.font = fonts.get("4x6")
        self.cols = [self._new(x, True) for x in range(0, w, 5)]
        self.glyph_cache = {}

    def _new(self, x, scatter=False):
        return {"x": x, "y": random.uniform(-30, 0) if not scatter else random.uniform(-30, self.h),
                "speed": random.uniform(10, 24), "len": random.randint(3, 7),
                "chars": [random.choice(self.CHARS) for _ in range(12)]}

    def _glyph(self, ch, col):
        key = (ch, col)
        if key not in self.glyph_cache:
            self.glyph_cache[key] = self.font.render(ch, col)
        return self.glyph_cache[key]

    def step(self, dt):
        super().step(dt)
        img = Image.new("RGB", (self.w, self.h))
        for i, col in enumerate(self.cols):
            col["y"] += col["speed"] * dt
            if random.random() < 0.15:
                col["chars"][random.randrange(12)] = random.choice(self.CHARS)
            head = int(col["y"] // 6)
            for k in range(col["len"]):
                row = head - k
                y = row * 6
                if y < -6 or y >= self.h:
                    continue
                if k == 0:
                    c = (210, 255, 210)
                else:
                    f = 1 - k / (col["len"] + 0.5)
                    c = (0, int(60 + 180 * f), int(20 * f))
                g = self._glyph(col["chars"][row % 12], c)
                img.paste(g, (col["x"], y), g)
            if (head - col["len"]) * 6 > self.h:
                self.cols[i] = self._new(col["x"])
        self.img = img


class Starfield(_Anim):
    def __init__(self, w, h, n=90):
        super().__init__(w, h)
        self.stars = [self._new() for _ in range(n)]

    def _new(self):
        return [random.uniform(-1, 1), random.uniform(-1, 1), random.uniform(0.05, 1)]

    def step(self, dt):
        super().step(dt)
        img = Image.new("RGB", (self.w, self.h))
        px = img.load()
        cx, cy = self.w / 2, self.h / 2
        for s in self.stars:
            oz = s[2]
            s[2] -= dt * 0.35
            if s[2] <= 0.02:
                s[:] = self._new()
                s[2] = 1.0
                continue
            for z, bright in ((s[2], 1.0), (oz, 0.35)):           # star + short streak
                x = cx + s[0] / z * cx * 0.9
                y = cy + s[1] / z * cy * 0.9
                if 0 <= x < self.w and 0 <= y < self.h:
                    b = int(255 * min(1, (1 - z) * 1.4) * bright)
                    if b > 0:
                        o = px[int(x), int(y)]
                        px[int(x), int(y)] = (max(o[0], b), max(o[1], b), max(o[2], min(255, b + 30)))
        self.img = img


class Plasma(_Anim):
    def __init__(self, w, h):
        super().__init__(w, h)
        self.gw, self.gh = w // 4, h // 4

    def step(self, dt):
        super().step(dt)
        t = self.t * 0.8
        data = []
        sin = math.sin
        for y in range(self.gh):
            for x in range(self.gw):
                v = (sin(x * 0.35 + t) + sin(y * 0.6 - t * 1.3) + sin((x + y) * 0.25 + t * 0.7)
                     + sin(math.hypot(x - self.gw / 2, y - self.gh / 2) * 0.45 - t * 1.6)) / 4
                h = (v + 1) * math.pi
                data.append((int(127 + 127 * sin(h)), int(127 + 127 * sin(h + 2.1)), int(127 + 127 * sin(h + 4.2))))
        im = Image.new("RGB", (self.gw, self.gh))
        im.putdata(data)
        self.img = im.resize((self.w, self.h), Image.BILINEAR)


class Lava(_Anim):
    def __init__(self, w, h):
        super().__init__(w, h)
        self.gw, self.gh = w // 4, h // 4
        self.blobs = [[random.uniform(0, self.gw), random.uniform(0, self.gh), random.uniform(1.6, 2.6),
                       random.uniform(0.2, 0.6), random.uniform(0, 6.3)] for _ in range(6)]

    def step(self, dt):
        super().step(dt)
        for b in self.blobs:
            b[4] += dt * b[3]
            b[1] = self.gh / 2 + math.sin(b[4]) * (self.gh / 2 + 1)
            b[0] += math.cos(b[4] * 0.7) * dt * 1.2
            b[0] %= self.gw
        data = []
        for y in range(self.gh):
            for x in range(self.gw):
                f = 0.0
                for bx, by, r, _, _ in self.blobs:
                    d2 = (x - bx) ** 2 + (y - by) ** 2 * 2.2 + 0.01
                    f += r * r / d2
                k = max(0.0, min(1.0, (f - 0.8) / 0.9))
                k = k * k * (3 - 2 * k)
                data.append((int(40 + 215 * k), int(5 + 120 * k * k), int(60 * (1 - k) + 20 * k)))
        im = Image.new("RGB", (self.gw, self.gh))
        im.putdata(data)
        self.img = im.resize((self.w, self.h), Image.BICUBIC)


class Life(_Anim):
    def __init__(self, w, h):
        super().__init__(w, h)
        self.gw, self.gh = w // 2, h // 2
        self._seed()
        self.acc = 0.0

    def _seed(self):
        self.grid = [[1 if random.random() < 0.33 else 0 for _ in range(self.gw)] for _ in range(self.gh)]
        self.age = [[0] * self.gw for _ in range(self.gh)]
        self.history = []

    def step(self, dt):
        super().step(dt)
        self.acc += dt
        if self.acc >= 0.12:                    # ~8 generations a second
            self.acc = 0.0
            gw, gh, g = self.gw, self.gh, self.grid
            new = [[0] * gw for _ in range(gh)]
            for y in range(gh):
                ym, yp = g[(y - 1) % gh], g[(y + 1) % gh]
                row = g[y]
                for x in range(gw):
                    xm, xp = (x - 1) % gw, (x + 1) % gw
                    n = ym[xm] + ym[x] + ym[xp] + row[xm] + row[xp] + yp[xm] + yp[x] + yp[xp]
                    if n == 3 or (n == 2 and row[x]):
                        new[y][x] = 1
                        self.age[y][x] = min(40, self.age[y][x] + 1) if row[x] else 0
            self.grid = new
            sig = hash(tuple(map(tuple, new)))
            self.history = (self.history + [sig])[-12:]
            if self.history.count(sig) > 2 or sum(map(sum, new)) < 12:
                self._seed()                    # stuck or dying out: start over
        data = []
        for y in range(self.gh):
            for x in range(self.gw):
                if self.grid[y][x]:
                    a = self.age[y][x] / 40
                    data.append((int(40 + 60 * a), int(220 - 120 * a), int(120 + 135 * a)))
                else:
                    data.append((0, 0, 0))
        im = Image.new("RGB", (self.gw, self.gh))
        im.putdata(data)
        self.img = im.resize((self.w, self.h), Image.NEAREST)


CLASSES = {"fire": Fire, "matrix": Matrix, "starfield": Starfield, "plasma": Plasma, "lava": Lava, "life": Life}


class AmbientBoard(Board):
    name = "ambient"
    title = "Ambient"
    description = ("Relaxing animations: fireplace, Matrix rain, starfield, plasma, lava lamp and Game of Life. "
                   "Use it in the rotation, as the 'nothing to show' screen (Settings), or during quiet hours.")
    DEFAULTS = {"enabled": False, "duration": 30, "animation": "cycle", "cycle_minutes": 5}
    FIELDS = [
        {"key": "animation", "label": "Animation", "type": "select", "options": ["cycle"] + list(ANIMATIONS),
         "option_labels": dict({"cycle": "Cycle through all"}, **ANIMATIONS)},
        {"key": "cycle_minutes", "label": "Minutes per animation when cycling", "type": "number",
         "show_if": {"animation": "cycle"}},
        {"key": "duration", "label": "Seconds on screen (in rotation)", "type": "number"},
    ]

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.anims = {}
        self._last = None

    def current_name(self):
        a = self.cfg.get("animation", "cycle")
        if a in CLASSES:
            return a
        n = max(1.0, float(self.cfg.get("cycle_minutes", 5) or 5)) * 60
        names = list(CLASSES)
        return names[int(time.time() // n) % len(names)]

    def render(self, c, slide, t):
        name = self.current_name()
        anim = self.anims.get(name)
        if anim is None or anim.w != c.width or anim.h != c.height:
            anim = self.anims[name] = CLASSES[name](c.width, c.height)
            anim.step(STEP)
        now = time.monotonic()
        if self._last is None or now - self._last >= STEP:
            dt = min(0.25, now - self._last) if self._last else STEP
            anim.step(dt)
            self._last = now
        c.image.paste(anim.img, (0, 0))
