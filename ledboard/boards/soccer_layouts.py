"""Graphical live-game layouts for soccer (MLS, Premier League, ...)."""
from __future__ import annotations

import math
import time

from ..gfx import DIM, GRAY, WHITE, YELLOW, scale
from .football_layouts import panel_color, score_color, score_text, small_logo

RED = (230, 30, 30)
CARD_YELLOW = (255, 210, 0)
ORANGE = (255, 140, 0)
LINE = (45, 45, 45)

LAYOUTS = {
    "classic": "Classic (logos + rotating info line)",
    "scorebug": "Scorebug (TV-style bars, clock, possession, cards)",
    "stats": "Match Stats (possession, shots, corners...)",
    "timeline": "Timeline (goals and cards along the 90 minutes)",
}

STAT_ROWS = [("possession", "POSS", True), ("shots_total", "SHOTS", False), ("shots_on", "ON TARGET", False),
             ("corners", "CORNERS", False), ("fouls", "FOULS", False), ("saves", "SAVES", False)]


# ---------------------------------------------------------------- helpers
def clock_label(g):
    """'67'', 'HT', 'FT', 'ET 104'' ..."""
    d = (g.get("detail") or "").upper()
    if d in ("HT", "HALFTIME") or "HALF" in d and "TIME" in d:
        return "HT"
    if d.startswith("FT") or d.startswith("FINAL"):
        return "FT"
    if "PEN" in d:
        return "PENS"
    return g.get("clock") or d or ""


def _card(c, x, y, color, w=3, h=4):
    c.rect(x, y, x + w - 1, y + h - 1, color)


def _ball(c, x, y, color=WHITE):
    """3x3 ball icon centered on (x, y)."""
    for dx, dy in ((0, -1), (-1, 0), (0, 0), (1, 0), (0, 1)):
        c.pixel(x + dx, y + dy, color)
    c.pixel(x, y, (0, 0, 0))


def available_stats(g):
    return [(key, label, pct) for key, label, pct in STAT_ROWS
            if g["away"].get(key) is not None and g["home"].get(key) is not None]


def ticker_items(board, g):
    items = []
    for side in ("away", "home"):
        goals = [e for e in g.get("events") or [] if e["kind"] == "goal" and e["side"] == side]
        if goals:
            names = ", ".join(f"{e['player'] or '?'} {e['minute_txt']}" + (" (P)" if e["penalty"] else "") +
                              (" (OG)" if e["own_goal"] else "") for e in goals)
            items.append((f"{g[side]['abbr']}: {names}", WHITE))
    for key, label, pct in available_stats(g)[:4]:
        a, h = g["away"][key], g["home"][key]
        fmt = (lambda v: f"{round(v)}%") if pct else (lambda v: f"{round(v)}")
        items.append((f"{label} {fmt(a)} - {fmt(h)}", GRAY))
    if not items and g.get("last_goal"):
        items.append((g["last_goal"], ORANGE))
    return items or [(clock_label(g), YELLOW)]


def _split_bar(c, x0, x1, y, h, a, b, ca, cb):
    """Bar from x0..x1 split in proportion a : b (away color left, home color right)."""
    total = (a or 0) + (b or 0)
    if total <= 0:
        c.rect(x0, y, x1, y + h - 1, DIM)
        return
    split = x0 + round((x1 - x0 + 1) * a / total)
    if split > x0:
        c.rect(x0, y, split - 1, y + h - 1, ca)
    if split <= x1:
        c.rect(split, y, x1, y + h - 1, cb)


# ---------------------------------------------------------------- layouts
def render_scorebug(board, c, g, t, team_color, cycle):
    W, H = c.width, c.height
    L = 14                                     # logo tiles
    bar_w = (W - 2 * L - 28) // 2              # 36 px bars, 28 px clock box
    clock_x0 = L + bar_w
    clock_x1 = W - L - bar_w - 1
    for side, lx, bx0 in (("away", 0, L), ("home", W - L, W - L - bar_w)):
        team = g[side]
        bx1 = bx0 + bar_w - 1
        c.rect(bx0, 0, bx1, L - 1, panel_color(team))
        edge = bx0 if side == "away" else bx1 - 1              # bright stripe in the true team color
        c.rect(edge, 0, edge + 1, L - 1, team_color(team))
        logo = small_logo(board, team, L, g["home" if side == "away" else "away"])
        if logo is not None:
            c.paste(logo, lx, 0)
        s = score_text(board, g, side)
        if side == "away":
            c.text(bx0 + 4, 2, team["abbr"], "6x10", WHITE)
            c.text_right(bx1 - 1, 1, s, "7x13B", score_color(board, g, side))
        else:
            c.text(bx0 + 2, 1, s, "7x13B", score_color(board, g, side))
            c.text_right(bx1 - 3, 2, team["abbr"], "6x10", WHITE)
    # clock box
    c.rect(clock_x0, 0, clock_x1, L - 1, (20, 22, 28))
    c.text_centered(2, clock_label(g), "6x10", YELLOW, clock_x0, clock_x1 + 1)

    # possession bar under the bars, cards below each team
    a, h = g["away"].get("possession"), g["home"].get("possession")
    if a is not None and h is not None:
        _split_bar(c, L, W - L - 1, L + 1, 2, a, h, team_color(g["away"]), team_color(g["home"]))
    for side in ("away", "home"):
        team = g[side]
        cards = [RED] * min(3, team.get("red_cards") or 0) + [CARD_YELLOW] * min(5, team.get("yellow_cards") or 0)
        for i, col in enumerate(cards):
            x = L + 1 + i * 5 if side == "away" else W - L - 4 - i * 5
            _card(c, x, L + 5, col)
    # ticker
    text, color, lt = cycle(c, ticker_items(board, g), t, W, font="5x7")
    c.scroll_text(0, W, H - 7, text, "5x7", color, lt, speed=26, pause=0.8, gap=200)


