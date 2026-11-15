"""Extra 'now playing' layouts for the Steam board. 'classic' is drawn in steam.py."""
from __future__ import annotations

import time

from ..gfx import GRAY, WHITE, scale

INGAME_GREEN = (144, 186, 60)
STEAM_BLUE = (102, 192, 244)
GOLD = (255, 190, 40)

LAYOUTS = {
    "classic": "Classic (game art, name, session time and total hours)",
    "cinema": "Cinema (the game's hero banner, bright, with a slim bar for the name and time)",
    "banner": "Banner (hero on the right fading into black, text on the left)",
    "session": "Session timer (a big play-time clock with the game art)",
    "stats": "Stats card (total hours, last 2 weeks, achievement progress)",
    "party": "Party (everyone in the house who's playing, one per row)",
}
HERO_LAYOUTS = {"cinema", "banner"}


def _session(p):
    return max(0, int(time.time() - p.get("since", time.time())))


def _clock(secs):
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _hrs(minutes):
    h = (minutes or 0) / 60
    return f"{h:,.1f}" if h < 10 else f"{round(h):,}"


def _text(board, hero):
    """Outlined text over a picture, plain text otherwise."""
    if hero is not None:
        return board._shadow_scroll
    return lambda c, *a, **k: c.scroll_text(*a, **k)


def _art(board, c, p, hero=None):
    """Game art at the left (with a dark edge over a hero), or the Steam badge."""
    H = c.height
    art = p.get("art")
    if art is None:
        board._steam_badge(c, 2, 2, H - 4)
        return
    if hero is not None:
        bb = art.getbbox() or (0, 0, art.width, art.height)
        c.rect(0, 0, min(c.width - 1, bb[2]), H - 1, (0, 0, 0))
    c.paste(art, 0, 0)


def render_cinema(board, c, p, t):
    W, H = c.width, c.height
    full = p.get("hero_full")
    if full is None:
        return board._render_classic(c, p, t)
    c.paste(full, 0, 0)
    bar = H - 9
    c.rect(0, bar, W - 1, H - 1, (0, 0, 0))
    c.line(0, bar, W - 1, bar, scale(INGAME_GREEN, 0.6))
    clock = _clock(_session(p))
    cw = c.text_width(clock, "5x7")
    c.scroll_text(2, W - cw - 6, bar + 2, p["game"] or "In game", "5x7", WHITE, t)
    c.text_right(W - 2, bar + 2, clock, "5x7", INGAME_GREEN)


def render_banner(board, c, p, t):
    W, H = c.width, c.height
    banner = p.get("hero_banner")
    if banner is None:
        return board._render_classic(c, p, t)
    c.paste(banner, W - banner.width, 0)
    txt = board._shadow_scroll
    x1 = W - 2
    txt(c, 2, x1, 1, p["game"] or "In game", "6x10", WHITE, t)
    txt(c, 2, x1, 12, f"PLAYING · {_clock(_session(p))}", "5x8", INGAME_GREEN, t)
    bits = [p["name"]]
    if p.get("total_minutes"):
        bits.append(f"{_hrs(p['total_minutes'])} hrs total")
    txt(c, 2, x1, H - 7, "  ·  ".join(bits), "4x6", (215, 215, 215), t)


def render_session(board, c, p, t):
    W, H = c.width, c.height
    hero = p.get("hero")
    if hero is not None:
        c.paste(hero, 0, 0)
    _art(board, c, p, hero)
    txt = _text(board, hero)
    x0 = H + 3
    txt(c, x0, W, 0, p["game"] or "In game", "5x7", WHITE, t)
    clock = _clock(_session(p))
    font = "9x15B" if c.text_width(clock, "9x15B") <= W - x0 else "7x13B"
    txt(c, x0, W, 9, clock, font, INGAME_GREEN, 0)
    foot = f"{_hrs(p['total_minutes'])} HRS TOTAL" if p.get("total_minutes") else p["name"].upper()
    txt(c, x0, W, H - 6, foot, "4x6", GRAY if hero is None else (215, 215, 215), t)


def render_stats(board, c, p, t):
    W, H = c.width, c.height
    hero = p.get("hero")
    if hero is not None:
        c.paste(hero, 0, 0)
    _art(board, c, p, hero)
    txt = _text(board, hero)
    x0 = H + 3
    txt(c, x0, W, 0, p["game"] or "In game", "5x7", WHITE, t)
    total = p.get("total_minutes")
    if total:
        big = _hrs(total)
        txt(c, x0, W, 8, big, "6x10", WHITE, 0)
        txt(c, x0 + c.text_width(big, "6x10") + 3, W, 11, "HRS TOTAL", "4x6", GRAY if hero is None else (200, 200, 200), 0)
    recent = p.get("recent_minutes")
    line3 = f"{_hrs(recent)} HRS IN 2 WEEKS" if recent else f"PLAYING {_clock(_session(p))}"
    txt(c, x0, W, 19, line3, "4x6", INGAME_GREEN, t)
    ach = p.get("achievements")
    if ach and ach[1]:
        got, all_ = ach
        label = f"{got}/{all_}"
        lw = c.text_width(label, "4x6")
        txt(c, x0, W, H - 6, label, "4x6", GOLD, 0)
        bx0, bx1 = x0 + lw + 3, W - 2
        if bx1 - bx0 > 6:
            c.rect(bx0, H - 5, bx1, H - 2, (0, 0, 0) if hero is not None else (35, 30, 15))
            c.rect(bx0, H - 5, bx1, H - 2, scale(GOLD, 0.35), fill=False)
            fill = bx0 + round((bx1 - bx0) * got / all_)
            if fill > bx0:
                c.rect(bx0, H - 5, fill, H - 2, GOLD)
    else:
        txt(c, x0, W, H - 6, p["name"].upper(), "4x6", GRAY if hero is None else (215, 215, 215), t)


def render_party(board, c, players, t):
    """Up to three players, one per row: avatar, name, and the game (or their status)."""
    W = c.width
    for i, p in enumerate(players[:3]):
        y = 1 + i * 11
        av = p.get("avatar_small")
        if av is not None:
            c.paste(av, 1, y)
        else:
            c.rect(1, y, 9, y + 8, scale(STEAM_BLUE, 0.4))
        name = (p["name"] or "?").upper()
        name = name if c.text_width(name, "5x7") <= 44 else name[:7]
        nw = c.text(13, y + 1, name, "5x7", WHITE)
        if p["appid"]:
            clock = _clock(_session(p))
            cw = c.text_width(clock, "4x6")
            c.scroll_text(13 + nw + 4, W - cw - 7, y + 1, p["game"] or "In game", "5x7", INGAME_GREEN, t)
            c.text_right(W - 1, y + 2, clock, "4x6", GRAY)
        else:
            from .steam import STATES
            c.text(13 + nw + 4, y + 1, STATES.get(p["state"], "ONLINE"), "5x7", STEAM_BLUE)


RENDERERS = {"cinema": render_cinema, "banner": render_banner, "session": render_session, "stats": render_stats}
