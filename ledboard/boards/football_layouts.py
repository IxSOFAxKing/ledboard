"""Graphical live-game layouts for football (NFL / college).

Coordinates assume a 128x32 display but scale with the canvas where practical.
Field positions use 0-100: 0 = away end zone (left), 100 = home end zone (right).
"""
from __future__ import annotations

import colorsys
import time

from ..gfx import DIM, GRAY, RED, WHITE, YELLOW, fit_image, scale, trim_alpha

FIELD_GREEN = (0, 55, 0)
YARD_LINE = (0, 120, 0)
MIDFIELD = (90, 160, 90)
REDZONE = (70, 0, 0)
BALL = (190, 95, 30)
SCRIMMAGE = (60, 140, 255)
ORANGE = (255, 140, 0)

LAYOUTS = {
    "classic": "Classic (logos + rotating info line)",
    "field": "Field (classic + mini football field)",
    "scorebug": "Scorebug (TV-style team color panels)",
    "drive": "Drive (full-width field, win probability)",
}


# ---------------------------------------------------------------- shared pieces
def panel_color(team):
    """Team color usable as a background behind white text."""
    r, g, b = (x / 255 for x in team["color"])
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.2 and v > 0.7:                 # white / light gray -> try the alternate color
        r, g, b = (x / 255 for x in team["alt"])
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
    v = min(v, 0.62) if s > 0.2 else min(v, 0.35)
    v = max(v, 0.22)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    # light colors (pink, yellow, sky blue...) need extra darkening for white text to stand out
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if lum > 0.24:
        k = 0.24 / lum
        r, g, b = r * k, g * k, b * k
    return (int(r * 255), int(g * 255), int(b * 255))


def draw_field(c, x0, x1, y0, y1, g, team_color):
    """Mini football field between x0..x1 (inclusive) and y0..y1."""
    w = x1 - x0 + 1
    ez = max(2, round(w * 10 / 120))            # end zone width
    fx0, fx1 = x0 + ez, x1 - ez                  # playing field (goal line to goal line)
    fw = fx1 - fx0

    def X(pos):
        return fx0 + round(max(0, min(100, pos)) / 100 * fw)

    c.rect(x0, y0, x0 + ez - 1, y1, scale(team_color(g["away"]), 0.75))
    c.rect(x1 - ez + 1, y0, x1, y1, scale(team_color(g["home"]), 0.75))
    c.rect(fx0, y0, fx1, y1, FIELD_GREEN)
    if g.get("redzone") and g.get("possession"):
        if g["possession"] == "away":
            c.rect(X(80), y0, fx1, y1, REDZONE)
        else:
            c.rect(fx0, y0, X(20), y1, REDZONE)
    for yd in range(10, 100, 10):
        c.line(X(yd), y0, X(yd), y1, MIDFIELD if yd == 50 else YARD_LINE)
    if g.get("first_down_pos") is not None:
        fx = X(g["first_down_pos"])
        c.line(fx, y0, fx, y1, YELLOW)
    if g.get("ball_pos") is not None:
        bx = X(g["ball_pos"])
        c.line(bx, y0, bx, y1, SCRIMMAGE)
        mid = (y0 + y1) // 2
        # the ball: a small brown oval with a white lace, nudged toward the drive direction
        dx = 1 if g.get("possession") == "away" else -1
        cx = bx + dx * 2
        for px, py in ((cx - 1, mid), (cx, mid), (cx + 1, mid), (cx, mid - 1), (cx, mid + 1)):
            if y0 <= py <= y1:
                c.pixel(px, py, BALL)
        c.pixel(cx, mid, WHITE)


def draw_timeouts(c, x, y, n, width=2, gap=1, on=YELLOW, off=DIM, height=1):
    if not isinstance(n, int):
        return
    for i in range(3):
        xx = x + i * (width + gap)
        c.rect(xx, y, xx + width - 1, y + height - 1, on if i < n else off)


