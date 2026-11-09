"""Playoff picture (seeds with clinch status) and, for the NFL, the playoff bracket (ESPN)."""
from __future__ import annotations

import logging
import math

import requests

from ..gfx import DIM, GRAY, WHITE, YELLOW
from . import offdays
from .offdays import _groups, _num, _stat, record_text

log = logging.getLogger(__name__)

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
# playoff spots per conference / league
SPOTS = {"nfl": 7, "nba": 10, "wnba": 8, "nhl": 8, "mlb": 6, "mls": 9}
ROWS = 4
GREEN = (60, 210, 90)
CLINCH = {
    "z": ("CLINCHED TOP SEED", (255, 200, 40)),
    "*": ("CLINCHED BYE", (255, 200, 40)),
    "p": ("PRESIDENTS' TROPHY", (255, 200, 40)),
    "y": ("CLINCHED DIVISION", GREEN),
    "x": ("CLINCHED PLAYOFFS", (120, 200, 120)),
    "w": ("CLINCHED WILD CARD", (120, 200, 120)),
    "pi": ("PLAY-IN", (255, 170, 60)),
    "pb": ("PLAY-IN", (255, 170, 60)),
    "e": ("ELIMINATED", (110, 60, 60)),
    "o": ("ELIMINATED", (110, 60, 60)),
}
HUNT = ("IN THE HUNT", (150, 150, 210))


def _abbr(e):
    return ((e.get("team") or {}).get("abbreviation") or "").upper()


def picture(sport, league_path, league_key, favorites):
    """{'group': 'NFC', 'rows': [...], 'teams': set(abbr)} for the conference/league holding the
    first favorite team (or the first group). Rows: rank, abbr, record, tag, label, color, fav, logo_url."""
    r = requests.get(offdays.STANDINGS_URL.format(sport=sport, league=league_path), timeout=15)
    r.raise_for_status()
    groups = []
    _groups(r.json(), groups)
    if not groups:
        return None
    favs = [f.upper() for f in favorites]
    chosen = None
    for fav in favs:
        cands = [g for g in groups if any(_abbr(e) == fav for e in g["standings"]["entries"])]
        if cands:
            chosen = max(cands, key=lambda g: len(g["standings"]["entries"]))     # the conference, not division
            break
    chosen = chosen or groups[0]
    entries = list(chosen["standings"]["entries"])
    seeded = all(_stat(e, "playoffSeed") for e in entries)
    if seeded:
        entries.sort(key=lambda e: _num(_stat(e, "playoffSeed"), 99) or 99)
    else:
        entries.sort(key=lambda e: -(_num(_stat(e, "winPercent"), 0) or 0))
    spots = SPOTS.get(league_key, 8)
    rows, hunt = [], 0
    for i, e in enumerate(entries):
        seed = int(_num(_stat(e, "playoffSeed"), i + 1) or i + 1) if seeded else i + 1
        clinch = ((_stat(e, "clincher") or {}).get("displayValue") or "").strip().lower()
        label, color = CLINCH.get(clinch, ("", GRAY))
        if seed > spots:
            if clinch in ("e", "o") or hunt >= 3:
                continue
            label, color = HUNT
            hunt += 1
        elif not label and league_key in ("nba",) and seed > 6:
            label, color = CLINCH["pi"]
        t = e.get("team") or {}
        logos = t.get("logos") or []
        rec, _extra = record_text(sport, e)
        rows.append({"rank": seed if seed <= spots else None, "abbr": _abbr(e), "record": rec, "label": label,
                     "color": color, "fav": _abbr(e) in favs,
                     "logo_url": t.get("logo") or (logos[0].get("href") if logos else None)})
    name = chosen.get("abbreviation") or chosen.get("shortName") or chosen.get("name") or "PLAYOFFS"
    return {"group": name.upper(), "rows": rows, "teams": {_abbr(e) for e in entries}}


def bracket(sport, league_path, league_key, conf_teams, parse_event):
    """NFL only: [(round label, [games])] for the favorite's conference, or None before the playoffs."""
    if league_key != "nfl" or not conf_teams:
        return None
    rounds = []
    for week, label in ((1, "WILD CARD"), (2, "DIVISIONAL"), (3, "CHAMP")):
        r = requests.get(SCOREBOARD_URL.format(sport=sport, league=league_path),
                         params={"seasontype": 3, "week": week}, timeout=15)
        r.raise_for_status()
        games = []
        for ev in r.json().get("events") or []:
            g = parse_event(ev, league_key)
            if not g:
                continue
            a, h = g["away"]["abbr"].upper(), g["home"]["abbr"].upper()
            if a in conf_teams and h in conf_teams:
                games.append(g)
        rounds.append((label, games))
    if not rounds[0][1]:            # wild-card matchups not set yet: no bracket
        return None
    return rounds


