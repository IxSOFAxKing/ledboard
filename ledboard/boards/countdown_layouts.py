"""Extra designs for the next-game countdown (the original 'classic' one lives in offdays.py)."""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from ..gfx import DIM, GRAY, WHITE, YELLOW, scale

STYLES = {
    "classic": "Classic (logos, countdown, rotating info line)",
    "flip": "Flip clock (split-flap digits: days / hours / minutes)",
    "ticket": "Ticket (game ticket with a tear-off countdown stub)",
    "matchup": "Matchup (each half in its team's colors)",
    "road": "Road to kickoff (the ball travels toward the game over the last week)",
    "hype": "Game day hype (big GAME DAY on the day, flip clock before)",
}

BALL = {"football": ((150, 80, 30), (255, 255, 255)), "baseball": ((240, 240, 240), (220, 40, 40)),
        "basketball": ((240, 120, 30), (60, 30, 10)), "hockey": ((30, 30, 30), (140, 140, 140)),
        "soccer": ((240, 240, 240), (30, 30, 30))}
KICKOFF = {"football": "KICKOFF", "baseball": "FIRST PITCH", "basketball": "TIP-OFF", "hockey": "PUCK DROP",
           "soccer": "KICKOFF"}


# ---------------------------------------------------------------- helpers
def _secs(g):
    if not g.get("start"):
        return None
    return max(0, int((g["start"] - datetime.now(timezone.utc)).total_seconds()))


def _sides(g, fav):
    side = "home" if g["home"]["abbr"].upper() == fav else "away"
    other = "away" if side == "home" else "home"
    return side, other


def _when(g, fmt="%a %-I:%M%p"):
    if not g.get("start"):
        return "TBD"
    return g["start"].astimezone().strftime(fmt).upper().replace(":00", "")


def _vs(g, side, other):
    return ("@ " if side == "away" else "VS ") + g[other]["abbr"]


def _is_today(g):
    return bool(g.get("start")) and g["start"].astimezone().date() == datetime.now().astimezone().date()


