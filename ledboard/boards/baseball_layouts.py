"""Graphical live-game layouts for baseball (MLB)."""
from __future__ import annotations

from ..gfx import DIM, GRAY, WHITE, YELLOW
from .football_layouts import panel_color, score_color, score_text, small_logo

GREEN = (40, 210, 70)
RED = (230, 40, 40)
ORANGE = (255, 140, 0)
LINE = (45, 45, 45)

LAYOUTS = {
    "classic": "Classic (logos + rotating info line)",
    "scorebug": "Scorebug (TV-style: big diamond, B/S/O dots)",
    "linescore": "Line Score (runs by inning + R/H/E)",
    "matchup": "Matchup (pitcher vs. batter)",
}


# ---------------------------------------------------------------- shared pieces
def draw_base(c, cx, cy, r, on, color=YELLOW, off=GRAY):
    """A base as a diamond-shaped square of radius r centered at (cx, cy)."""
    for dy in range(-r, r + 1):
        half = r - abs(dy)
        for dx in range(-half, half + 1):
            if on or abs(dx) == half:
                c.pixel(cx + dx, cy + dy, color if on else off)


def draw_diamond(c, cx, top, d, r, bases, home=True):
    """Bases [1st, 2nd, 3rd]: 2nd at the top, 3rd left, 1st right, home plate at the bottom.

    `top` is the topmost pixel row of the diamond (the tip of 2nd base); `d` is the
    distance between base centers and `r` the radius of each base.
    """
    top += r                       # centre row of 2nd base
    mid = top + d
    if home:
        for i in range(1, d):
            # faint base paths
            c.pixel(cx - d + i, mid + i, LINE)
            c.pixel(cx + d - i, mid + i, LINE)
        c.rect(cx - 1, top + 2 * d - 1, cx + 1, top + 2 * d, GRAY)
    for i in range(1, d):
        c.pixel(cx - i, top + i, LINE)
        c.pixel(cx + i, top + i, LINE)
    draw_base(c, cx, top, r, bases[1])
    draw_base(c, cx + d, mid, r, bases[0])
    draw_base(c, cx - d, mid, r, bases[2])


def draw_dots(c, x, y, n, total, color, size=3, gap=1):
    n = n if isinstance(n, int) else 0
    for i in range(total):
        xx = x + i * (size + gap)
        c.rect(xx, y, xx + size - 1, y + size - 1, color if i < n else DIM)


def draw_half_arrow(c, x, y, half, color=YELLOW):
    """5x3 triangle: up for top of the inning, down for bottom."""
    if half == "top":
        for i in range(3):
            c.line(x + 2 - i, y + i, x + 2 + i, y + i, color)
    elif half == "bottom":
        for i in range(3):
            c.line(x + i, y + i, x + 4 - i, y + i, color)


def inning_label(g):
    """('▲', '7') style pieces; mid/end innings get a word instead of an arrow."""
    n = g.get("period") or ""
    half = g.get("half")
    if half in ("mid", "end"):
        return half.upper(), str(n)
    return None, str(n)


def draw_inning(c, x, y, g, font="6x10", color=YELLOW):
    """Draw the inning indicator at (x, y); returns its width."""
    word, n = inning_label(g)
    if word:
        return c.text(x, y, f"{word} {n}", "4x6" if font != "4x6" else font, color)
    draw_half_arrow(c, x, y + 3 if font == "6x10" else y + 1, g.get("half"), color)
    return 6 + c.text(x + 6, y, n, font, color)


def ticker_items(board, g):
    items = []
    if g.get("pitcher"):
        items.append((f"P: {g['pitcher']}" + (f"  {g['pitcher_summary']}" if g.get("pitcher_summary") else ""), GRAY))
    if g.get("batter"):
        items.append((f"AB: {g['batter']}" + (f"  {g['batter_summary']}" if g.get("batter_summary") else ""), WHITE))
    if board.cfg.get("show_last_play", True) and g.get("last_play"):
        items.append((g["last_play"], ORANGE))
    if not items:
        items.append((board._clock_text(g), YELLOW))
    return items


