"""Graphical live-game layouts for hockey (NHL), plus the goal celebration
used by hockey and soccer."""
from __future__ import annotations

import time

from ..gfx import DIM, GRAY, WHITE, YELLOW, scale
from .football_layouts import panel_color, score_color, score_text, small_logo

ORANGE = (255, 140, 0)
LINE = (45, 45, 45)
BOARDS = (90, 90, 110)
RED_LINE = (150, 0, 0)
BLUE_LINE = (0, 40, 170)
CREASE = (0, 70, 200)
GOAL_RED = (255, 20, 20)

LAYOUTS = {
    "classic": "Classic (logos + rotating info line)",
    "scorebug": "Scorebug (team bars, shots, big clock)",
    "periods": "Period Score (goals by period + SOG)",
    "rink": "Rink (mini hockey rink graphic)",
}


# ---------------------------------------------------------------- helpers
def ordinal(n):
    return {1: "1ST", 2: "2ND", 3: "3RD"}.get(n, f"{n}TH")


def period_label(g):
    """'2ND', 'OT', '2OT', 'SO', or 'END 2ND' / 'INT' during breaks."""
    n = int(g.get("period") or 0)
    detail = (g.get("detail") or "").upper()
    if "SO" in detail.split() or "SHOOTOUT" in detail:
        name = "SO"
    elif n > 3:
        name = "OT" if n == 4 else f"{n - 3}OT"
    else:
        name = ordinal(n) if n else ""
    if "END" in detail or "INT" in detail:
        return f"END {name}".strip()
    return name


def clock_text(g):
    detail = (g.get("detail") or "").upper()
    if "END" in detail or "INT" in detail:
        return "INT"
    return g.get("clock") or ""


def ticker_items(board, g):
    items = []
    if board.cfg.get("show_last_play", True) and g.get("last_play"):
        items.append((g["last_play"], WHITE))
    for side in ("away", "home"):
        t = g[side]
        for cat, name, val in t.get("leaders") or []:
            if cat.upper() in ("G", "GOALS", "PTS", "POINTS") and name:
                items.append((f"{t['abbr']} {name} {val} {cat.upper()}", GRAY))
                break
    sa, sh = g["away"].get("shots"), g["home"].get("shots")
    if sa is not None and sh is not None:
        items.append((f"SHOTS {sa}-{sh}", GRAY))
    if not items:
        items.append((f"{period_label(g)} {clock_text(g)}", YELLOW))
    return items


# ---------------------------------------------------------------- goal celebration
def goal_side(board, g, hold=5.0):
    """'away' / 'home' if that team scored within the last `hold` seconds."""
    now = time.monotonic()
    for side in ("home", "away"):
        until = board.flash.get(f"{g['id']}|{side}", 0)
        if until - now > 8 - hold:      # flash lasts 8 s from the moment the goal was seen
            return side
    return None


def render_goal(board, c, g, side, team_color):
    """Flashing goal light: team logo, GOAL!, and the new score."""
    W, H = c.width, c.height
    team = g[side]
    phase = int(time.monotonic() * 4) % 2
    bg = panel_color(team) if phase else (0, 0, 0)
    c.rect(0, 0, W - 1, H - 1, bg)
    # goal-light bars on the edges
    lamp = GOAL_RED if phase else scale(GOAL_RED, 0.3)
    c.rect(0, 0, 1, H - 1, lamp)
    c.rect(W - 2, 0, W - 1, H - 1, lamp)
    c.paste(board.logos.get(team.get("logo_url")), 3, 0)
    x0 = H + 4
    c.text_centered(1, "GOAL!", "7x13B", YELLOW if phase else WHITE, x0, W - 2)
    line = f"{g['away']['abbr']} {score_text(board, g, 'away')}  {g['home']['abbr']} {score_text(board, g, 'home')}"
    c.text_centered(17, line, "5x8", WHITE if phase else team_color(team), x0, W - 2)


# ---------------------------------------------------------------- layouts
def render_scorebug(board, c, g, t, team_color, cycle):
    W, H = c.width, c.height
    pw = 62
    for row, side in ((0, "away"), (1, "home")):
        team = g[side]
        y = row * 12
        bg = panel_color(team)
        c.rect(0, y, pw - 1, y + 11, bg)
        c.rect(0, y, 2, y + 11, team_color(team))
        c.paste(small_logo(board, team, 10), 4, y + 1)
        c.text(16, y + 1, team["abbr"], "6x10", WHITE)
        shots = team.get("shots")
        if shots is not None:
            c.text(40, y + 4, str(shots), "4x6", scale(WHITE, 0.7))
        c.text_right(pw - 2, y + 1, score_text(board, g, side), "6x10", score_color(board, g, side))
    if g["away"].get("shots") is not None:
        c.text(40, 25, "SOG", "4x6", DIM)
    # period + clock (right)
    rx0 = pw + 2
    c.text_centered(1, period_label(g), "5x8", YELLOW, rx0, W)
    c.text_centered(10, clock_text(g), "7x13B", WHITE, rx0, W)
    # ticker (bottom)
    text, color, lt = cycle(c, ticker_items(board, g), t, W - 56, font="4x6")
    c.scroll_text(56 if g["away"].get("shots") is not None else 0, W, H - 6, text, "4x6", color, lt,
                  speed=26, pause=0.8, gap=200)