# ---------------------------------------------------------------- drawing
def render_picture(board, c, data, page):
    from .football_layouts import small_logo
    W = c.width
    rows = data["rows"]
    pages = max(1, math.ceil(len(rows) / ROWS))
    c.text(1, 0, f"{data['group']} PLAYOFF PICTURE", "4x6", YELLOW)
    if pages > 1:
        c.text_right(W - 1, 0, f"{page + 1}/{pages}", "4x6", DIM)
    for i, row in enumerate(rows[page * ROWS:(page + 1) * ROWS]):
        y = 7 + i * 6
        if row["fav"]:
            c.rect(0, y - 1, W - 1, y + 5, (25, 30, 45))
        c.text_right(8, y, str(row["rank"]) if row["rank"] else "-", "4x6", YELLOW if row["fav"] else GRAY)
        if row["logo_url"] in board.logos:
            logo = small_logo(board, {"logo_url": row["logo_url"]}, 6)
            if logo is not None:
                c.paste(logo, 11, y - 1)
        c.text(19, y, row["abbr"], "4x6", WHITE)
        c.text(39, y, row["record"], "4x6", GRAY)
        if row["label"]:
            c.text_right(W - 1, y, row["label"], "4x6", row["color"])


def _when(g):
    if g["state"] == "post":
        return None
    if g["state"] == "in":
        return "LIVE"
    if not g.get("start"):
        return "TBD"
    return g["start"].astimezone().strftime("%a %-I:%M").upper().replace(":00", "")


def render_bracket(board, c, rounds, group):
    from .football_layouts import small_logo
    W, H = c.width, c.height
    (wl, wc), (dl, div), (cl, champ) = rounds
    c.text(1, 0, group, "4x6", YELLOW)

    def logo(team, x, y, s):
        if team.get("logo_url") in board.logos:
            im = small_logo(board, team, s)
            if im is not None:
                c.paste(im, x, y)
                return
        c.text(x, y, team["abbr"][:1], "4x6", GRAY)

    def score_col(g, side):
        if g["state"] != "post":
            return WHITE
        other = "home" if side == "away" else "away"
        try:
            return WHITE if int(g[side]["score"]) > int(g[other]["score"]) else DIM
        except (TypeError, ValueError):
            return WHITE

    # wild card: up to 3 games
    for i, g in enumerate(wc[:3]):
        y = 7 + i * 8
        for k, side in enumerate(("away", "home")):
            x = 1 + k * 20
            logo(g[side], x, y, 6)
            txt = str(g[side]["score"]) if g["state"] != "pre" else ""
            c.text(x + 7, y + 1, txt, "4x6", score_col(g, side))
        if g["state"] == "pre":
            c.text(9, y + 1, _when(g) or "", "4x6", GRAY)
    c.line(42, 8, 42, H - 2, DIM)
    c.text(46, 0, "DIVISIONAL", "4x6", GRAY)
    for i in range(2):
        y = 9 + i * 11
        if i < len(div):
            g = div[i]
            logo(g["away"], 46, y, 8)
            c.text(56, y + 1, "v", "4x6", DIM)
            logo(g["home"], 62, y, 8)
            fav = board.is_favorite(g)
            txt = _when(g) or f"{g['away']['score']}-{g['home']['score']}"
            c.text(72, y + 1, txt, "4x6", YELLOW if fav else GRAY)
        else:
            c.text(46, y + 1, "TBD", "4x6", DIM)
    c.line(104, 8, 104, H - 2, DIM)
    c.text(108, 0, "CHAMP", "4x6", GRAY)
    if champ:
        g = champ[0]
        logo(g["away"], 107, 9, 8)
        logo(g["home"], 117, 9, 8)
        txt = _when(g) or f"{g['away']['score']}-{g['home']['score']}"
        c.text_centered(20, txt.split(" ")[0], "4x6", GRAY, 105, W)
    else:
        c.text_centered(12, "?", "7x13B", DIM, 105, W)