def _compact_header(board, c, g, team_color, y=0):
    """[logo ABBR score]   clock   [score ABBR logo] on one 9px row."""
    W = c.width
    L = 9
    for side in ("away", "home"):
        team = g[side]
        logo = small_logo(board, team, L, g["home" if side == "away" else "away"])
        s = score_text(board, g, side)
        if side == "away":
            x = 0
            if logo is not None:
                c.paste(logo, 0, y)
                x = L + 1
            x += c.text(x, y + 1, team["abbr"], "5x8", team_color(team)) + 3
            c.text(x, y + 1, s, "5x8", score_color(board, g, side))
        else:
            x = W
            if logo is not None:
                c.paste(logo, W - L, y)
                x = W - L - 1
            aw = c.text_width(team["abbr"], "5x8")
            c.text(x - aw, y + 1, team["abbr"], "5x8", team_color(team))
            c.text_right(x - aw - 3, y + 1, s, "5x8", score_color(board, g, side))
    c.text_centered(y + 1, clock_label(g), "5x8", YELLOW, 44, W - 44)


def render_stats(board, c, g, t, team_color, cycle):
    W, H = c.width, c.height
    _compact_header(board, c, g, team_color)
    c.line(0, 9, W - 1, 9, LINE)
    stats = available_stats(g)
    if not stats:
        c.text_centered(14, "NO STATS YET", "5x7", GRAY)
        text, color, lt = cycle(c, ticker_items(board, g), t, W, font="5x7")
        c.scroll_text(0, W, H - 7, text, "5x7", color, lt, speed=26, pause=0.8, gap=200)
        return
    rows = 3
    pages = [stats[i:i + rows] for i in range(0, len(stats), rows)]
    page = pages[int(t // 4) % len(pages)]
    ca, ch = team_color(g["away"]), team_color(g["home"])
    for i, (key, label, pct) in enumerate(page):
        y = 11 + i * 7
        a, h = g["away"][key], g["home"][key]
        fa = f"{round(a)}%" if pct else str(round(a))
        fh = f"{round(h)}%" if pct else str(round(h))
        c.text_right(17, y, fa, "4x6", WHITE)
        c.text(W - 17, y, fh, "4x6", WHITE)
        _split_bar(c, 20, W - 21, y + 1, 4, a, h, scale(ca, 0.8), scale(ch, 0.8))
        lw = c.text_width(label, "4x6")
        c.rect((W - lw) // 2 - 2, y, (W + lw) // 2 + 1, y + 5, (0, 0, 0))
        c.text_centered(y, label, "4x6", GRAY)
    if len(pages) > 1:          # page dots bottom-right
        cur = int(t // 4) % len(pages)
        for i in range(len(pages)):
            c.pixel(W - 2 - (len(pages) - 1 - i) * 2, H - 1, WHITE if i == cur else DIM)


def render_timeline(board, c, g, t, team_color, cycle):
    W, H = c.width, c.height
    _compact_header(board, c, g, team_color)
    events = g.get("events") or []
    minute = g.get("minute") or 0
    label = clock_label(g)
    if label == "HT":
        minute = max(minute, 45)
    full = 120 if (g.get("period") or 0) > 2 or minute > 95 else 90
    x0, x1 = 15, W - 3                          # team keys sit left of the line
    y = 22                                      # the timeline

    def X(m):
        return x0 + round(min(full, max(0, m)) / full * (x1 - x0))

    c.line(x0, y, x1, y, DIM)                                   # the whole match
    if minute:
        c.line(x0, y, X(minute), y, WHITE)                      # played so far
    for m, tall in ((0, 2), (45, 3), (90, 2)) + (((105, 2), (120, 2)) if full == 120 else ()):
        c.line(X(m), y - tall, X(m), y + tall, GRAY if m in (45, 90) else DIM)
    # now marker: small pulsing triangle
    if minute and label not in ("FT",):
        b = 0.5 + 0.5 * (math.sin(time.monotonic() * 5) + 1) / 2
        mx = X(minute)
        col = scale(YELLOW, b)
        c.pixel(mx, y - 2, col)
        c.line(mx - 1, y - 3, mx + 1, y - 3, col)
    # events: away above the line, home below
    for e in events:
        if e.get("minute") is None:
            continue
        ex = X(e["minute"])
        above = e["side"] == "away"
        if e["kind"] == "goal":
            col = team_color(g[e["side"]])
            ey = y - 5 if above else y + 5
            _ball(c, ex, ey, WHITE)
            c.line(ex, y - 1 if above else y + 1, ex, ey + (1 if above else -1), scale(col, 0.7))
        else:
            col = RED if e["kind"] == "red" else CARD_YELLOW
            _card(c, ex - 1, y - 7 if above else y + 4, col, w=2, h=3)
    # tiny team keys at the ends: away above, home below
    c.text(0, 13, g["away"]["abbr"], "4x6", team_color(g["away"]))
    c.text(0, H - 6, g["home"]["abbr"], "4x6", team_color(g["home"]))


RENDERERS = {"scorebug": render_scorebug, "stats": render_stats, "timeline": render_timeline}
