"""Extra layouts for the Plex board. 'classic' (now playing and recently added) is drawn in plex.py.

Several use the item's wide background art (Plex's "fanart", the picture behind a movie or show in Plex),
prepared once per item: 'dim' (behind text), 'full' (Cinema) and 'banner' (fading into black on its left).
"""
from __future__ import annotations

import time
from datetime import datetime

from PIL import Image, ImageEnhance

from ..gfx import DIM, GRAY, WHITE, fmt_ms, scale, trim_alpha

PLEX_ORANGE = (229, 160, 13)

LAYOUTS = {
    "classic": "Classic (poster, title, episode, who's watching, progress bar)",
    "cinema": "Cinema (the background art, bright, with a slim bar for the title and time left)",
    "backdrop": "Backdrop (the background art, dimmed, behind the poster and text)",
    "endsat": "Ends at (when it finishes, big, with the time left)",
    "details": "Stream details (quality, direct play or transcode, who and on what)",
    "streams": "All streams (everyone watching, one per row, when two or more are)",
}
RECENT_LAYOUTS = {
    "classic": "Classic (poster, title, details, when it was added)",
    "backdrop": "Backdrop (the background art behind the poster and text)",
    "wall": "Poster wall (every new poster side by side on one screen, titles scrolling below)",
}
FANART_LAYOUTS = {"cinema", "backdrop"}


def prepare_fanart(src, W, H):
    """{'dim', 'full', 'banner'} from a wide background picture, or None."""
    if src is None:
        return None
    src = trim_alpha(src)
    if src.width < 8 or src.height < 4:
        return None
    src = src.convert("RGB")
    h = max(H, round(src.height * W / src.width))
    band = src.resize((W, h), Image.LANCZOS)
    top = (h - H) // 3                                   # a little above the middle: faces are usually there
    band = band.crop((0, top, W, top + H))
    bw = max(1, min(W, round(src.width * H / src.height)))
    banner = ImageEnhance.Brightness(src.resize((bw, H), Image.LANCZOS)).enhance(0.8).convert("RGBA")
    fade = max(1, bw * 2 // 3)
    mask = Image.new("L", (bw, H), 255)
    for x in range(fade):
        mask.paste(round(255 * (x / fade) ** 1.8), (x, 0, x + 1, H))
    banner.putalpha(mask)
    return {"dim": ImageEnhance.Brightness(band).enhance(0.36).convert("RGBA"),
            "full": ImageEnhance.Brightness(band).enhance(0.85).convert("RGBA"), "banner": banner}


def _shadow(c, x0, x1, y, text, font, color, t):
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1), (-1, 1), (1, -1), (-1, -1)):
        c.scroll_text(x0 + dx, x1 + dx, y + dy, text, font, (0, 0, 0), t)
    c.scroll_text(x0, x1, y, text, font, color, t)


