"""Realistic sample games for previewing a league board (e.g. in the off-season).

Games are built in ESPN's scoreboard format and run through the normal parser, so a
preview exercises exactly the same code (and the board's current layout) as real games.
Team colors and logos come from ESPN's teams list, which is available all year.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams"

# league -> (away, home) used when no favorites are set, with fallback colors
DEFAULT_MATCHUPS = {
    "nfl": (("KC", "e31837"), ("BUF", "00338d")),
    "ncaaf": (("UGA", "ba0c2f"), ("ALA", "9e1b32")),
    "nba": (("GS", "1d428a"), ("LAL", "552583")),
    "wnba": (("NY", "6eceb2"), ("LV", "a7a8aa")),
    "ncaam": (("DUKE", "00539b"), ("UNC", "7bafd4")),
    "ncaaw": (("SC", "73000a"), ("UCONN", "000e2f")),
    "nhl": (("TOR", "00205b"), ("BUF", "003087")),
    "mlb": (("BOS", "bd3039"), ("NYY", "132448")),
    "mls": (("LA", "00245d"), ("MIA", "f7b5cd")),
    "epl": (("ARS", "ef0107"), ("LIV", "c8102e")),
    "laliga": (("BAR", "a50044"), ("RMA", "febe10")),
    "bundesliga": (("BVB", "fde100"), ("BAY", "dc052d")),
    "seriea": (("INT", "0068a8"), ("JUV", "000000")),
    "ligue1": (("OM", "2faee0"), ("PSG", "004170")),
    "ucl": (("RMA", "febe10"), ("MCI", "6cabdd")),
}

_team_cache: dict = {}


def fetch_teams(sport, league_path):
    """{abbr: {color, alt, logo, name}} from ESPN (cached; {} if unreachable)."""
    key = (sport, league_path)
    if key in _team_cache:
        return _team_cache[key]
    teams = {}
    try:
        r = requests.get(TEAMS_URL.format(sport=sport, league=league_path), params={"limit": 1000}, timeout=10)
        r.raise_for_status()
        for lg in (r.json().get("sports") or [{}])[0].get("leagues") or []:
            for t in lg.get("teams") or []:
                t = t.get("team", t)
                logos = t.get("logos") or []
                teams[(t.get("abbreviation") or "").upper()] = {
                    "color": t.get("color"), "alt": t.get("alternateColor"),
                    "logo": logos[0].get("href") if logos else None,
                    "name": t.get("shortDisplayName") or t.get("displayName") or "",
                    "id": str(t.get("id", "")),
                }
        _team_cache[key] = teams
    except Exception as e:  # off-line: fall back to built-in colors, no logos
        log.info("team list unavailable for %s/%s: %s", sport, league_path, e)
    return teams


def pick_teams(league, favorites, teams):
    """(away, home) abbreviations: favorites first, then the league's default matchup."""
    (da, _), (dh, _) = DEFAULT_MATCHUPS.get(league, (("AWAY", ""), ("HOME", "")))
    favs = [f for f in favorites if not teams or f in teams]
    if len(favs) >= 2:
        return favs[1], favs[0]
    if favs:
        home = favs[0]
        away = da if da != home else dh
        return away, home
    return da, dh


def _competitor(side, abbr, score, league, teams, extra=None):
    info = teams.get(abbr, {})
    fallback = dict(DEFAULT_MATCHUPS.get(league, ())).get(abbr, "808080")
    c = {"homeAway": side, "score": str(score), "records": [{"summary": "9-4" if side == "home" else "8-5"}],
         "team": {"id": info.get("id") or abbr, "abbreviation": abbr, "shortDisplayName": info.get("name") or abbr,
                  "color": info.get("color") or fallback, "alternateColor": info.get("alt") or "ffffff",
                  "logo": info.get("logo")}}
    c.update(extra or {})
    return c


def _ls(values):
    return [{"value": v} for v in values]