def render_periods(board, c, g, t, team_color, cycle):
    W = c.width
    away, home = g["away"], g["home"]
    cur = int(g.get("period") or 1)
    n = max(3, cur, len(away.get("linescore") or []), len(home.get("linescore") or []))
    labels = ["1", "2", "3"] + (["OT" if i == 4 else f"{i - 3}OT" for i in range(4, n + 1)] if n > 3 else [])
    if "SO" in (g.get("detail") or "").upper().split() and labels[-1] != "SO":
        labels[-1] = "SO"
    labels = labels[-5:]                       # keep it to 5 columns max
    first = n - len(labels) + 1
    abbr_w, col_w, stat_w = 22, 12, 14
    xs = abbr_w
    x_t = xs + len(labels) * col_w + 4
    for i, lab in enumerate(labels):
        per = first + i
        c.text_centered(0, lab, "4x6", YELLOW if per == cur else GRAY, xs + i * col_w, xs + (i + 1) * col_w)
    c.text_centered(0, "T", "4x6", WHITE, x_t, x_t + stat_w)
    c.text_centered(0, "SOG", "4x6", GRAY, x_t + stat_w, x_t + 2 * stat_w + 4)
    c.line(0, 6, W - 1, 6, LINE)
    c.line(x_t - 2, 0, x_t - 2, 21, LINE)
    hide = board.cfg.get("hide_favorite_score") and board.is_favorite(g)
    for row, side in ((0, "away"), (1, "home")):
        team = g[side]
        y = 8 + row * 7
        c.paste(small_logo(board, team, 6), 1, y)
        c.text(9, y, team["abbr"], "4x6", team_color(team))
        ls = team.get("linescore") or []
        for i in range(len(labels)):
            per = first + i
            if per > len(ls):
                continue
            v = ls[per - 1]
            c.text_centered(y, "?" if hide else ("-" if v is None else str(v)), "4x6",
                            YELLOW if per == cur else WHITE, xs + i * col_w, xs + (i + 1) * col_w)
        c.text_centered(y, score_text(board, g, side), "4x6", score_color(board, g, side), x_t, x_t + stat_w)
        shots = team.get("shots")
        c.text_centered(y, "" if shots is None else str(shots), "4x6", GRAY, x_t + stat_w, x_t + 2 * stat_w + 4)
    c.line(0, 22, W - 1, 22, LINE)
    x = c.text(1, 24, period_label(g), "5x7", YELLOW) + 4
    x += c.text(x, 24, clock_text(g), "5x7", WHITE) + 5
    text, color, lt = cycle(c, ticker_items(board, g), t, W - x, font="4x6")
    c.scroll_text(x, W, 25, text, "4x6", color, lt, speed=26, pause=0.8, gap=200)


def draw_rink(c):
    W, H = c.width, c.height
    c.draw.rounded_rectangle((0, 0, W - 1, H - 1), radius=7, outline=BOARDS)
    mid = W // 2
    c.rect(mid - 1, 1, mid, H - 2, RED_LINE)                    # center red line
    c.draw.ellipse((mid - 6, H // 2 - 6, mid + 5, H // 2 + 5), outline=scale(BLUE_LINE, 0.8))
    bl = round(W * 25 / 200)                                     # blue lines 25 ft from center
    for x in (mid - bl - 2, mid + bl):
        c.rect(x, 1, x + 1, H - 2, BLUE_LINE)
    gl = round(W * 11 / 200)                                     # goal lines 11 ft from the boards
    for x, d in ((gl, 1), (W - 1 - gl, -1)):
        c.line(x, 3, x, H - 4, RED_LINE)
        # crease: small half circle in front of the net
        c.draw.arc((x - 4, H // 2 - 4, x + 4, H // 2 + 4), 270 if d > 0 else 90, 90 if d > 0 else 270, fill=CREASE)
        c.rect(x - 2 * d if d > 0 else x + 1, H // 2 - 2, x - 1 if d > 0 else x + 2, H // 2 + 2, GRAY)  # the net


def render_rink(board, c, g, t, team_color, cycle):
    W = c.width
    draw_rink(c)
    mid = W // 2
    L = 16
    # each team in its own end: logo, abbreviation, big score
    for side, lx, sx, align in (("away", 9, 27, "left"), ("home", W - 9 - L, W - 27, "right")):
        team = g[side]
        c.paste(small_logo(board, team, L), lx, 4)
        c.text_centered(22, team["abbr"], "4x6", team_color(team), lx - 2, lx + L + 2)
        s = score_text(board, g, side)
        col = score_color(board, g, side)
        if align == "left":
            c.rect(sx - 1, 7, sx + c.text_width(s, "7x13B"), 21, (0, 0, 0))
            c.text(sx, 7, s, "7x13B", col)
        else:
            c.rect(sx - c.text_width(s, "7x13B") - 1, 7, sx, 21, (0, 0, 0))
            c.text_right(sx, 7, s, "7x13B", col)
    # neutral zone: period, clock, shots on a dark plate so the lines don't clash
    c.rect(mid - 14, 3, mid + 13, 28, (0, 0, 0))
    c.text_centered(3, period_label(g), "5x7", YELLOW, mid - 14, mid + 14)
    c.text_centered(11, clock_text(g), "5x8", WHITE, mid - 14, mid + 14)
    sa, sh = g["away"].get("shots"), g["home"].get("shots")
    if sa is not None and sh is not None:
        c.text_centered(21, f"{sa}-{sh}", "4x6", GRAY, mid - 14, mid + 14)


RENDERERS = {"scorebug": render_scorebug, "periods": render_periods, "rink": render_rink}