# ---------------------------------------------------------------- layouts
def render_scorebug(board, c, g, t, team_color, cycle):
    """TV-style bug using the full width:

    [logo][team bar: ABBR  score] x2 | big diamond | inning + B/S/O column
    with a pitcher / batter / last-play ticker underneath the teams and diamond.
    """
    W, H = c.width, c.height
    row_h = 12                     # two team rows: y 0-11 and 12-23
    bar_end = 55                   # team bars run x 0..55
    col_x = W - 40                 # right column x 88..127
    dia_cx = (bar_end + 2 + col_x) // 2

    # teams: logo tile (on black) + team-color bar with abbreviation and score
    for row, side in ((0, "away"), (1, "home")):
        team = g[side]
        y = row * row_h
        logo = small_logo(board, team, row_h - 1, g["home" if side == "away" else "away"])
        has_logo = logo is not None
        bx = row_h if has_logo else 0
        bg = panel_color(team)
        c.rect(bx, y, bar_end, y + row_h - 2, bg)
        c.rect(bx, y, bx + 1, y + row_h - 2, team_color(team))
        if has_logo:
            c.paste(logo, 0, y)
        c.text(bx + 4, y + 1, team["abbr"], "6x10", WHITE)
        c.text_right(bar_end - 2, y + 1, score_text(board, g, side), "6x10", score_color(board, g, side))
        if g.get("half") == ("top" if side == "away" else "bottom"):
            # batting team: small yellow pointer just outside the bar
            for i in range(3):
                c.line(bar_end + 2 + i, y + 3 + i, bar_end + 2 + i, y + 7 - i, YELLOW)

    # diamond (middle), a bit larger than before
    draw_diamond(c, dia_cx, 1, 9, 3, g.get("bases") or [False] * 3)

    # right column: big inning on top, then B / S / O rows with large dots
    word, n = inning_label(g)
    if word:
        c.text_centered(1, f"{word} {n}", "5x8", YELLOW, col_x, W)
    else:
        f = "7x13B"
        nw = c.text_width(n, f)
        x = col_x + (W - col_x - (6 + nw)) // 2
        draw_half_arrow(c, x, 5, g.get("half"))
        c.text(x + 6, 0, n, f, YELLOW)
    dot, gap = 5, 2
    block_w = 4 + 3 + 3 * dot + 2 * gap               # label + space + three dots
    bx = col_x + (W - col_x - block_w) // 2           # centered in the column
    for i, (label, cnt, total, color) in enumerate((("B", g.get("balls"), 3, GREEN),
                                                   ("S", g.get("strikes"), 2, RED),
                                                   ("O", g.get("outs"), 2, YELLOW))):
        y = 14 + i * 6
        c.text(bx, y, label, "4x6", GRAY)
        draw_dots(c, bx + 7, y, cnt, total, color, size=dot, gap=gap)

    # ticker underneath the teams and diamond
    tick_x1 = col_x - 2
    text, color, lt = cycle(c, ticker_items(board, g), t, tick_x1, font="4x6")
    c.scroll_text(0, tick_x1, H - 6, text, "4x6", color, lt, speed=26, pause=0.8, gap=200)


def render_linescore(board, c, g, t, team_color, cycle):
    """Box score: innings + R/H/E, current inning highlighted, situation strip underneath."""
    W = c.width
    away, home = g["away"], g["home"]
    cur = int(g.get("period") or 1)
    n_inn = max(9, cur, len(away.get("linescore") or []), len(home.get("linescore") or []))
    first = max(1, n_inn - 8)                       # show the last 9 innings in extras
    abbr_w = 16
    col_w = 8
    stat_w = 10
    x_inn = abbr_w + 1
    x_r = x_inn + 9 * col_w + 3
    # header
    for i in range(9):
        inn = first + i
        color = YELLOW if inn == cur else GRAY
        c.text_centered(0, str(inn), "4x6", color, x_inn + i * col_w, x_inn + (i + 1) * col_w)
    for j, lab in enumerate("RHE"):
        c.text_centered(0, lab, "4x6", WHITE if lab == "R" else GRAY, x_r + j * stat_w, x_r + (j + 1) * stat_w)
    c.line(0, 6, W - 1, 6, LINE)
    c.line(x_r - 2, 0, x_r - 2, 20, LINE)
    hide = board.cfg.get("hide_favorite_score") and board.is_favorite(g)
    for row, side in ((0, "away"), (1, "home")):
        team = g[side]
        y = 8 + row * 7
        c.text(1, y, team["abbr"], "4x6", team_color(team))
        ls = team.get("linescore") or []
        for i in range(9):
            inn = first + i
            if inn > len(ls):
                continue
            v = ls[inn - 1]
            txt = "?" if hide else ("-" if v is None else str(v))
            color = YELLOW if inn == cur and g.get("half") == ("top" if side == "away" else "bottom") else WHITE
            c.text_centered(y, txt, "4x6", color, x_inn + i * col_w, x_inn + (i + 1) * col_w)
        vals = [score_text(board, g, side), team.get("hits"), team.get("errors")]
        for j, v in enumerate(vals):
            txt = "?" if hide and j == 0 else ("" if v is None else str(v))
            color = score_color(board, g, side) if j == 0 else GRAY
            c.text_centered(y, txt, "4x6", color, x_r + j * stat_w, x_r + (j + 1) * stat_w)
    c.line(0, 21, W - 1, 21, LINE)
    # situation strip
    y = 23
    x = 1 + draw_inning(c, 1, y, g, font="5x8") + 4
    draw_diamond(c, x + 5, y, 4, 1, g.get("bases") or [False] * 3, home=False)
    x += 13
    if g.get("balls") is not None and g.get("strikes") is not None:
        x += c.text(x, y + 1, f"{g['balls']}-{g['strikes']}", "5x7", WHITE) + 3
    draw_dots(c, x, y + 3, g.get("outs"), 2, RED, size=3)
    x += 10
    text, color, lt = cycle(c, ticker_items(board, g), t, W - x, font="4x6")
    c.scroll_text(x, W, y + 2, text, "4x6", color, lt, speed=26, pause=0.8, gap=200)


