"""Scoring celebrations for favorite teams (touchdown, field goal, goal, home run...).

A celebration is queued by a league board when its update sees a favorite team score.
The engine plays queued celebrations as "intro" slides the next time that board comes
up, then the board's normal slides. Everything here is a pure function of time t, so
the animation is smooth at any frame rate.
"""
from __future__ import annotations

import math
import random
import time

from .. import fonts
from ..gfx import scale, WHITE, YELLOW, readable

DURATION = 6.0
KINDS = {
    "touchdown": "TOUCHDOWN!",
    "field_goal": "FIELD GOAL!",
    "goal": "GOAL!",
    "home_run": "HOME RUN!",
    "grand_slam": "GRAND SLAM!",
    "run": "RUN SCORES!",
}


class Celebration:
    """An intro slide. Stays valid while the board's own slide list changes."""
    intro = True

    def __init__(self, kind, side, game, sport):
        self.kind, self.side, self.sport = kind, side, sport
        self.game = game                      # snapshot of the parsed game dict
        self.created = time.time()

    def __eq__(self, other):
        return self is other

    def __hash__(self):
        return id(self)

    def __repr__(self):
        return f"Celebration({self.kind}, {self.game[self.side]['abbr']})"


# ---------------------------------------------------------------- scoring rules
HR_WORDS = ("homer", "home run", "grand slam")


def classify(sport, diff, last_play="", cfg=None, recent_td=False):
    """What a score increase of `diff` means, or None if it isn't celebrated."""
    cfg = cfg or {}
    if diff <= 0:
        return None
    if sport == "football":
        if diff >= 6:
            return "touchdown"
        if diff in (1, 2) and recent_td:
            return None                       # the extra point / 2-pt try after a TD we already have
        if diff == 3 and cfg.get("celebrate_field_goals"):
            return "field_goal"
        return None
    if sport in ("hockey", "soccer"):
        return "goal"
    if sport == "baseball":
        text = (last_play or "").lower()
        if "grand slam" in text or (diff == 4 and any(w in text for w in HR_WORDS)):
            return "grand_slam"
        if any(w in text for w in HR_WORDS):
            return "home_run"
        if cfg.get("celebrate_runs"):
            return "run"
        return None
    return None


# ---------------------------------------------------------------- drawing helpers
def _colors(team, team_color):
    from .sports import accent_color
    a = team_color(team)
    b = accent_color(team)                    # the team's other real color, never plain white
    if sum(abs(x - y) for x, y in zip(a, b)) < 90:
        b = scale(a, 0.5)
    return a, b


def _fit_font(c, text, max_w, max_h, key="anton"):
    for px in range(max_h + 6, 5, -1):
        f = fonts.scalable(key, px, ref="ABCDEFGHIJKLMNOPQRSTUVWXYZ!")
        if f.height <= max_h and f.width(text) <= max_w:
            return f
    return fonts.get("5x8")


def _confetti(c, t, colors, n=36, seed=1):
    rnd = random.Random(seed)
    for i in range(n):
        x0 = rnd.uniform(0, c.width)
        vy = rnd.uniform(9, 18)
        ph = rnd.uniform(0, 6.3)
        y = (t * vy + rnd.uniform(0, c.height + 8)) % (c.height + 8) - 4
        x = x0 + math.sin(t * 3 + ph) * 3
        col = colors[i % len(colors)]
        if int(t * 6 + i) % 3:                # flutter
            c.rect(round(x), round(y), round(x) + 1, round(y), col)
        else:
            c.pixel(round(x), round(y), col)


def _fireworks(c, t, bursts, colors, seed=7):
    """bursts: [(t0, cx, cy)]; each launches from the bottom, then explodes at t0."""
    rnd = random.Random(seed)
    for b, (t0, cx, cy) in enumerate(bursts):
        dt = t - t0
        if -0.45 < dt < 0:                    # rising rocket
            y = c.height - (c.height - cy) * (1 + dt / 0.45)
            c.pixel(cx, round(y), (255, 220, 150))
            c.pixel(cx, round(y) + 1, (120, 80, 40))
        elif 0 <= dt < 1.5:
            col = colors[b % len(colors)]
            fade = 1 - dt / 1.5
            n = 16
            for k in range(n):
                a = k * 2 * math.pi / n + rnd.uniform(-0.1, 0.1)
                r = 15 * dt * (1 - dt * 0.25)
                x = cx + math.cos(a) * r
                y = cy + math.sin(a) * r * 0.75 + 5 * dt * dt
                shade = tuple(int(v * fade) for v in col)
                c.pixel(round(x), round(y), shade)
                if dt < 0.6:
                    c.pixel(round(cx + math.cos(a) * r * 0.6), round(cy + math.sin(a) * r * 0.45 + 3 * dt * dt),
                            tuple(int(v * fade * 0.5) for v in col))