def _poster(c, s, dark=False):
    """The poster at the left; returns where the text starts."""
    H = c.height
    art = s.get("art")
    if art is None:
        c.text(2, (H - 8) // 2, "PLEX", "5x8", PLEX_ORANGE)
        return 26
    if dark:
        c.rect(0, 0, art.width, H - 1, (0, 0, 0))
    c.paste(art, 0, 0)
    return art.width + 3


def _bar(board, c, x0, x1, H, s, slide, frac):
    if board.cfg.get("progress_style", "wavy") == "wavy":
        c.wavy_progress(x0, x1, H - 4, frac, PLEX_ORANGE, amp=board._wave.value(slide, s["state"] == "playing"))
        return H - 12
    c.progress(x0, H - 3, x1, frac, PLEX_ORANGE)
    return H - 10


def _clock(board, epoch):
    clock = board.app.boards.get("clock")
    h24 = bool(clock and clock.cfg.get("format_24h"))
    dt = datetime.fromtimestamp(epoch)
    return dt.strftime("%H:%M") if h24 else dt.strftime("%I:%M").lstrip("0") + ("AM" if dt.hour < 12 else "PM")


# -- now playing -------------------------------------------------------------------------
def render_cinema(board, c, s, offset, slide, t):
    W, H = c.width, c.height
    fan = (s.get("fanart") or {}).get("full")
    if fan is None:
        return board._render_classic(c, s, offset, slide, t)
    c.paste(fan, 0, 0)
    bar = H - 9
    c.rect(0, bar, W - 1, H - 1, (0, 0, 0))
    if s["duration"]:                                       # the top line of the bar is the progress
        c.line(0, bar, W - 1, bar, scale(PLEX_ORANGE, 0.25))
        end = int((W - 1) * offset / s["duration"])
        if end > 0:
            c.line(0, bar, end, bar, PLEX_ORANGE)
    right = "PAUSED" if s["state"] != "playing" else ("-" + fmt_ms(s["duration"] - offset) if s["duration"] else "")
    rw = c.text_width(right, "5x7") if right else 0
    c.scroll_text(2, W - rw - 6, bar + 2, s["title"], "5x7", WHITE, t)
    if right:
        c.text_right(W - 2, bar + 2, right, "5x7", PLEX_ORANGE)


def render_backdrop(board, c, s, offset, slide, t):
    W, H = c.width, c.height
    fan = (s.get("fanart") or {}).get("dim")
    if fan is not None:
        c.paste(fan, 0, 0)
    x0 = _poster(c, s, dark=fan is not None)
    txt = _shadow if fan is not None else (lambda c_, *a: c_.scroll_text(*a))
    txt(c, x0, W, 1, s["title"], "6x10", WHITE, t)
    txt(c, x0, W, 11, s["subtitle"], "5x8", PLEX_ORANGE, t)
    frac = offset / s["duration"] if s["duration"] else 0
    ty = _bar(board, c, x0, W - 1, H, s, slide, frac) if s["duration"] else H - 7
    label = "PAUSED" if s["state"] != "playing" else (
        s["user"] if board.cfg.get("show_user", True) and s["user"] else fmt_ms(offset))
    txt(c, x0, W, ty, label, "4x6", (215, 215, 215) if fan is not None else GRAY, t)
    if s["duration"]:
        rem = "-" + fmt_ms(s["duration"] - offset)
        rw = c.text_width(rem, "4x6")
        if c.text_width(label, "4x6") + rw + 4 < W - x0:
            txt(c, W - 1 - rw, W, ty, rem, "4x6", (215, 215, 215) if fan is not None else GRAY, 0)


def render_endsat(board, c, s, offset, slide, t):
    W, H = c.width, c.height
    x0 = _poster(c, s)
    c.scroll_text(x0, W, 0, s["title"], "5x7", WHITE, t)
    if not s["duration"]:
        c.text(x0, 10, fmt_ms(offset), "7x13B", PLEX_ORANGE)
        return
    left = max(0, s["duration"] - offset) / 1000
    if s["state"] == "playing":
        c.text(x0, 8, "ENDS AT", "4x6", GRAY)
        when = _clock(board, time.time() + left)
        font = "7x13B" if c.text_width(when, "7x13B") <= W - x0 - 1 else "6x10"
        c.text(x0, 14, when, font, PLEX_ORANGE)
    else:
        c.text(x0, 8, "PAUSED", "4x6", (220, 140, 0))
        c.text(x0, 14, fmt_ms(offset), "7x13B", GRAY)
    mins = int(left // 60)
    rem = f"{mins // 60}H {mins % 60:02d}M LEFT" if mins >= 60 else f"{mins}M LEFT"
    frac = offset / s["duration"]
    c.progress(x0, H - 2, W - 1, frac, PLEX_ORANGE, height=2)
    rw = c.text_width(rem, "4x6")
    if c.text_width("ENDS AT", "4x6") + rw + 4 < W - x0:
        c.text_right(W - 1, 8, rem, "4x6", GRAY)


def render_details(board, c, s, offset, slide, t):
    W, H = c.width, c.height
    x0 = _poster(c, s)
    c.scroll_text(x0, W, 0, s["title"], "5x7", WHITE, t)
    c.scroll_text(x0, W, 8, s["subtitle"] or (s.get("type") or "").upper(), "4x6", GRAY, t)
    # quality and how it's being streamed
    dec = s.get("decision") or ""
    dcol = {"DIRECT PLAY": (60, 200, 90), "DIRECT STREAM": (80, 170, 255), "TRANSCODE": (255, 120, 40)}.get(dec, GRAY)
    x = x0
    if s.get("quality"):
        qw = c.text_width(s["quality"], "4x6")
        c.rect(x, 14, x + qw + 2, 21, scale(PLEX_ORANGE, 0.8), fill=False)
        c.text(x + 2, 15, s["quality"], "4x6", PLEX_ORANGE)
        x += qw + 6
    if dec:
        c.scroll_text(x, W, 15, dec + (" · " + s["remote"] if s.get("remote") else ""), "4x6", dcol, t)
    who = " · ".join(b for b in ((s["user"] if board.cfg.get("show_user", True) else ""), s.get("device")) if b)
    if s["state"] != "playing":
        who = "PAUSED" + (" · " + who if who else "")
    c.scroll_text(x0, W, 23, who.upper(), "4x6", (220, 140, 0) if s["state"] != "playing" else scale(WHITE, 0.7), t)
    if s["duration"]:
        c.progress(x0, H - 2, W - 1, offset / s["duration"], PLEX_ORANGE, height=1)


def render_streams(board, c, streams, page, t):
    """Up to three streams, one per row: tiny poster, who, what, and a progress line under the row."""
    W = c.width
    rows = streams[page * 3:page * 3 + 3]
    for i, s in enumerate(rows):
        y = 1 + i * 11
        small = s.get("art_small")
        if small is not None:
            c.paste(small, 1, y)
        else:
            c.rect(1, y, 6, y + 8, scale(PLEX_ORANGE, 0.4))
        x = (small.width if small is not None else 6) + 4
        who = (s["user"] or s.get("device") or "?").upper()
        who = who if c.text_width(who, "4x6") <= 36 else who[:9]
        ww = c.text(x, y + 1, who, "4x6", PLEX_ORANGE)
        what = s["title"] + (f" · {s['subtitle']}" if s.get("type") == "episode" and s["subtitle"] else "")
        c.scroll_text(x + ww + 3, W, y, what, "5x7", WHITE if s["state"] == "playing" else GRAY, t)
        if s["duration"]:
            off = board._offset(s)
            c.line(x, y + 9, W - 1, y + 9, DIM)
            end = x + int((W - 1 - x) * off / s["duration"])
            if end > x:
                c.line(x, y + 9, end, y + 9, PLEX_ORANGE if s["state"] == "playing" else GRAY)


RENDERERS = {"cinema": render_cinema, "backdrop": render_backdrop, "endsat": render_endsat, "details": render_details}


# -- recently added ----------------------------------------------------------------------------
def _added(item):
    ago = max(0, time.time() - item["added"])
    return "JUST ADDED" if ago < 3600 else (f"ADDED {int(ago // 3600)}H AGO" if ago < 86400 else
                                             f"ADDED {int(ago // 86400)}D AGO")


def render_recent_backdrop(board, c, item, t):
    W, H = c.width, c.height
    fan = (item.get("fanart") or {}).get("dim")
    if fan is None:
        return board._render_recent(c, item, t)
    c.paste(fan, 0, 0)
    x0 = _poster(c, item, dark=True)
    _shadow(c, x0, W, 0, item["tag"], "4x6", PLEX_ORANGE, 0)
    _shadow(c, x0, W, 7, item["title"], "6x10", WHITE, t)
    _shadow(c, x0, W, 18, item["sub"].upper(), "4x6", (215, 215, 215), t)
    _shadow(c, x0, W, H - 6, _added(item), "4x6", (150, 150, 150), 0)


def render_wall(board, c, items, t):
    """Every new poster side by side; a black strip at the bottom scrolls their titles."""
    W, H = c.width, c.height
    arts, total = [], -1
    for it in items:
        art = it.get("art")
        if art is None:
            continue
        if total + 1 + art.width > W:
            break
        arts.append(art)
        total += art.width + 1
    x = max(0, (W - total) // 2)                          # centered
    for art in arts:
        c.paste(art, x, 0)
        x += art.width + 1
    x = len(arts)
    if x == 0:
        c.text_centered(10, "RECENTLY ADDED", "5x7", PLEX_ORANGE)
    c.rect(0, H - 8, W - 1, H - 1, (0, 0, 0))
    c.line(0, H - 8, W - 1, H - 8, scale(PLEX_ORANGE, 0.7))
    titles = "   ·   ".join(it["title"] for it in items)
    c.text(1, H - 6, "NEW", "4x6", PLEX_ORANGE)
    c.scroll_text(15, W, H - 6, titles, "4x6", WHITE, t, pause=0.8)
