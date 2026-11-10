"""Full-screen designs for upcoming (pre-game) games. 'classic' is the standard view in sports.py."""
from __future__ import annotations

import math
import time

from ..gfx import DIM, GRAY, WHITE, YELLOW, scale

LAYOUTS = {
    "classic": "Classic (logos, start time, records; works with the team color accents)",
    "versus": "Versus (logos on a split team-color background with a big VS)",
    "tape": "Tale of the tape (records, ranking and betting line side by side)",
    "marquee": "Marquee (chasing light-bulb border, like a theater sign)",
    "broadcast": "Broadcast (TV-style team bars with names and a time / network box)",
}


def _when(board, g):
    return board._start_text(g)


def _info(board, g):
    """Network / odds / playoff round items, like the classic view's rotating line."""
    items = []
    if g.get("note"):
        items.append(g["note"].upper())
    if g.get("series"):
        items.append(g["series"].upper())
    if board.cfg.get("show_network", True) and g.get("network"):
        items.append(g["network"].upper())
    if board.cfg.get("show_odds") and g.get("odds"):
        items.append(g["odds"])
    return items


def _draw_line(board, c, g, text, x0, x1, y, color, t, font="5x7"):
    logo = board._network_logo(g, text) if hasattr(board, "_network_logo") else None
    if logo is not None:
        c.paste(logo, x0 + (x1 - x0 - logo.width) // 2, y)
    else:
        c.scroll_text(x0, x1, y, text, font, color, t % 3, speed=30, align="center", pause=0.3)


def render_versus(board, c, g, t, team_color):
    from .football_layouts import logo_fit, panel_color
    W, H = c.width, c.height
    away, home = g["away"], g["home"]
    mid = W // 2
    # diagonal split: away color on the left, home color on the right
    pa, ph = scale(panel_color(away), 0.8), scale(panel_color(home), 0.8)
    for y in range(H):
        cut = mid + 6 - round(y * 12 / (H - 1))
        c.line(0, y, cut - 1, y, pa)
        c.line(cut, y, W - 1, y, ph)
    L = 28
    c.paste(logo_fit(board, away, L, L, home), 2, 2)
    c.paste(logo_fit(board, home, L, L, away), W - L - 2, 2)
    # the VS badge, pulsing
    k = 0.85 + 0.15 * math.sin(time.monotonic() * 3)
    c.draw.ellipse((mid - 11, 0, mid + 10, 20), fill=(0, 0, 0), outline=scale(WHITE, k))
    c.text_centered(4, "VS", "7x13B", scale(YELLOW, k), mid - 11, mid + 11)
    c.rect(mid - 26, H - 9, mid + 25, H - 1, (0, 0, 0))
    items = [_when(board, g)] + _info(board, g)
    _draw_line(board, c, g, items[int(t // 3) % len(items)], mid - 26, mid + 26, H - 8, WHITE, t)


def render_tape(board, c, g, t, team_color):
    from .football_layouts import small_logo
    W, H = c.width, c.height
    away, home = g["away"], g["home"]
    # header: logos + abbreviations at the edges, start time in the middle
    for side, x, align in (("away", 0, "left"), ("home", W - 12, "right")):
        team = g[side]
        c.paste(small_logo(board, team, 12, g["home" if side == "away" else "away"]), x, 0)
        if align == "left":
            c.text(14, 2, team["abbr"], "6x10", team_color(team))
        else:
            c.text_right(W - 15, 2, team["abbr"], "6x10", team_color(team))
    c.text_centered(1, _when(board, g), "4x6", WHITE, 36, W - 36)
    c.text_centered(7, "@" if board.sport != "soccer" else "V", "4x6", DIM, 36, W - 36)
    c.line(0, 13, W - 1, 13, (40, 40, 46))
    rows = []
    if away.get("record") or home.get("record"):
        rows.append(("RECORD", away.get("record") or "-", home.get("record") or "-"))
    if away.get("rank") or home.get("rank"):
        rows.append(("RANK", f"#{away['rank']}" if away.get("rank") else "-", f"#{home['rank']}" if home.get("rank") else "-"))
    if g.get("odds"):
        rows.append(("LINE", g["odds"], f"O/U {g['over_under']}" if g.get("over_under") not in (None, "") else ""))
    if g.get("series"):
        rows.append(("SERIES", g["series"], ""))
    if board.cfg.get("show_network", True) and g.get("network"):
        rows.append(("TV", g["network"].upper(), ""))
    pages = [rows[i:i + 3] for i in range(0, len(rows), 3)] or [[]]
    for i, (label, a, h) in enumerate(pages[int(t // 4) % len(pages)]):
        y = 15 + i * 6
        if h:                                # two-sided row: away value | label | home value
            c.text(1, y, a, "4x6", WHITE)
            c.text_centered(y, label, "4x6", GRAY, 40, W - 40)
            c.text_right(W - 1, y, h, "4x6", WHITE)
        else:                                # one value: label on the left, value on the right
            c.text(1, y, label, "4x6", GRAY)
            c.text_right(W - 1, y, a, "4x6", WHITE)


def render_marquee(board, c, g, t, team_color):
    from .football_layouts import small_logo
    W, H = c.width, c.height
    away, home = g["away"], g["home"]
    # chasing bulbs around the edge
    perim = [(x, 0) for x in range(0, W, 2)] + [(W - 1, y) for y in range(2, H, 2)] + \
            [(x, H - 1) for x in range(W - 1, -1, -2)] + [(0, y) for y in range(H - 1, 1, -2)]
    off = int(time.monotonic() * 12)
    gold = (255, 190, 60)
    for i, (x, y) in enumerate(perim):
        c.pixel(x, y, gold if (i + off) % 3 else scale(gold, 0.3))
    c.paste(small_logo(board, away, 14, home), 4, 3)
    c.paste(small_logo(board, home, 14, away), W - 18, 3)
    when = _when(board, g)
    head = "TONIGHT" if "PM" in when and ":" in when and len(when) <= 7 else ("TODAY" if len(when) <= 7 else "COMING UP")
    c.text_centered(3, head, "5x7", gold, 20, W - 20)
    c.text_centered(11, f"{away['abbr']} @ {home['abbr']}", "6x10", WHITE, 20, W - 20)
    items = [when] + _info(board, g)
    _draw_line(board, c, g, items[int(t // 3) % len(items)], 4, W - 4, H - 9, YELLOW, t)


def render_broadcast(board, c, g, t, team_color):
    from .football_layouts import panel_color, small_logo
    W, H = c.width, c.height
    bw = 84                                         # the team bars; the right box holds time / network
    for row, side in ((0, "away"), (1, "home")):
        team = g[side]
        y = 2 + row * 14
        c.rect(0, y, bw - 1, y + 12, panel_color(team))
        c.rect(0, y, 2, y + 12, team_color(team))
        c.rect(3, y, 16, y + 12, (0, 0, 0))                 # dark tile so the logo never blends into the bar
        c.paste(small_logo(board, team, 12, g["home" if side == "away" else "away"]), 4, y)
        name = (team.get("name") or team["abbr"]).upper()
        c.scroll_text(18, bw - 22, y + 2, name, "6x10" if c.text_width(name, "6x10") <= bw - 40 else "5x8",
                      WHITE, t)
        if team.get("record"):
            c.text_right(bw - 2, y + 4, team["record"], "4x6", scale(WHITE, 0.75))
    bx0 = bw + 2
    c.draw.rounded_rectangle((bx0, 2, W - 1, H - 3), radius=2, fill=(20, 22, 28), outline=(60, 64, 72))
    when = _when(board, g)
    parts = when.split(" ")
    top, bottom = (parts[0], " ".join(parts[1:])) if len(parts) > 1 else ("", when)
    if top:
        c.text_centered(5, top, "4x6", GRAY, bx0, W)
    c.text_centered(12, bottom, "5x7", WHITE if c.text_width(bottom, "5x7") < W - bx0 - 2 else WHITE, bx0, W)
    info = _info(board, g)
    if info:
        _draw_line(board, c, g, info[int(t // 3) % len(info)], bx0 + 1, W - 1, H - 10, YELLOW, t, font="4x6")


RENDERERS = {"versus": render_versus, "tape": render_tape, "marquee": render_marquee, "broadcast": render_broadcast}