def _info_line(g, side, other, t):
    items = [x for x in (_when(g), _vs(g, side, other), (g.get("note") or "").upper(),
                         (g.get("series") or "").upper(), (g.get("network") or "").upper()) if x]
    return items[int(t // 3) % len(items)] if items else ""


def _draw_info(board, c, g, text, x0, x1, y, color, font="5x7", t=0.0):
    """Info line text, or the TV network's logo when that's the item and logos are on."""
    logo = board._network_logo(g, text) if hasattr(board, "_network_logo") else None
    if logo is not None:
        c.paste(logo, x0 + (x1 - x0 - logo.width) // 2, y)
    else:
        c.scroll_text(x0, x1, y, text, font, color, t % 3, speed=30, align="center", pause=0.3)


def _flip_tiles(c, x, y, digits, t):
    """Split-flap tiles: two digits per group, groups separated by colons. Returns the width."""
    tw, th = 9, 15
    x0 = x
    for gi, pair in enumerate(digits):
        if gi:
            if int(t * 2) % 2 == 0:
                c.rect(x + 1, y + 4, x + 2, y + 5, GRAY)
                c.rect(x + 1, y + 9, x + 2, y + 10, GRAY)
            x += 5
        for ch in pair:
            c.rect(x, y, x + tw - 1, y + th - 1, (32, 32, 38))
            c.rect(x, y, x + tw - 1, y, (55, 55, 62))
            c.text(x + 1, y + 1, ch, "7x13B", WHITE)
            c.line(x, y + th // 2, x + tw - 1, y + th // 2, (0, 0, 0))      # the flap seam
            x += tw + 1
        x -= 1
    return x - x0


def _countdown_groups(secs):
    """(['03', '14', '27'], ['DAYS', 'HRS', 'MIN']) or (hours, min, sec) in the last day."""
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return [f"{min(d, 99):02d}", f"{h:02d}", f"{m:02d}"], ["DAYS", "HRS", "MIN"]
    return [f"{h:02d}", f"{m:02d}", f"{s:02d}"], ["HRS", "MIN", "SEC"]


def _short_countdown(secs):
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}D {h}H" if d < 3 else f"{d} DAYS"
    return f"{h}:{m:02d}" if h else f"{m}M"


# ---------------------------------------------------------------- designs
def render_flip(board, c, g, fav, t, team_color):
    from .football_layouts import logo_fit
    W, H = c.width, c.height
    side, other = _sides(g, fav)
    me, opp = g[side], g[other]
    L = 28
    c.paste(logo_fit(board, me, L, L, opp), 0, 2)
    c.paste(logo_fit(board, opp, L, L, me), W - L, 2)
    secs = _secs(g)
    x0, x1 = L + 2, W - L - 2
    if secs is None:
        c.text_centered(8, "TBD", "7x13B", WHITE, x0, x1)
    else:
        digits, labels = _countdown_groups(secs)
        width = 3 * 19 + 2 * 5
        x = x0 + (x1 - x0 - width) // 2
        _flip_tiles(c, x, 2, digits, t)
        for i, lab in enumerate(labels):
            gx = x + i * 24
            c.text_centered(18, lab, "4x6", GRAY, gx, gx + 19)
    _draw_info(board, c, g, _info_line(g, side, other, t), x0, x1, H - 7,
               team_color(me) if int(t // 3) % 3 == 1 else GRAY, t=t)


def render_ticket(board, c, g, fav, t, team_color):
    from .football_layouts import small_logo
    W, H = c.width, c.height
    side, other = _sides(g, fav)
    me, opp = g[side], g[other]
    col = team_color(me)
    stub = 88
    c.draw.rounded_rectangle((0, 0, W - 1, H - 1), radius=3, outline=col)
    for y in (0, H - 1):                                   # notches where the stub tears off
        c.draw.ellipse((stub - 3, y - 3, stub + 3, y + 3), fill=(0, 0, 0))
    for y in range(4, H - 4, 3):                           # perforation
        c.pixel(stub, y, scale(col, 0.6))
    # main part: logos, matchup, date / time / network
    c.paste(small_logo(board, me, 12, opp), 4, 3)
    x = 18
    x += c.text(x, 4, me["abbr"], "6x10", col) + 3
    x += c.text(x, 6, "@" if side == "away" else "VS", "4x6", GRAY) + 3
    x += c.text(x, 4, opp["abbr"], "6x10", team_color(opp)) + 3
    c.paste(small_logo(board, opp, 12, me), min(x, stub - 15), 3)
    if g.get("start"):
        local = g["start"].astimezone()
        c.text(5, 17, local.strftime("%a %b %-d").upper(), "4x6", WHITE)
        c.text(5, 24, local.strftime("%-I:%M %p").replace(":00", ""), "4x6", GRAY)
    net = (g.get("network") or "").upper()
    if net:
        logo = board._network_logo(g, net) if hasattr(board, "_network_logo") else None
        if logo is not None:
            c.paste(logo, stub - 4 - logo.width, 22)
        else:
            c.text_right(stub - 4, 24, net, "4x6", GRAY)
    # stub: the countdown
    secs = _secs(g)
    c.text_centered(3, "IN", "4x6", DIM, stub + 1, W - 1)
    if secs is None:
        c.text_centered(11, "TBD", "6x10", WHITE, stub + 1, W - 1)
    else:
        d = secs // 86400
        if d:
            c.text_centered(9, str(d), "7x13B", YELLOW, stub + 1, W - 1)
            c.text_centered(23, "DAY" if d == 1 else "DAYS", "4x6", GRAY, stub + 1, W - 1)
        else:
            h, m = secs // 3600, secs % 3600 // 60
            c.text_centered(11, f"{h}:{m:02d}", "6x10", YELLOW, stub + 1, W - 1)
            c.text_centered(23, "HRS", "4x6", GRAY, stub + 1, W - 1)


def render_matchup(board, c, g, fav, t, team_color):
    from .football_layouts import logo_fit, panel_color
    W, H = c.width, c.height
    side, other = _sides(g, fav)
    me, opp = g[side], g[other]
    left, right = (g["away"], g["home"])            # keep away on the left like every other screen
    mid = W // 2
    c.rect(0, 0, mid - 1, H - 1, scale(panel_color(left), 0.75))
    c.rect(mid, 0, W - 1, H - 1, scale(panel_color(right), 0.75))
    L = 28
    c.paste(logo_fit(board, left, L, L, right), 3, 2)
    c.paste(logo_fit(board, right, L, L, left), W - L - 3, 2)
    # centre plate
    px0, px1 = mid - 25, mid + 24
    c.draw.rounded_rectangle((px0, 1, px1, H - 2), radius=4, fill=(0, 0, 0))
    secs = _secs(g)
    c.text_centered(2, f"{left['abbr']} @ {right['abbr']}", "4x6", GRAY, px0, px1 + 1)
    cd = "TBD" if secs is None else _short_countdown(secs)
    font = "7x13B" if c.text_width(cd, "7x13B") <= px1 - px0 - 2 else "6x10"
    c.text_centered(9, cd, font, YELLOW if secs is not None and secs < 86400 else WHITE, px0, px1 + 1)
    _draw_info(board, c, g, _when(g, "%a %-I:%M%p") if int(t // 3) % 2 == 0 or not g.get("network")
               else (g.get("network") or "").upper(), px0, px1 + 1, H - 8, WHITE, font="4x6", t=t)


def render_road(board, c, g, fav, t, team_color):
    from .football_layouts import small_logo
    W, H = c.width, c.height
    side, other = _sides(g, fav)
    me, opp = g[side], g[other]
    sport = getattr(board, "sport", "football")
    c.paste(small_logo(board, me, 10, opp), 1, 0)
    x = 13 + c.text(13, 2, me["abbr"], "5x7", team_color(me)) + 3
    x += c.text(x, 2, "@" if side == "away" else "VS", "4x6", GRAY) + 3
    c.text(x, 2, opp["abbr"], "5x7", team_color(opp))
    c.text_right(W - 1, 2, _when(g, "%a %-I%p"), "4x6", GRAY)
    # the road: one tick per day over the last week, the ball moving toward the goal
    secs = _secs(g)
    rx0, rx1, ry = 3, W - 14, 18
    c.line(rx0, ry, rx1, ry, (60, 60, 60))
    for dday in range(8):
        tx = rx1 - round(dday / 7 * (rx1 - rx0))
        c.line(tx, ry - 1, tx, ry + 1, GRAY if dday else YELLOW)
    # the destination: the opponent's logo
    c.paste(small_logo(board, opp, 11, me), W - 12, ry - 5)
    frac = 0.0 if secs is None else max(0.0, min(1.0, 1 - secs / (7 * 86400)))
    bx = rx0 + round(frac * (rx1 - rx0))
    c.line(rx0, ry, bx, ry, scale(team_color(me), 0.9))
    body, lace = BALL.get(sport, BALL["football"])
    bob = round(math.sin(time.monotonic() * 4))
    c.draw.ellipse((bx - 3, ry - 3 + bob, bx + 3, ry + 1 + bob), fill=body)
    c.pixel(bx, ry - 1 + bob, lace)
    if secs is not None:
        d, rem = divmod(secs, 86400)
        h, m = rem // 3600, rem % 3600 // 60
        txt = (f"{d}D {h}H {m}M" if d else f"{h}H {m:02d}M") + f" TO {KICKOFF.get(sport, 'GAME TIME')}"
    else:
        txt = "TIME TBD"
    c.text_centered(H - 7, txt, "5x7", WHITE if secs is None or secs > 86400 else YELLOW)


def render_hype(board, c, g, fav, t, team_color):
    if not _is_today(g):
        render_flip(board, c, g, fav, t, team_color)
        return
    from .football_layouts import logo_fit, panel_color
    W, H = c.width, c.height
    side, other = _sides(g, fav)
    me, opp = g[side], g[other]
    phase = int(t * 2) % 2
    c.rect(0, 0, W - 1, H - 1, panel_color(me) if phase else (0, 0, 0))
    L = 30
    c.paste(logo_fit(board, me, L, L, opp), 1, 1)
    c.paste(logo_fit(board, opp, L, L, me), W - L - 1, 1)
    x0, x1 = L + 2, W - L - 2
    c.text_centered(0, "GAME DAY", "7x13B", YELLOW if phase else team_color(me), x0, x1)
    secs = _secs(g) or 0
    if secs > 0:
        h, m = secs // 3600, secs % 3600 // 60
        line = f"IN {h}:{m:02d}" if h else f"IN {m} MIN"
    else:
        line = "STARTING"
    c.text_centered(14, line, "5x7", WHITE, x0, x1)
    items = [_when(g, "%-I:%M%p"), _vs(g, side, other)] + ([(g.get("network") or "").upper()] if g.get("network") else [])
    _draw_info(board, c, g, items[int(t // 3) % len(items)], x0, x1, H - 8, team_color(me), t=t)


RENDERERS = {"flip": render_flip, "ticket": render_ticket, "matchup": render_matchup, "road": render_road,
             "hype": render_hype}