def draw_football(c, x, y):
    """5x3 football icon."""
    for px, py in ((x + 1, y), (x + 2, y), (x + 3, y), (x, y + 1), (x + 1, y + 1), (x + 2, y + 1),
                   (x + 3, y + 1), (x + 4, y + 1), (x + 1, y + 2), (x + 2, y + 2), (x + 3, y + 2)):
        c.pixel(px, py, BALL)
    c.pixel(x + 2, y + 1, WHITE)


def draw_arrow(c, x, y, direction, color):
    """Small 3x5 arrow pointing left (-1) or right (+1); (x, y) is the top of its back edge."""
    for i in range(3):
        col = x + i * direction
        c.line(col, y + i, col, y + 4 - i, color)


def down_text(g):
    if g.get("down") and g.get("distance") is not None:
        dist = "G" if "goal" in (g.get("down_text") or "").lower() else g["distance"]
        return f"{g['down']}&{dist}"
    from .sports import short_down
    return short_down(g.get("down_text") or "").split(" ")[0] if g.get("down_text") else ""


def score_text(board, g, side):
    if board.cfg.get("hide_favorite_score") and board.is_favorite(g):
        return "?"
    return str(g[side]["score"])


def score_color(board, g, side, default=WHITE):
    if board.cfg.get("score_flash", True) and int(time.monotonic() * 3) % 2 == 0 \
            and board.flash.get(f"{g['id']}|{side}", 0) > time.monotonic():
        return YELLOW
    return default


_logo_cache: dict = {}


def logo_fit(board, team, w, h=None):
    """Team logo scaled to fill a w x h box (transparent padding trimmed), from the hi-res copy."""
    h = h or w
    url = team.get("logo_url")
    src = getattr(board, "logo_src", {}).get(url) or board.logos.get(url)
    if src is None:
        return None
    key = (id(src), w, h)
    if key not in _logo_cache:
        if len(_logo_cache) > 300:
            _logo_cache.clear()
        _logo_cache[key] = fit_image(trim_alpha(src), w, h)
    return _logo_cache[key]


def small_logo(board, team, size):
    return logo_fit(board, team, size, size)