def _banner(board, c, cel, t, team_color, extra_fx=None):
    """Logo + big flashing text + score line; the 'payoff' half of every animation."""
    W, H = c.width, c.height
    team = cel.game[cel.side]
    a, b = _colors(team, team_color)
    if extra_fx:
        extra_fx(t)
    from .football_layouts import logo_fit
    slide_in = min(1.0, t / 0.35)
    logo = logo_fit(board, team, H - 1, H)
    if logo is not None:
        c.paste(logo, round(-H + slide_in * H), 0 if int(t * 4) % 2 else 1)
    x0 = H + 2
    text = KINDS[cel.kind]
    f = _fit_font(c, text, W - x0 - 1, 21)
    col = a if int(t * 5) % 2 else b
    tw = f.width(text)
    c.text(x0 + (W - x0 - tw) // 2, 1, text, f, col)
    g = cel.game
    line = f"{g['away']['abbr']} {g['away']['score']}  {g['home']['abbr']} {g['home']['score']}"
    c.text_centered(H - 8, line, "5x8", WHITE, x0, W)


# ---------------------------------------------------------------- scenes
def _touchdown(board, c, cel, t, team_color, field_goal=False):
    W, H = c.width, c.height
    team = cel.game[cel.side]
    a, b = _colors(team, team_color)
    intro = 1.6
    if t < intro:
        k = t / intro
        # scrolling field with yard lines, end zone in team color arriving from the right
        off = int(t * 60)
        c.rect(0, 0, W - 1, H - 1, (0, 45, 0))
        for x in range(-(off % 16), W, 16):
            c.line(x, 0, x, H - 1, (0, 110, 0))
        ez = round(W - 26 * k)
        c.rect(ez, 0, W - 1, H - 1, tuple(int(v * 0.6) for v in a))
        if field_goal:
            # uprights
            ux = W - 14
            c.line(ux, 12, ux, H - 1, YELLOW)
            c.line(ux - 7, 12, ux + 7, 12, YELLOW)
            c.line(ux - 7, 2, ux - 7, 12, YELLOW)
            c.line(ux + 7, 2, ux + 7, 12, YELLOW)
        # the ball: spiral across with an arc
        bx = -6 + k * (W + 2 if not field_goal else W - 8)
        by = H - 8 - math.sin(k * math.pi) * (18 if not field_goal else 22)
        spin = int(t * 14) % 2
        for dx, dy in ((-3, 0), (-2, -1), (-1, -1), (0, -1), (1, -1), (2, -1), (3, 0), (-2, 1), (-1, 1), (0, 1),
                       (1, 1), (2, 1), (-2, 0), (-1, 0), (0, 0), (1, 0), (2, 0)):
            c.pixel(round(bx + dx), round(by + dy), (150, 75, 25))
        c.pixel(round(bx) + (1 if spin else -1), round(by), WHITE)
        c.pixel(round(bx), round(by), WHITE)
        return
    tt = t - intro

    def fx(_):
        _confetti(c, tt, [a, b, WHITE])
        if not field_goal:
            _fireworks(c, tt, [(0.4, 80, 9), (1.2, 112, 12), (2.0, 60, 7), (2.8, 100, 8)], [a, b, YELLOW])
    _banner(board, c, cel, tt, team_color, fx)


def _hockey_goal(board, c, cel, t, team_color):
    W, H = c.width, c.height
    team = cel.game[cel.side]
    a, b = _colors(team, team_color)
    intro = 1.3
    lamp_on = int(t * 6) % 2 == 0
    if t < intro:
        k = t / intro
        c.line(0, H - 4, W - 1, H - 4, (40, 40, 60))                 # ice line
        # net: red posts + white mesh at the right
        nx = W - 18
        c.rect(nx, 8, nx + 1, H - 4, (220, 0, 0))
        c.line(nx, 8, W - 4, 8, (220, 0, 0))
        for y in range(10, H - 4, 3):
            c.line(nx + 2, y, W - 5, y, (90, 90, 100))
        for x in range(nx + 3, W - 4, 3):
            c.line(x, 9, x, H - 5, (90, 90, 100))
        px = round(-4 + k * (nx + 8))
        c.rect(px - 2, H - 7, px + 2, H - 5, (200, 200, 210))          # puck
        for i in range(1, 4):                                          # motion streaks
            c.pixel(px - 2 - i * 3, H - 6, (60, 60, 70))
        return
    tt = t - intro
    mid = W // 2
    # rotating goal-light beams
    for k in range(3):
        ang = tt * 4 + k * 2 * math.pi / 3
        x2 = mid + math.cos(ang) * 90
        y2 = math.sin(ang) * 40 + 4
        c.line(mid, 0, round(x2), round(y2), (90, 0, 0))
    lamp = (255, 20, 20) if lamp_on else (90, 0, 0)
    c.rect(0, 0, 1, H - 1, lamp)
    c.rect(W - 2, 0, W - 1, H - 1, lamp)

    def fx(_):
        _confetti(c, tt, [a, b], n=24, seed=3)
    _banner(board, c, cel, tt, team_color, fx)


def _soccer_goal(board, c, cel, t, team_color):
    W, H = c.width, c.height
    team = cel.game[cel.side]
    a, b = _colors(team, team_color)
    intro = 1.4
    if t < intro:
        k = t / intro
        nx = W - 20
        ripple = max(0.0, k - 0.75) * 4                                # net bulges as the ball hits
        c.rect(nx, 4, nx + 1, H - 2, WHITE)
        c.line(nx, 4, W - 2, 4, WHITE)
        for y in range(6, H - 2, 3):
            bulge = round(math.sin((y - 4) / (H - 6) * math.pi) * 4 * ripple)
            c.line(nx + 2 + bulge, y, W - 2, y, (100, 100, 110))
        for x in range(nx + 4, W - 2, 3):
            c.line(x, 5, x, H - 3, (100, 100, 110))
        bx = -4 + k * (nx + 6)
        by = H - 6 - math.sin(k * math.pi) * 14
        c.draw.ellipse((round(bx) - 3, round(by) - 3, round(bx) + 3, round(by) + 3), fill=WHITE)
        c.pixel(round(bx), round(by), (0, 0, 0))
        c.pixel(round(bx) - 2, round(by) - 1, (0, 0, 0))
        c.pixel(round(bx) + 2, round(by) + 1, (0, 0, 0))
        return
    tt = t - intro

    def fx(_):
        _confetti(c, tt, [a, b, WHITE], n=40, seed=5)
    _banner(board, c, cel, tt, team_color, fx)


def _home_run(board, c, cel, t, team_color):
    W, H = c.width, c.height
    team = cel.game[cel.side]
    a, b = _colors(team, team_color)
    intro = 1.8
    if t < intro:
        k = t / intro
        # outfield wall with a yellow top line, scrolling stars for the night sky
        wall_x = 70
        c.rect(wall_x, H - 9, W - 1, H - 1, (0, 70, 30))
        c.line(wall_x, H - 9, W - 1, H - 9, YELLOW)
        for i in range(8):
            c.pixel((i * 37 + 11) % W, (i * 13 + 3) % 18, (90, 90, 120))
        # ball on a long parabola from home plate (bottom left) out of the park (top right)
        pts = []
        for j in range(12):
            kk = max(0.0, k - j * 0.025)
            x = 4 + kk * (W + 10)
            y = H - 3 - math.sin(min(1.0, kk) * math.pi * 0.62) * 34
            pts.append((round(x), round(y)))
        for j, (x, y) in enumerate(reversed(pts[1:])):
            shade = int(40 + 12 * j)
            c.pixel(x, y, (shade, shade, shade))
        x, y = pts[0]
        c.rect(x - 1, y - 1, x + 1, y + 1, WHITE)
        c.pixel(x, y, (220, 40, 40))                                  # the stitches
        return
    tt = t - intro
    big = cel.kind == "grand_slam"

    def fx(_):
        bursts = [(0.2, 70, 9), (0.7, 100, 7), (1.2, 50, 10), (1.7, 115, 12), (2.2, 85, 6)]
        if big:
            bursts += [(0.45, 40, 8), (0.95, 120, 9), (1.45, 75, 5), (1.95, 60, 11)]
        _fireworks(c, tt, bursts, [a, b, YELLOW, WHITE])
    _banner(board, c, cel, tt, team_color, fx)


def render(board, c, cel, t, team_color):
    if cel.kind in ("touchdown", "field_goal"):
        _touchdown(board, c, cel, t, team_color, field_goal=cel.kind == "field_goal")
    elif cel.kind == "goal" and cel.sport == "hockey":
        _hockey_goal(board, c, cel, t, team_color)
    elif cel.kind == "goal":
        _soccer_goal(board, c, cel, t, team_color)
    elif cel.kind in ("home_run", "grand_slam"):
        _home_run(board, c, cel, t, team_color)
    else:   # "run"
        team = cel.game[cel.side]
        a, b = _colors(team, team_color)
        _banner(board, c, cel, t, team_color, lambda _: _confetti(c, t, [a, b], n=20))