def render_matchup(board, c, g, t, team_color, cycle):
    """Small logos + big scores on top; pitcher vs batter, diamond and count below."""
    W = c.width
    L = 16
    away, home = g["away"], g["home"]
    c.paste(small_logo(board, away, L, home), 0, 0)
    c.paste(small_logo(board, home, L, away), W - L, 0)
    a_s, h_s = score_text(board, g, "away"), score_text(board, g, "home")
    c.text(L + 2, 1, a_s, "7x13B", score_color(board, g, "away"))
    c.text_right(W - L - 2, 1, h_s, "7x13B", score_color(board, g, "home"))
    # inning + outs, centered
    mid = W // 2
    iw = 6 + c.text_width(str(g.get("period") or ""), "6x10") if not inning_label(g)[0] else \
        c.text_width(f"{inning_label(g)[0]} {g.get('period')}", "4x6")
    draw_inning(c, mid - (iw + 9) // 2, 2, g)
    draw_dots(c, mid - (iw + 9) // 2 + iw + 2, 6, g.get("outs"), 2, RED, size=3)
    c.line(0, 16, W - 1, 16, LINE)

    # bottom: diamond + count on the right, players (or last play) on the left
    # diamond tucked into the bottom-right corner, count to its left
    draw_diamond(c, W - 8, 19, 5, 2, g.get("bases") or [False] * 3, home=False)
    if g.get("balls") is not None and g.get("strikes") is not None:
        c.text_right(W - 17, 22, f"{g['balls']}-{g['strikes']}", "5x7", WHITE)
    area = W - 36
    players = []
    if g.get("pitcher"):
        players.append(("P", g["pitcher"], g.get("pitcher_summary", ""), GRAY))
    if g.get("batter"):
        players.append(("AB", g["batter"], g.get("batter_summary", ""), WHITE))
    show_play = board.cfg.get("show_last_play", True) and g.get("last_play")
    # alternate between the matchup (6 s) and the last play (time to scroll it once)
    play_w = c.text_width(g.get("last_play") or "", "5x7")
    play_d = 1.0 + max(0, play_w - area) / 26 + 1.0
    phase = t % (6 + (play_d if show_play else 0))
    if players and (phase < 6 or not show_play):
        for i, (lab, name, summ, color) in enumerate(players[:2]):
            y = 18 + i * 7
            x = c.text(0, y, lab, "4x6", YELLOW) + 3
            x += c.text(x, y, name, "4x6", color)
            if summ:
                c.text(x + 3, y, summ, "4x6", DIM if color == GRAY else GRAY, clip=(0, area))
    elif show_play:
        c.scroll_text(0, area, 21, g["last_play"], "5x7", ORANGE, max(0.0, phase - 6), speed=26, pause=1.0, gap=400)
    else:
        c.text(0, 21, board._clock_text(g), "5x7", YELLOW)


RENDERERS = {"scorebug": render_scorebug, "linescore": render_linescore, "matchup": render_matchup}