def make_event(league, sport, state, away, home, teams):
    """One ESPN-format event for a sample game in `state` (pre / in / post)."""
    now = datetime.now(timezone.utc)
    comp = {"status": {"type": {"state": state, "shortDetail": ""}, "period": 0, "displayClock": ""}}
    a_extra, h_extra, sit = {}, {}, {}
    sa, sh = 0, 0
    date = now
    if state == "pre":
        date = now + timedelta(hours=3)
        comp["status"]["type"]["shortDetail"] = "Upcoming"
        comp["broadcasts"] = [{"names": ["ESPN"]}]
        comp["odds"] = [{"details": f"{home} -3.5", "overUnder": {"football": 47.5, "basketball": 224.5,
                                                                    "hockey": 6.5, "baseball": 8.5}.get(sport, 2.5)}]
        if league in ("ncaaf", "ncaam", "ncaaw"):
            a_extra["curatedRank"], h_extra["curatedRank"] = {"current": 7}, {"current": 3}
    elif state == "post":
        sa, sh = {"football": (20, 27), "basketball": (108, 115), "hockey": (2, 4), "baseball": (3, 6)}.get(sport, (1, 2))
        h_extra["winner"] = True
        comp["status"] = {"type": {"state": "post", "shortDetail": "Final"}, "period": 4, "displayClock": "0:00"}
        if sport == "baseball":
            a_extra.update(hits=7, errors=1, linescores=_ls([0, 1, 0, 0, 2, 0, 0, 0, 0]))
            h_extra.update(hits=10, errors=0, linescores=_ls([1, 0, 0, 3, 0, 0, 2, 0]))
        if sport == "hockey":
            a_extra["linescores"], h_extra["linescores"] = _ls([1, 0, 1]), _ls([1, 2, 1])
    else:   # live
        if sport == "football":
            sa, sh = 17, 24
            comp["status"] = {"type": {"state": "in", "shortDetail": "5:21 - 3rd"}, "period": 3, "displayClock": "5:21"}
            sit = {"possession": teams.get(away, {}).get("id") or away, "down": 2, "distance": 7,
                   "possessionText": f"{home} 35", "shortDownDistanceText": "2nd & 7", "isRedZone": False,
                   "homeTimeouts": 2, "awayTimeouts": 3,
                   "lastPlay": {"text": "Pass complete to the 35 for a gain of 12 yards",
                                "probability": {"homeWinPercentage": 0.64, "awayWinPercentage": 0.36}}}
        elif sport == "baseball":
            sa, sh = 3, 5
            comp["status"] = {"type": {"state": "in", "shortDetail": "Bot 7th"}, "period": 7, "displayClock": ""}
            a_extra.update(hits=7, errors=0, linescores=_ls([0, 1, 0, 0, 2, 0, 0]))
            h_extra.update(hits=9, errors=1, linescores=_ls([1, 0, 0, 3, 0, 1]))
            sit = {"balls": 2, "strikes": 1, "outs": 1, "onFirst": True, "onThird": True,
                   "batter": {"athlete": {"shortName": "J. Smith"}, "summary": "2-3, HR"},
                   "pitcher": {"athlete": {"shortName": "M. Jones"}, "summary": "6.1 IP, 4 ER"},
                   "lastPlay": {"text": "Smith doubled to deep right, runner to third."}}
        elif sport == "hockey":
            sa, sh = 2, 3
            comp["status"] = {"type": {"state": "in", "shortDetail": "12:04 - 2nd"}, "period": 2, "displayClock": "12:04"}
            a_extra.update(linescores=_ls([1, 1]), statistics=[{"name": "shotsTotal", "displayValue": "21"}],
                           leaders=[{"abbreviation": "G", "leaders": [{"displayValue": "2", "athlete": {"shortName": "A. Wilson"}}]}])
            h_extra.update(linescores=_ls([2, 1]), statistics=[{"name": "shotsTotal", "displayValue": "28"}])
            sit = {"lastPlay": {"text": "Goal - wrist shot from the slot, assisted by Brown and Davis"}}
        elif sport == "basketball":
            sa, sh = 88, 91
            comp["status"] = {"type": {"state": "in", "shortDetail": "2:11 - 4th"}, "period": 4, "displayClock": "2:11"}
            a_extra["leaders"] = [{"abbreviation": "PTS", "leaders": [{"displayValue": "31", "athlete": {"shortName": "D. Miller"}}]}]
            h_extra["leaders"] = [{"abbreviation": "PTS", "leaders": [{"displayValue": "27", "athlete": {"shortName": "K. Moore"}}]}]
            sit = {"lastPlay": {"text": "Miller makes 26-foot three point jumper"}}
        else:   # soccer
            sa, sh = 1, 2
            comp["status"] = {"type": {"state": "in", "shortDetail": "67'"}, "period": 2, "displayClock": "67'"}
            comp["details"] = [
                {"team": {"id": teams.get(away, {}).get("id") or away}, "redCard": True},
                {"team": {"id": teams.get(home, {}).get("id") or home}, "scoringPlay": True,
                 "clock": {"displayValue": "61'"}, "athletesInvolved": [{"shortName": "L. Garcia"}]}]
        comp["situation"] = sit
    comp["competitors"] = [_competitor("home", home, sh if state != "pre" else "", league, teams, h_extra),
                           _competitor("away", away, sa if state != "pre" else "", league, teams, a_extra)]
    return {"id": f"preview-{state}", "date": date.isoformat(), "competitions": [comp]}