# ---------------------------------------------------------------- layouts
def render_field(board, c, g, t, team_color, cycle):
    """Logos at the edges, scores, a mini field, one combined info line."""
    W, H = c.width, c.height
    away, home = g["away"], g["home"]
    # logos fully on screen (they're trimmed, so any offset would clip real artwork)
    c.paste(logo_fit(board, away, H - 1, H), 0, 0)
    c.paste(logo_fit(board, home, H - 1, H), W - H + 1, 0)
    x0, x1 = H, W - H
    mid = (x0 + x1) // 2

    aw = c.text(x0 + 1, 0, away["abbr"], "5x8", team_color(away))
    hw = c.text_width(home["abbr"], "5x8")
    c.text(x1 - 1 - hw, 0, home["abbr"], "5x8", team_color(home))
    if g["possession"] == "away":
        draw_football(c, x0 + 2 + aw, 2)
    elif g["possession"] == "home":
        draw_football(c, x1 - 8 - hw, 2)
    draw_timeouts(c, x0 + 1, 9, g["timeouts"].get("away"))
    draw_timeouts(c, x1 - 9, 9, g["timeouts"].get("home"))

    font = "7x13B"
    dash = c.text_width("-", font)
    c.text(mid - dash // 2, 6, "-", font, GRAY)
    c.text_right(mid - dash // 2 - 1, 6, score_text(board, g, "away"), font, score_color(board, g, "away"))
    c.text(mid + dash // 2 + 2, 6, score_text(board, g, "home"), font, score_color(board, g, "home"))

    draw_field(c, x0 + 1, x1 - 2, 20, 24, g, team_color)

    info = board._clock_text(g)
    dd = down_text(g)
    items = [(f"{info} {dd}".strip(), RED if g.get("redzone") else YELLOW)]
    if board.cfg.get("show_last_play", True) and g.get("last_play"):
        items.append((g["last_play"], WHITE))
    text, color, lt = cycle(c, items, t, x1 - x0, font="4x6")
    c.scroll_text(x0, x1, H - 6, text, "4x6", color, lt, speed=28, align="center", pause=0.6, gap=200)


def render_scorebug(board, c, g, t, team_color, cycle):
    """TV-style: solid team-color panels with score + timeouts; center shows game situation."""
    W, H = c.width, c.height
    pw = 40 if W >= 128 else W // 3
    for side, px0 in (("away", 0), ("home", W - pw)):
        team = g[side]
        bg = panel_color(team)
        c.rect(px0, 0, px0 + pw - 1, H - 1, bg)
        # thin bright edge in the readable team color
        edge = px0 + pw - 1 if side == "away" else px0
        c.line(edge, 0, edge, H - 1, team_color(team))
        c.text_centered(1, team["abbr"], "5x8", WHITE, px0, px0 + pw)
        c.text_centered(10, score_text(board, g, side), "7x13B", score_color(board, g, side), px0, px0 + pw)
        n = g["timeouts"].get(side)
        if isinstance(n, int):
            bw = (pw - 10) // 3
            for i in range(3):
                bx = px0 + 4 + i * (bw + 1)
                c.rect(bx, H - 3, bx + bw - 2, H - 2, YELLOW if i < n else scale(bg, 0.5))
        if g["possession"] == side:
            draw_football(c, px0 + 2 if side == "away" else px0 + pw - 7, 3)

    cx0, cx1 = pw, W - pw
    c.text_centered(0, board._clock_text(g), "5x7", YELLOW, cx0, cx1)
    dd = down_text(g)
    if dd:
        c.text_centered(8, dd, "6x10", RED if g.get("redzone") else ORANGE, cx0, cx1)
    spot = (g.get("spot") or "").upper()
    if spot:
        c.text_centered(18, spot, "4x6", GRAY, cx0, cx1)
    draw_field(c, cx0 + 1, cx1 - 2, 25, 30, g, team_color)


def render_drive(board, c, g, t, team_color, cycle):
    """Small logos, big scores, drive arrow, win probability bar and a full-width field."""
    W, H = c.width, c.height
    away, home = g["away"], g["home"]
    L = 20
    c.paste(small_logo(board, away, L), 0, 0)
    c.paste(small_logo(board, home, L), W - L, 0)
    font = "7x13B"
    a_s, h_s = score_text(board, g, "away"), score_text(board, g, "home")
    c.text(L + 2, 2, a_s, font, score_color(board, g, "away"))
    c.text_right(W - L - 2, 2, h_s, font, score_color(board, g, "home"))
    a_end = L + 2 + c.text_width(a_s, font)
    h_start = W - L - 2 - c.text_width(h_s, font)

    cx0, cx1 = a_end + 2, h_start - 2
    c.text_centered(0, board._clock_text(g), "5x7", YELLOW, cx0, cx1)
    dd = down_text(g)
    spot = (g.get("spot") or "").replace(" ", "")
    line = " ".join(x for x in (dd, spot) if x)
    if line:
        c.text_centered(8, line, "5x7", RED if g.get("redzone") else ORANGE, cx0, cx1)
    if g["possession"] in ("away", "home"):
        d = 1 if g["possession"] == "away" else -1
        mid = (cx0 + cx1) // 2
        draw_arrow(c, mid - 1 if d > 0 else mid + 1, 15, d, team_color(g[g["possession"]]))
    # timeouts under each logo
    draw_timeouts(c, 1, L + 1, g["timeouts"].get("away"), width=5, gap=2)
    draw_timeouts(c, W - L + 1, L + 1, g["timeouts"].get("home"), width=5, gap=2)

    wp = g.get("win_prob")
    if wp and board.cfg.get("show_win_probability", True):
        a, h = wp
        total = (a + h) or 1
        split = round(W * a / total)
        c.rect(0, 23, split - 1, 23, team_color(away)) if split > 0 else None
        c.rect(split, 23, W - 1, 23, team_color(home)) if split < W else None
    draw_field(c, 0, W - 1, 25, H - 1, g, team_color)


RENDERERS = {"field": render_field, "scorebug": render_scorebug, "drive": render_drive}
