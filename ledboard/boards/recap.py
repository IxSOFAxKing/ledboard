"""Scoring play recap: shown right after a favorite-team celebration.

The play comes from ESPN's game summary (scoringPlays; drive stats for football), falling
back to the scoreboard's last play when the summary isn't available.
"""
from __future__ import annotations

import logging
import time

import requests

from ..gfx import DIM, GRAY, WHITE

log = logging.getLogger(__name__)

SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/summary"
HEADLINE = {"touchdown": "TOUCHDOWN", "field_goal": "FIELD GOAL", "home_run": "HOME RUN", "grand_slam": "GRAND SLAM",
            "run": "RUN SCORES", "goal": "GOAL"}
SAMPLE_PLAYS = {
    "touchdown": ("J. Love 24 yd pass to C. Watson (B. McManus kick)", "8 PL · 75 YD"),
    "field_goal": ("B. McManus 47 yd field goal is GOOD", "6 PL · 38 YD"),
    "home_run": ("A. Judge homers to center field (431 ft)", ""),
    "grand_slam": ("A. Judge hits a grand slam to left field, 4 runs score", ""),
    "run": ("G. Stanton singles to right, A. Volpe scores", ""),
    "goal": ("Goal by T. Thompson, assisted by R. Dahlin and J. Cozens", ""),
}
DURATION = 10.0


class Recap:
    """Intro slide that follows a Celebration."""
    intro = True

    def __init__(self, kind, side, game, text, drive=""):
        self.kind, self.side, self.game = kind, side, game
        self.text, self.drive = text, drive
        self.created = time.time()

    def __eq__(self, other):
        return self is other

    def __hash__(self):
        return id(self)

    def __repr__(self):
        return f"Recap({self.kind}, {self.text[:30]!r})"


def fetch_play(sport, league_path, event_id, team_id, fallback=""):
    """(play text, drive summary) for the team's latest scoring play."""
    try:
        r = requests.get(SUMMARY_URL.format(sport=sport, league=league_path), params={"event": event_id}, timeout=10)
        r.raise_for_status()
        j = r.json()
    except Exception as e:
        log.info("game summary unavailable: %s", e)
        return fallback, ""
    plays = [p for p in j.get("scoringPlays") or []
             if not team_id or str((p.get("team") or {}).get("id", "")) == str(team_id)]
    text = (plays[-1].get("text") or "").strip() if plays else ""
    drive = ""
    if sport == "football":
        prev = (j.get("drives") or {}).get("previous") or []
        mine = [d for d in prev if not team_id or str((d.get("team") or {}).get("id", "")) == str(team_id)]
        if mine:
            d = mine[-1]
            plays_n, yds = d.get("offensivePlays"), d.get("yards")
            if plays_n is not None and yds is not None:
                drive = f"{plays_n} PL · {yds} YD"
            elif d.get("description"):
                drive = d["description"].upper()
    return text or fallback, drive


def render(board, c, r, t, team_color, clock_text):
    from .football_layouts import panel_color, small_logo
    W, H = c.width, c.height
    g, side = r.game, r.side
    team = g[side]
    other = g["home" if side == "away" else "away"]
    from ..gfx import readable
    panel = panel_color(team)
    from .sports import accent_color
    stripe = accent_color(team)
    if sum(abs(a - b) for a, b in zip(stripe, panel)) < 120:      # stripe would vanish on the panel
        stripe = team_color(team)
    c.rect(0, 0, W - 1, 8, panel)
    c.rect(0, 8, W - 1, 8, stripe)
    head = f"{HEADLINE.get(r.kind, 'SCORE')} {(team.get('name') or team['abbr']).upper()}"
    clk = clock_text(g) or ""
    cw = c.text_width(clk, "5x7")
    c.scroll_text(2, W - cw - 4, 1, head, "5x7", WHITE, t)
    c.text_right(W - 2, 1, clk, "5x7", (255, 215, 90))
    logo = small_logo(board, team, 22, other)
    x0 = 2
    if logo is not None:
        c.paste(logo, 2, 10)
        x0 = 27
    c.scroll_text(x0, W, 11, r.text or HEADLINE.get(r.kind, ""), "5x8", WHITE, t, speed=24, pause=0.8)
    x = x0
    for tm, col in ((g["away"], team_color(g["away"])), (g["home"], team_color(g["home"]))):
        x += c.text(x, 22, tm["abbr"], "6x10", col) + 2
        x += c.text(x, 22, str(tm["score"]), "6x10", WHITE if tm is team else GRAY) + 6
    if r.drive and x + c.text_width(r.drive, "4x6") < W - 1:
        c.text_right(W - 2, 24, r.drive, "4x6", DIM)


def duration(r, canvas_width=128):
    """Long enough for the play to scroll by once."""
    from .. import fonts
    w = fonts.get("5x8").width(r.text or "")
    return max(DURATION, 0.8 + (w - (canvas_width - 27)) / 24 + 2.0)
