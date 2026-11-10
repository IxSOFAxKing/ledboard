"""Live scores from ESPN's public scoreboard API (no API key required).

Every league is its own board (NFL, NBA, MLB...), so each one has its own
place in the rotation, its own favorites, scroll mode, takeover and display
options. All of ESPN's fields are treated as optional: when a field is missing
the corresponding detail is simply not drawn.
"""
from __future__ import annotations

import colorsys
import logging
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from .base import Board, organize
from ..gfx import DIM, GRAY, RED, WHITE, YELLOW, fetch_image, fit_image, hex_to_rgb, readable, scale

log = logging.getLogger(__name__)

ESPN = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"

# key: (sport, espn league path, display name, extra query params when favorites are set)
CELEBRATED = ("football", "hockey", "soccer", "baseball")
SIM_KINDS = {
    "football": {"touchdown": "Touchdown", "field_goal": "Field goal"},
    "hockey": {"goal": "Goal"},
    "soccer": {"goal": "Goal"},
    "baseball": {"home_run": "Home run", "grand_slam": "Grand slam"},
}

LEAGUES = {
    "nfl": ("football", "nfl", "NFL", {}),
    "ncaaf": ("football", "college-football", "College Football", {"groups": "80"}),
    "nba": ("basketball", "nba", "NBA", {}),
    "wnba": ("basketball", "wnba", "WNBA", {}),
    "ncaam": ("basketball", "mens-college-basketball", "Men's College Basketball", {"groups": "50"}),
    "ncaaw": ("basketball", "womens-college-basketball", "Women's College Basketball", {"groups": "50"}),
    "nhl": ("hockey", "nhl", "NHL", {}),
    "mlb": ("baseball", "mlb", "MLB", {}),
    "mls": ("soccer", "usa.1", "MLS", {}),
    "epl": ("soccer", "eng.1", "Premier League", {}),
    "laliga": ("soccer", "esp.1", "La Liga", {}),
    "bundesliga": ("soccer", "ger.1", "Bundesliga", {}),
    "seriea": ("soccer", "ita.1", "Serie A", {}),
    "ligue1": ("soccer", "fra.1", "Ligue 1", {}),
    "ucl": ("soccer", "uefa.champions", "Champions League", {}),
}
COLLEGE = {"ncaaf", "ncaam", "ncaaw"}
DEFAULT_ON = {"nfl", "nba", "mlb", "nhl"}
PLAYOFF_LEAGUES = {"nfl", "nba", "wnba", "nhl", "mlb", "mls"}
ORANGE = (255, 140, 0)
INFO_SECONDS = 3.0      # how long each line of the rotating info line shows
INFO_SPEED = 28.0       # px/s for info lines too long to fit


# ---------------------------------------------------------------------------- parsing
def parse_time(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def _athlete(obj):
    """Short name from a situation.batter / pitcher style object."""
    if not isinstance(obj, dict):
        return ""
    a = obj.get("athlete") or obj
    return a.get("shortName") or a.get("displayName") or ""


def _linescore(items):
    """[{'value': 1.0}, ...] -> [1, ...]; None when ESPN doesn't provide one."""
    if not isinstance(items, list) or not items:
        return None
    out = []
    for it in items:
        v = it.get("value", it.get("displayValue")) if isinstance(it, dict) else it
        try:
            out.append(int(float(v)))
        except (TypeError, ValueError):
            out.append(None)
    return out


def _half(detail):
    d = (detail or "").strip().lower()
    for prefix, half in (("top", "top"), ("bot", "bottom"), ("mid", "mid"), ("end", "end")):
        if d.startswith(prefix):
            return half
    return None


def _stat(competitor, *names):
    for st in competitor.get("statistics") or []:
        if st.get("name") in names or st.get("abbreviation") in names:
            return st.get("displayValue")
    return None


def parse_event(ev, league_key):
    """Normalise one ESPN event into a compact dict (pure function, easy to test)."""
    comp = (ev.get("competitions") or [{}])[0]
    status = comp.get("status") or ev.get("status") or {}
    stype = status.get("type", {})
    teams = {}
    for c in comp.get("competitors", []):
        if not isinstance(c, dict):
            continue
        t = c.get("team") if isinstance(c.get("team"), dict) else {}
        # scoreboard: "records" + "summary"; team schedule: "record" + "displayValue"
        rec = [r for r in (c.get("records") or c.get("record") or []) if isinstance(r, dict)] \
            if isinstance(c.get("records") or c.get("record"), list) else []
        side = c.get("homeAway", "home")
        cr = c.get("curatedRank")
        rank = cr.get("current") if isinstance(cr, dict) else None
        leaders = []
        for cat in c.get("leaders") or []:
            if not isinstance(cat, dict):
                continue
            top = (cat.get("leaders") or [{}])[0]
            if isinstance(top, dict) and top.get("athlete"):
                leaders.append((cat.get("abbreviation") or cat.get("shortDisplayName") or cat.get("name", ""),
                                _athlete(top), top.get("displayValue", "")))
        teams[side] = {
            "id": str(t.get("id", c.get("id", ""))),
            "abbr": t.get("abbreviation") or (t.get("shortDisplayName") or "?")[:4].upper(),
            "name": t.get("shortDisplayName") or t.get("displayName") or "",
            "score": _score(c.get("score", "")),
            "color": hex_to_rgb(t.get("color"), GRAY),
            "alt": hex_to_rgb(t.get("alternateColor"), WHITE),
            "_has_color": bool(t.get("color")),
            "logo_url": t.get("logo") or ((t.get("logos") or [{}])[0].get("href")),
            "record": (rec[0].get("summary") or rec[0].get("displayValue") or "") if rec else "",
            "winner": bool(c.get("winner")),
            "rank": rank if isinstance(rank, int) and 0 < rank <= 25 else None,
            "hits": c.get("hits"),
            "errors": c.get("errors"),
            "shots": _stat(c, "shotsTotal", "shots", "shotsOnGoal", "SOG"),
            # soccer match stats (None when ESPN doesn't send them)
            "possession": _fnum(_stat(c, "possessionPct", "possession")),
            "shots_total": _fnum(_stat(c, "totalShots", "shotsTotal")),
            "shots_on": _fnum(_stat(c, "shotsOnTarget", "shotsOnGoal")),
            "corners": _fnum(_stat(c, "wonCorners", "cornerKicks", "corners")),
            "fouls": _fnum(_stat(c, "foulsCommitted", "fouls")),
            "saves": _fnum(_stat(c, "saves")),
            "yellow_cards": 0,
            "leaders": leaders,
            "red_cards": 0,
            "linescore": _linescore(c.get("linescores")),
        }
    if "home" not in teams or "away" not in teams:
        return None

    sit = comp.get("situation") if isinstance(comp.get("situation"), dict) else {}
    odds = (comp.get("odds") or [{}])[0] if isinstance(comp.get("odds"), list) else {}
    network = ""
    for b in comp.get("broadcasts") or []:
        if not isinstance(b, dict):
            continue
        if b.get("names"):
            network = b["names"][0]
            break
        if isinstance(b.get("media"), dict) and b["media"].get("shortName"):   # team schedule format
            network = b["media"]["shortName"]
            break
    if not network:
        for b in comp.get("geoBroadcasts") or []:
            network = (b.get("media") or {}).get("shortName", "")
            if network:
                break
    if not network and isinstance(comp.get("broadcast"), str):
        network = comp["broadcast"]

    game = {
        "id": str(ev.get("id")),
        "league": league_key,
        "sport": LEAGUES[league_key][0],
        "state": stype.get("state", "pre"),          # pre | in | post
        "detail": stype.get("shortDetail") or stype.get("detail") or "",
        "description": stype.get("description", ""),
        "period": status.get("period", 0) or 0,
        "clock": status.get("displayClock", ""),
        "start": parse_time(ev.get("date") or comp.get("date")),
        "away": teams["away"],
        "home": teams["home"],
        "time_tbd": comp.get("timeValid") is False,
        "network": network,
        "odds": odds.get("details", "") if isinstance(odds, dict) else "",
        "over_under": odds.get("overUnder") if isinstance(odds, dict) else None,
        # live situation (football / baseball / some basketball)
        "possession": None,
        "down": sit.get("down"),
        "distance": sit.get("distance"),
        "spot": sit.get("possessionText", ""),
        "down_text": sit.get("shortDownDistanceText") or sit.get("downDistanceText") or "",
        "redzone": bool(sit.get("isRedZone")),
        "timeouts": {"home": sit.get("homeTimeouts"), "away": sit.get("awayTimeouts")},
        "outs": sit.get("outs"),
        "balls": sit.get("balls"),
        "strikes": sit.get("strikes"),
        "bases": [bool(sit.get("onFirst")), bool(sit.get("onSecond")), bool(sit.get("onThird"))],
        "batter": _athlete(sit.get("batter")),
        "pitcher": _athlete(sit.get("pitcher")),
        "batter_summary": (sit.get("batter") or {}).get("summary", "") if isinstance(sit.get("batter"), dict) else "",
        "pitcher_summary": (sit.get("pitcher") or {}).get("summary", "") if isinstance(sit.get("pitcher"), dict) else "",
        "half": _half(stype.get("shortDetail") or stype.get("detail") or ""),
        "last_play": ((sit.get("lastPlay") or {}).get("text") or "").strip(),
        "last_goal": "",
        # playoffs: series standing ("STL leads 2-1") and round / game note ("West 1st Round - Game 3")
        "series": _series(comp),
        "note": _note(comp) or ({"baseball": "SPRING TRAINING"}.get(LEAGUES[league_key][0], "PRESEASON")
                                if _season_type(ev) == "1" else ""),
        "postseason": _postseason(ev, comp),
        "preseason": _season_type(ev) == "1",
        "season_year": _season_year(ev),
    }
    poss = str(sit.get("possession", "") or "")
    if poss:
        game["possession"] = ("home" if poss == teams["home"]["id"]
                              else "away" if poss == teams["away"]["id"] else None)
    game.update(field_position(game))
    prob = (sit.get("lastPlay") or {}).get("probability") or {}
    hw, aw = prob.get("homeWinPercentage"), prob.get("awayWinPercentage")
    game["win_prob"] = (float(aw), float(hw)) if isinstance(hw, (int, float)) and isinstance(aw, (int, float)) else None
    # soccer: goals / cards are listed in competition "details"
    game["events"] = []
    for d in comp.get("details") or []:
        if not isinstance(d, dict):
            continue
        tid = str((d.get("team") or {}).get("id", ""))
        side = "home" if tid == teams["home"]["id"] else "away" if tid == teams["away"]["id"] else None
        clock = d.get("clock") or {}
        minute_txt = clock.get("displayValue", "")
        who = _athlete((d.get("athletesInvolved") or [{}])[0])
        if d.get("redCard") and side:
            teams[side]["red_cards"] += 1
        if d.get("yellowCard") and side:
            teams[side]["yellow_cards"] += 1
        kind = ("goal" if d.get("scoringPlay") else "red" if d.get("redCard") else
                "yellow" if d.get("yellowCard") else None)
        if kind and side:
            game["events"].append({"kind": kind, "side": side, "minute": _minute(minute_txt, clock.get("value")),
                                   "minute_txt": minute_txt, "player": who,
                                   "own_goal": bool(d.get("ownGoal")), "penalty": bool(d.get("penaltyKick"))})
        if d.get("scoringPlay"):
            game["last_goal"] = " ".join(x for x in ("GOAL", teams[side]["abbr"] if side else "", who, minute_txt) if x)
    game["minute"] = _minute(game["clock"]) if game["sport"] == "soccer" else None
    return game


def _series(comp):
    se = comp.get("series")
    if isinstance(se, dict):
        txt = (se.get("summary") or se.get("description") or "").strip()
        txt = re.sub(r"(?i)\bseries\s+tied\b", "TIED", txt)            # 'Series tied 2-2' -> 'TIED 2-2'
        return re.sub(r"(?i)\s*\bseries\b", "", txt).strip().upper()  # 'STL leads series 2-1' -> 'STL LEADS 2-1'
    return ""


def _note(comp):
    for n in comp.get("notes") or []:
        if isinstance(n, dict) and n.get("headline"):
            return short_note(n["headline"])
    return ""


_NOTE_SUBS = [
    (r"\s*-\s*Game\s+(\d+)", r" G\1"), (r"\bGame\s+(\d+)", r"G\1"),
    (r"\b1st Round\b", "RD1"), (r"\b2nd Round\b", "RD2"), (r"\bFirst Round\b", "RD1"),
    (r"\bSecond Round\b", "RD2"), (r"\bWild ?Card Round\b", "WILD CARD"), (r"\bDivisional Round\b", "DIVISIONAL"),
    (r"\bConference Finals?\b", "CONF FINAL"), (r"\bConference Semifinals?\b", "CONF SEMI"),
    (r"\bChampionship\b", "CHAMP"), (r"\bEastern\b", "EAST"), (r"\bWestern\b", "WEST"),
    (r"\bAmerican League\b", "AL"), (r"\bNational League\b", "NL"), (r"\bPlayoffs?\b", ""),
    (r"\s+", " "),
]


def short_note(text):
    """'West 1st Round - Game 4' -> 'WEST RD1 G4' (fits the panel's info line)."""
    out = text or ""
    for pat, rep in _NOTE_SUBS:
        out = re.sub(pat, rep, out, flags=re.I)
    return out.strip(" -·").upper()


def _season_year(ev):
    season = ev.get("season") if isinstance(ev.get("season"), dict) else {}
    try:
        return int(season.get("year"))
    except (TypeError, ValueError):
        return None


def _season_type(ev):
    st = ev.get("seasonType") if isinstance(ev.get("seasonType"), dict) else {}
    season = ev.get("season") if isinstance(ev.get("season"), dict) else {}
    return str(st.get("type") or season.get("type") or "")


def _postseason(ev, comp):
    st = ev.get("seasonType") if isinstance(ev.get("seasonType"), dict) else {}
    season = ev.get("season") if isinstance(ev.get("season"), dict) else {}
    kind = st.get("type") or season.get("type")
    return str(kind) == "3" or isinstance(comp.get("series"), dict)


def _score(v):
    """Scoreboard scores are strings; team schedules send {"value": 3.0, "displayValue": "3"}."""
    if isinstance(v, dict):
        return v.get("displayValue") or ("" if v.get("value") is None else str(int(v["value"])))
    return v


def _fnum(v):
    try:
        return float(str(v).rstrip("%"))
    except (TypeError, ValueError):
        return None


def _minute(text, seconds=None):
    """Match minute from ESPN's '67'' / '45'+2'' style clock (or a seconds value)."""
    m = re.match(r"\s*(\d+)'?\s*(?:\+\s*(\d+))?", text or "")
    if m:
        return int(m.group(1)) + int(m.group(2) or 0)
    try:
        return int(float(seconds) // 60) + 1 if seconds is not None else None
    except (TypeError, ValueError):
        return None


def field_position(g):
    """Ball spot and first-down line on a 0-100 scale: 0 = away end zone (left), 100 = home end zone (right).

    The away team attacks to the right, the home team to the left, matching where the logos sit.
    """
    out = {"ball_pos": None, "first_down_pos": None}
    m = re.match(r"\s*([A-Za-z]+)?\s*(\d+)\s*$", g.get("spot") or "")
    if not m:
        return out
    side, yard = (m.group(1) or "").upper(), int(m.group(2))
    if yard == 50 or not side:
        pos = 50
    elif side == g["home"]["abbr"].upper():
        pos = 100 - yard          # on the home team's half (right side)
    elif side == g["away"]["abbr"].upper():
        pos = yard                # on the away team's half (left side)
    else:
        return out
    out["ball_pos"] = pos
    dist = g.get("distance")
    goal = "goal" in (g.get("down_text") or "").lower()
    if g.get("possession") in ("home", "away"):
        sign = 1 if g["possession"] == "away" else -1
        if goal:
            out["first_down_pos"] = 100 if sign > 0 else 0
        elif isinstance(dist, (int, float)):
            out["first_down_pos"] = max(0, min(100, pos + sign * dist))
    return out


def short_down(text):
    """'2nd & 7 at KC 35' -> '2&7 KC35' so it fits between the logos."""
    m = re.match(r"\s*(\d)\w*\s*&\s*(\w+)(?:\s+at\s+(\w+)\s*(\d+))?", text or "")
    if not m:
        return text
    out = f"{m.group(1)}&{m.group(2).replace('Goal', 'G')}"
    if m.group(3):
        out += f" {m.group(3)}{m.group(4)}"
    return out


def _neutral(c):
    """White, black or gray (no real hue)."""
    if not c:
        return True
    _h, s, _v = colorsys.rgb_to_hsv(*(x / 255 for x in c[:3]))
    return s < 0.18


def _team_colors(team):
    """(main, other) raw team colors, preferring real colors over white / black / gray."""
    def val(c):
        _h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in c))
        return v * (0.5 + s / 2)
    col, alt = team.get("color") or (128, 128, 128), team.get("alt") or (255, 255, 255)
    real = [x for x in (col, alt) if not _neutral(x)]
    if len(real) == 2:
        main = alt if val(col) < 0.35 and val(alt) > val(col) else col
        return main, (col if main is alt else alt)
    if len(real) == 1:
        return real[0], None                 # the other one is white / black / gray
    return (col if val(col) >= val(alt) else alt), None


def team_color(team):
    """Pick whichever of the team's colors is most visible on black, then brighten it. A white or
    gray color is only used when the team has no real color."""
    return readable(_team_colors(team)[0], min_value=0.9)


def accent_color(team):
    """The team's second color for stripes and trims: the other real team color, never white;
    a darker shade of the main color when the team has only one."""
    main, other = _team_colors(team)
    if other is not None:
        return readable(other, min_value=0.6)
    return scale(readable(main, min_value=0.9), 0.55)


def _snapshot(g):
    """Copy of a game dict that later score updates won't change."""
    out = dict(g)
    out["home"], out["away"] = dict(g["home"]), dict(g["away"])
    return out


def _sample_game(league, fav=None):
    """A stand-in game for Simulate when ESPN has no games for this league right now."""
    home = fav or "HOME"
    return {"id": "sim", "league": league, "sport": LEAGUES[league][0], "state": "in", "detail": "", "period": 2,
            "clock": "", "start": None, "last_play": "", "possession": None,
            "home": {"id": "h", "abbr": home, "name": home, "score": "10", "color": (0, 90, 200), "alt": (255, 255, 255),
                     "logo_url": None, "record": "", "winner": False, "rank": None},
            "away": {"id": "a", "abbr": "AWAY" if home != "AWAY" else "OPP", "name": "", "score": "7",
                     "color": (200, 30, 30), "alt": (255, 255, 255), "logo_url": None, "record": "", "winner": False,
                     "rank": None}}


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------- board
FINAL_STYLES = {
    "plain": "Plain (no accents)",
    "winner": "Winner color (winning score, underline and FINAL in the winner's color)",
    "splitbar": "Team split bar (bar under the score split by points, records in team colors)",
    "tint": "Tinted halves (each side washed in its team color)",
    "banner": "Winner banner (team-colored 'GB WINS' banner + arrow)",
    "trim": "Team trim lines (team-color lines above and below the score)",
}

UPCOMING_STYLES = {
    "plain": "Plain (no accents)",
    "badge": "Matchup badge ('@' in a two-team color badge, records in team colors)",
    "splitbar": "Team split bar (half-and-half bar under the start time, records in team colors)",
    "tint": "Tinted halves (each side washed in its team color)",
    "banner": "Info banner (bottom line on a two-team color banner)",
    "trim": "Team trim lines (team-color lines above and below)",
}

from .countdown_layouts import STYLES as COUNTDOWN_STYLES  # noqa: E402
from .pregame_layouts import LAYOUTS as PREGAME_LAYOUTS  # noqa: E402

# How a league board's settings are grouped in the web UI (top to bottom).
LEAGUE_LAYOUT = [
    ("Teams", ["favorites", "favorites_only", "takeover", "hide_favorite_score"]),
    ("Preview", ["preview"]),
    ("Which games", ["live_only", "show_final", "final_hours", "show_pregame", "upcoming_hours", "max_games"]),
    ("Between games", ["next_game", "next_game_style", "offseason_countdown", "opener_days", "show_standings",
                       "show_playoffs"]),
    ("Upcoming games", ["upcoming_layout", "upcoming_style", "show_records", "show_network", "network_logos", "show_odds"]),
    ("Live games", ["live_layout", "live_style", "detailed_live", "show_win_probability", "show_last_play",
                    "score_flash", "goal_animation"]),
    ("Final scores", ["final_style", "show_final_records"]),
    ("Celebrations", ["celebrations", "celebrate_field_goals", "celebrate_runs", "recap", "simulate"]),
    ("Logos", ["show_abbreviations", "logo_version", "logo_overrides", "logo_detail", "logo_enhance", "logo_edges", "use_dark_logos"]),
    ("Text colors", ["text_score", "text_status", "text_info", "text_loser", "abbr_color_mode", "abbr_color"]),
    ("Timing", ["duration", "refresh", "live_refresh"]),
]
LEAGUE_LABELS = {
    "upcoming_style": "Team color accents",
    "live_style": "Team color accents (Classic layout)",
    "final_style": "Team color accents",
    "show_final_records": "Show each team's updated record",
    "show_records": "Show team records",
    "show_network": "Show the TV network",
    "max_games": "Most games per rotation",
    "duration": "Seconds per screen",
    "preview": "Show sample games",
}

LIVE_STYLES = {
    "plain": "Plain (no accents)",
    "leader": "Leader color (leading team's score in its color, with underlines)",
    "splitbar": "Team split bar (bar under the score split by points)",
    "tint": "Tinted halves (each side washed in its team color, leader brighter)",
    "banner": "Info banner (clock / info line on a two-team color banner)",
    "trim": "Team trim lines (team-color lines above and below)",
}


# Teams whose ESPN "dark background" logo is an all-white version; always use their color logo.
COLOR_LOGO_TEAMS = {"nfl": ("lar", "nyj")}
# leagues whose primary logos are busy wordmarks / scripts: use simpler logos by default
SCOREBOARD_LOGO_LEAGUES = {"mlb"}
LOGO_VERSIONS = {
    "auto": "Automatic (MLB: MLB's official cap logos; everything else: ESPN's full logos)",
    "primary": "ESPN full team logos",
    "scoreboard": "ESPN small-size logos",
    "mlb_cap": "MLB official cap logos (MLB only)",
    "mlb_primary": "MLB official team logos (MLB only)",
}
# ESPN abbreviation -> MLB's own team id (for logos from mlbstatic.com)
MLB_IDS = {"ARI": 109, "AZ": 109, "ATL": 144, "BAL": 110, "BOS": 111, "CHC": 112, "CHW": 145, "CWS": 145, "CIN": 113,
           "CLE": 114, "COL": 115, "DET": 116, "HOU": 117, "KC": 118, "LAA": 108, "LAD": 119, "MIA": 146,
           "MIL": 158, "MIN": 142, "NYM": 121, "NYY": 147, "ATH": 133, "OAK": 133, "PHI": 143, "PIT": 134,
           "SD": 135, "SF": 137, "SEA": 136, "STL": 138, "TB": 139, "TEX": 140, "TOR": 141, "WSH": 120, "WAS": 120}


def _fill_team(team, teams):
    """Fill colors / logo / name from ESPN's team list where the event left them out."""
    info = teams.get((team.get("abbr") or "").upper()) or {}
    if not info:
        return
    if not team.get("_has_color") and info.get("color"):
        team["color"] = hex_to_rgb(info["color"], team.get("color") or GRAY)
        team["alt"] = hex_to_rgb(info.get("alt"), WHITE)
    if not team.get("logo_url") and info.get("logo"):
        team["logo_url"] = info["logo"]
    if not team.get("name") and info.get("name"):
        team["name"] = info["name"]


def _abbr_from_logo_url(url):
    m = re.search(r"/teamlogos/[a-z]+/500(?:-dark)?/(?:scoreboard/)?([a-z0-9]+)\.png", url or "")
    return m.group(1).upper() if m else ""


def _fetch_svg(url, size):
    """An SVG logo as a PIL image (needs the optional 'cairosvg' package + libcairo); None otherwise."""
    import hashlib
    import io
    import os
    from PIL import Image
    from ..gfx import CACHE_DIR, trim_alpha, fit_image
    key = hashlib.sha1(f"svg|{url}|{size}".encode()).hexdigest()
    path = os.path.join(CACHE_DIR, key + ".png")
    if os.path.exists(path):
        try:
            return Image.open(path).convert("RGBA")
        except Exception:
            pass
    try:
        import cairosvg
    except Exception:
        return None
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        png = cairosvg.svg2png(bytestring=r.content, output_width=size * 2)
        img = fit_image(trim_alpha(Image.open(io.BytesIO(png)).convert("RGBA")), size, size)
        os.makedirs(CACHE_DIR, exist_ok=True)
        img.save(path)
        return img
    except Exception as e:
        log.info("svg logo %s unavailable: %s", url, e)
        return None


def _prefers_color_logo(url):
    m = re.search(r"/teamlogos/([a-z]+)/500/([a-z0-9]+)\.png", url or "")
    return bool(m) and m.group(2) in COLOR_LOGO_TEAMS.get(m.group(1), ())


def colorfulness(img):
    """Share of a logo's visible pixels that are clearly colored (not white, gray or black)."""
    small = img.convert("RGBA").resize((48, 48))
    seen = colored = 0
    for r, g, b, a in small.getdata():
        if a < 128:
            continue
        seen += 1
        hi, lo = max(r, g, b), min(r, g, b)
        if hi > 40 and (hi - lo) / hi > 0.3:
            colored += 1
    return colored / seen if seen else 0.0


class LeagueBoard(Board):
    """One league. Concrete classes are generated per league at the bottom of this file."""
    league = "nfl"
    refresh = 60

    COMMON = {
        "enabled": False, "duration": 7, "refresh": 60, "live_refresh": 15,
        "favorites": [], "favorites_only": False, "live_only": False, "takeover": False,
        "show_pregame": True, "show_final": True, "final_hours": 24, "upcoming_hours": 48, "max_games": 20,
        "show_records": True, "show_rankings": True, "show_odds": False, "show_network": True,
        "hide_favorite_score": False, "detailed_live": True, "show_last_play": True, "score_flash": True,
        "use_dark_logos": True, "live_layout": "classic", "show_win_probability": True, "goal_animation": True,
        "celebrations": True, "celebrate_field_goals": False, "celebrate_runs": False,
        "next_game": "no_games", "next_game_style": "classic", "offseason_countdown": True, "opener_days": 14, "show_standings": False, "show_playoffs": False, "recap": True,
        "show_abbreviations": True, "show_final_records": True, "logo_enhance": True, "logo_edges": "auto",
        "logo_version": "auto", "logo_detail": "full", "logo_overrides": [],
        "text_score": "#ffffff", "text_status": "#ffc800", "text_info": "#787878", "text_loser": "#787878",
        "abbr_color_mode": "team", "abbr_color": "#ffffff", "final_style": "plain", "upcoming_style": "plain", "upcoming_layout": "classic", "live_style": "plain",
        "network_logos": True,
    }

    @classmethod
    def build_fields(cls):
        sport = LEAGUES[cls.league][0]
        f = [
            {"key": "favorites", "label": "Favorite teams (ESPN abbreviations, comma-separated)", "type": "list",
             "section": "Teams", "help": "e.g. BUF, KC. Favorites are shown first."},
            {"key": "favorites_only", "label": "Only show favorite teams", "type": "bool", "section": "Teams"},
            {"key": "takeover", "label": "Take over the display while a favorite is playing", "type": "bool",
             "section": "Teams", "help": "Only that game is shown until it ends (like 'favorite sticky')."},
            {"key": "hide_favorite_score", "label": "Spoiler mode: hide scores of favorite teams' games",
             "type": "bool", "section": "Teams"},
            {"key": "live_only", "label": "Only show live games", "type": "bool", "section": "Which games"},
            {"key": "show_final", "label": "Show final scores", "type": "bool", "section": "Which games",
             "show_if": {"live_only": False}},
            {"key": "final_hours", "label": "...for this many hours after the game ends", "type": "number",
             "section": "Which games", "show_if": {"live_only": False, "show_final": True},
             "help": "Then the game drops off and (with the next-game countdown on) your team's next game is shown."},
            {"key": "show_pregame", "label": "Show upcoming games as game cards", "type": "bool", "section": "Which games",
             "show_if": {"live_only": False}},
            {"key": "upcoming_hours", "label": "...once kickoff is within (hours)", "type": "number", "section": "Which games",
             "show_if": {"live_only": False, "show_pregame": True},
             "help": "Further out than this, your team's next game shows as the countdown instead."},
            {"key": "next_game", "label": "Next-game countdown for favorite teams", "type": "select", "section": "Which games",
             "options": ["no_games", "always", "off"],
             "option_labels": {"no_games": "When your team has no game on the board (recommended)",
                               "always": "Always (in addition to games)", "off": "Off"},
             "help": "Logos, a countdown (live h:mm:ss in the last day), kickoff time, opponent and TV network. "
                     "Order of things for your team: game card while live, final score for the hours set above, "
                     "then this countdown until the game is close enough to show as a game card again."},
            {"key": "next_game_style", "label": "Countdown design", "type": "select", "section": "Which games",
             "options": list(COUNTDOWN_STYLES), "option_labels": COUNTDOWN_STYLES},
            {"key": "offseason_countdown", "label": "Count down through the offseason", "type": "bool",
             "section": "Which games",
             "help": "On: between seasons the countdown shows your team's next game, including spring training / "
                     "preseason. Off: preseason games are skipped and the countdown to the season opener only "
                     "appears in the days set below."},
            {"key": "opener_days", "label": "...otherwise start counting down this many days before the opener",
             "type": "number", "section": "Which games", "show_if": {"offseason_countdown": False}},
            {"key": "max_games", "label": "Max games per rotation", "type": "number", "section": "Which games"},
            {"key": "show_records", "label": "Show team records before the game", "type": "bool", "section": "Upcoming games"},
            {"key": "show_network", "label": "Show TV network", "type": "bool", "section": "Upcoming games"},
            {"key": "network_logos", "label": "Show the TV network's logo instead of its name", "type": "bool",
             "section": "Upcoming games", "show_if": {"show_network": True},
             "help": "Built-in logos for ESPN, ABC, CBS, NBC, FOX, FS1, NFL Network, Prime Video, Peacock, Apple TV, "
                     "Netflix, TNT, TBS, MLB/NHL/NBA networks and more; other networks still show as text."},
            {"key": "show_odds", "label": "Show betting line / over-under", "type": "bool", "section": "Upcoming games"},
            {"key": "upcoming_layout", "label": "Layout", "type": "select", "section": "Upcoming games",
             "options": list(PREGAME_LAYOUTS), "option_labels": PREGAME_LAYOUTS,
             "help": "Use Preview > Upcoming to try them."},
            {"key": "upcoming_style", "label": "Upcoming style", "type": "select", "section": "Upcoming games",
             "show_if": {"upcoming_layout": "classic"},
             "options": list(UPCOMING_STYLES), "option_labels": UPCOMING_STYLES,
             "help": "Team color accents on upcoming games (standard view). Use Preview > Upcoming to try them."},
        ]
        if cls.league in COLLEGE:
            f.append({"key": "show_rankings", "label": "Show AP Top 25 rankings", "type": "bool", "section": "Upcoming games"})
        live_help = {
            "football": "Possession marker, timeouts, down & distance with field position.",
            "baseball": "Base runners, outs, ball/strike count, batter and pitcher, hits/errors.",
            "basketball": "Top scorer for each team.",
            "hockey": "Shots on goal.",
            "soccer": "Red cards and the latest goal scorer.",
        }[sport]
        layouts = live_layouts(sport)
        if layouts:
            f.append({"key": "live_layout", "label": "Live game layout", "type": "select", "section": "Live games",
                      "options": list(layouts), "option_labels": layouts})
        if sport in ("hockey", "soccer"):
            f.append({"key": "goal_animation", "label": "Goal celebration (flashing GOAL! when a team scores)",
                      "type": "bool", "section": "Live games"})
        if sport == "football":
            f.append({"key": "show_win_probability", "label": "Show win probability bar (when ESPN provides it)",
                      "type": "bool", "section": "Live games", "show_if": {"live_layout": "drive"}})
        f += [
            {"key": "detailed_live", "label": "Detailed live info", "type": "bool", "section": "Live games",
             "help": live_help, "show_if": {"live_layout": "classic"} if layouts else None},
            {"key": "show_last_play", "label": "Scroll the last play", "type": "bool", "section": "Live games"},
            {"key": "score_flash", "label": "Flash the score when it changes", "type": "bool", "section": "Live games"},
            {"key": "live_style", "label": "Live style", "type": "select", "section": "Live games",
             "options": list(LIVE_STYLES), "option_labels": LIVE_STYLES,
             "show_if": {"live_layout": "classic"} if live_layouts(sport) else None,
             "help": "Team color accents on live games in the Classic layout. Use Preview > Live game to try them."},
        ]
        if sport in CELEBRATED:
            what = {"football": "touchdown", "hockey": "goal", "soccer": "goal", "baseball": "home run"}[sport]
            f.append({"key": "celebrations", "label": f"Celebrate when a favorite team scores a {what}",
                      "type": "bool", "section": "Celebrations",
                      "help": "Plays an animation the next time this board comes up, then the games. "
                              "Needs favorite teams; skipped in spoiler mode."})
            if sport == "football":
                f.append({"key": "celebrate_field_goals", "label": "Also celebrate field goals", "type": "bool",
                          "section": "Celebrations", "show_if": {"celebrations": True}})
            if sport == "baseball":
                f.append({"key": "celebrate_runs", "label": "Celebrate every run, not just home runs", "type": "bool",
                          "section": "Celebrations", "show_if": {"celebrations": True}})
            f.append({"key": "recap", "label": "Show a recap of the scoring play after the celebration", "type": "bool",
                      "section": "Celebrations", "show_if": {"celebrations": True},
                      "help": "The play (e.g. 'J. Love 24 yd pass to C. Watson'), the new score and, for football, the drive."})
            f.append({"key": "simulate", "label": "Simulate", "type": "simulate", "section": "Celebrations",
                      "options": SIM_KINDS[sport]})
        f += [
            {"key": "show_standings", "label": "Show standings (favorite team's division / group)", "type": "bool",
             "section": "Off days", "help": "Paged table, 4 teams per page, your team highlighted."},
        ]
        if cls.league in PLAYOFF_LEAGUES:
            f.append({"key": "show_playoffs", "label": "Show the playoff picture", "type": "bool", "section": "Off days",
                      "help": "Your conference's playoff seeds with clinch status and who's in the hunt"
                              + (", plus the playoff bracket once the playoffs start." if cls.league == "nfl" else ".")})
        f += [
            {"key": "preview", "label": "Preview", "type": "actions", "section": "Preview", "action": "preview",
             "options": {"live": "Live game", "pre": "Upcoming", "post": "Final", "all": "All three",
                         "countdown": "Next-game countdown", "logos": "Logos"},
             "help": "Shows sample games with your current settings and layout for 3 minutes, even in the "
                     "off-season. Uses your favorite teams when set."},
            {"key": "duration", "label": "Seconds per game", "type": "number", "section": "Timing"},
            {"key": "refresh", "label": "Refresh interval (s)", "type": "number", "section": "Timing"},
            {"key": "live_refresh", "label": "Refresh interval while games are live (s)", "type": "number", "section": "Timing"},
            {"key": "show_abbreviations", "label": "Show team abbreviations under the logos", "type": "bool",
             "section": "Logos", "help": "Off: logos grow to fill the space (standard view)."},
            {"key": "show_final_records", "label": "Show team records on final scores", "type": "bool", "section": "Logos"},
            {"key": "logo_enhance", "label": "Enhance logo colors for LEDs", "type": "bool", "section": "Logos",
             "help": "Crisp edges, the logo's true colors without muddy blends, and dark colors (e.g. Packers green, "
                     "navy) brightened so they show up on the panel."},
            {"key": "logo_edges", "label": "Logo edges", "type": "select", "section": "Logos",
             "options": ["auto", "smooth", "crisp"],
             "option_labels": {"auto": "Automatic (crisp when big, smooth when small)",
                               "smooth": "Smooth (anti-aliased, softer)", "crisp": "Crisp (hard pixel edges)"},
             "help": "Smooth keeps soft edges like the clock's 'Smooth edges'; crisp snaps to the logo's exact colors."},
            {"key": "logo_version", "label": "Logo version", "type": "select", "section": "Logos",
             "options": list(LOGO_VERSIONS), "option_labels": LOGO_VERSIONS,
             "help": "Use Preview > Logos to see every version of your favorite team's logo on the panel. "
                     "If a version isn't available for a team, its ESPN full logo is used."},
            {"key": "logo_overrides", "label": "Custom logos (one per team: ABBR=image URL)", "type": "list",
             "section": "Logos",
             "help": "e.g. STL=https://example.com/cardinals.png. A PNG with a transparent background works best "
                     "(SVG needs the optional cairosvg package)."},
            {"key": "logo_detail", "label": "Logo detail", "type": "select", "section": "Logos",
             "options": ["full", "clean"],
             "option_labels": {"full": "Full detail", "clean": "Clean (4 main colors, no stray pixels)"},
             "help": "Clean makes busy logos (scripts, thin outlines) read better on the LEDs."},
            {"key": "use_dark_logos", "label": "Prefer logos designed for dark backgrounds", "type": "bool", "section": "Logos",
             "help": "When a team's dark-background logo is plain white (e.g. Rams, Jets), the colored logo is used instead."},
            {"key": "final_style", "label": "Final style", "type": "select", "section": "Final scores",
             "options": list(FINAL_STYLES), "option_labels": FINAL_STYLES,
             "help": "Team color accents on finished games (standard view). Use Preview > Final to try them."},
            {"key": "text_score", "label": "Scores and start times", "type": "color", "section": "Text colors",
             "help": "Colors for the standard game view. The special live layouts keep their own color schemes."},
            {"key": "text_status", "label": "Live clock / game status", "type": "color", "section": "Text colors"},
            {"key": "text_info", "label": "Records, TV network and FINAL", "type": "color", "section": "Text colors"},
            {"key": "text_loser", "label": "Losing team's score (finals)", "type": "color", "section": "Text colors",
             "help": "Set it the same as the score color to not dim the loser."},
            {"key": "abbr_color_mode", "label": "Team abbreviations", "type": "select", "section": "Text colors",
             "options": ["team", "custom"], "option_labels": {"team": "Team colors", "custom": "One color for all"}},
            {"key": "abbr_color", "label": "Abbreviation color", "type": "color", "section": "Text colors",
             "show_if": {"abbr_color_mode": "custom"}},
        ]
        return organize(f, LEAGUE_LAYOUT, LEAGUE_LABELS)

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.games: list[dict] = []
        self.logos: dict[str, object] = {}       # url -> logo fitted to the display height
        self.logo_src: dict[str, object] = {}    # url -> trimmed hi-res copy for other sizes
        self.logo_fallback: dict = {}             # url -> retry time, for logos showing a fallback version
        self.logo_sources: dict = {}              # url -> "preferred" / "fallback" (for the Status tab)
        self.flash: dict[str, float] = {}     # "gameid|side" -> until (monotonic)
        self._scores: dict[str, str] = {}
        self.pending = []                     # queued Celebration slides
        self.next_games: dict = {}            # favorite abbr -> next scheduled game
        self.standings_data: list = []        # [(group name, rows)]
        self.playoff_data = None              # playoff picture: {group, rows, teams}
        self._final_seen: dict = {}           # game id -> when we saw it become final
        self._last_state: dict = {}
        self.bracket_data = None              # NFL bracket: [(round, games)]
        self._offday_at = 0.0
        self.preview_state = ([], 0.0)        # (sample games, show until epoch)
        self._cel_lock = threading.Lock()
        self._last_td: dict[str, float] = {}
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "ledboard/1.0"

    @property
    def sport(self):
        return LEAGUES[self.league][0]

    def interval(self):
        if any(g["state"] == "in" for g in self.games):
            return float(self.cfg.get("live_refresh", 15) or 15)
        return float(self.cfg.get("refresh", 60) or 60)

    def favorites(self):
        return {f.strip().upper() for f in self.cfg.get("favorites", []) if f and f.strip()}

    def is_favorite(self, g):
        fav = self.favorites()
        return g["home"]["abbr"].upper() in fav or g["away"]["abbr"].upper() in fav

    # -- data ---------------------------------------------------------------
    def _scoreboard(self, params):
        sport, path, _, _ = LEAGUES[self.league]
        r = self.session.get(ESPN.format(sport=sport, league=path), params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("events", []) or []

    def _fetch_events(self):
        """Scoreboard events: yesterday through the upcoming-games window.

        Without dates ESPN only returns *today's* games (by US Eastern date), so tomorrow's games
        never showed as upcoming cards. Not every league accepts a date range, so this tries, in
        order: one request for the whole range, then one request per day (future days cached for
        10 minutes), then plain today-only; it remembers what worked.
        """
        sport, _path, _, extra = LEAGUES[self.league]
        base = {"limit": 200}
        if self.favorites():
            base.update(extra)  # e.g. all FBS games so unranked favorites show up
        if sport == "football":                     # ESPN's football scoreboard already covers the week
            return self._scoreboard(base)
        try:
            ahead = float(self.cfg.get("upcoming_hours", 48) or 48)
        except (TypeError, ValueError):
            ahead = 48
        today = datetime.now().astimezone().date()
        days_ahead = max(1, min(7, math.ceil(ahead / 24))) if self.cfg.get("show_pregame", True) else 0
        days = [today + timedelta(days=d) for d in range(-1, days_ahead + 1)]
        mode = getattr(self, "_sb_mode", "range")

        def rejected(e):
            return isinstance(e, requests.HTTPError) and e.response is not None and 400 <= e.response.status_code < 500

        if mode == "range":
            try:
                return self._scoreboard(dict(base, dates=f"{days[0]:%Y%m%d}-{days[-1]:%Y%m%d}"))
            except requests.HTTPError as e:
                if not rejected(e):
                    raise
                log.info("[%s] ESPN rejected a date range (%s); asking one day at a time", self.name, e)
                self._sb_mode = mode = "daily"
        if mode == "daily":
            cache = getattr(self, "_day_cache", {})
            events, seen = [], set()
            try:
                for d in days:
                    key = f"{d:%Y%m%d}"
                    hit = cache.get(key)
                    if d > today and hit and time.time() - hit[0] < 600:
                        evs = hit[1]
                    else:
                        evs = self._scoreboard(dict(base, dates=key))
                        cache[key] = (time.time(), evs)
                    for ev in evs:
                        if ev.get("id") not in seen:
                            seen.add(ev.get("id"))
                            events.append(ev)
                self._day_cache = {k: v for k, v in cache.items() if k >= f"{days[0]:%Y%m%d}"}
                return events
            except requests.HTTPError as e:
                if not rejected(e):
                    raise
                log.info("[%s] ESPN rejected dates (%s); showing today's games only", self.name, e)
                self._sb_mode = "plain"
        return self._scoreboard(base)

    SCAN_DAYS = 21

    def _scan_ahead(self):
        """League scoreboard events for the next three weeks (for the next-game lookup). Cached for the
        favorites in this round of lookups; uses whatever date mode ESPN accepted for this league."""
        cached = getattr(self, "_scan_cache", None)
        if cached and time.time() - cached[0] < 600:
            return cached[1]
        today = datetime.now().astimezone().date()
        mode = getattr(self, "_sb_mode", "range")
        events = []
        if mode == "range":
            try:
                events = self._scoreboard({"limit": 300, "dates": f"{today:%Y%m%d}-{today + timedelta(days=self.SCAN_DAYS):%Y%m%d}"})
            except requests.HTTPError:
                mode = "daily"
        if mode == "daily":
            for d in range(self.SCAN_DAYS + 1):
                try:
                    events += self._scoreboard({"limit": 200, "dates": f"{today + timedelta(days=d):%Y%m%d}"})
                except requests.HTTPError:
                    break
        self._scan_cache = (time.time(), events)
        return events

    def update(self):
        events = self._fetch_events()
        games = [g for g in (parse_event(ev, self.league) for ev in events) if g]
        now = time.monotonic()
        for g in games:
            for side in ("home", "away"):
                self._load_logo(g[side])
                key = f"{g['id']}|{side}"
                score = str(g[side]["score"])
                old = self._scores.get(key)
                if old is not None and g["state"] == "in" and _num(score) > _num(old):
                    self.flash[key] = now + 8
                    self._maybe_celebrate(g, side, int(_num(score) - _num(old)))
                self._scores[key] = score
            if g["state"] == "post" and g["id"] not in self._final_seen and self._last_state.get(g["id"]) in ("in", "pre"):
                self._final_seen[g["id"]] = datetime.now(timezone.utc)      # we saw it end
            self._last_state[g["id"]] = g["state"]
        self.games = games
        # a favorite's 'next game' just started or finished: look up the one after it now, not in 3 hours
        now_utc = datetime.now(timezone.utc)
        finals = {g["id"] for g in games if g["state"] == "post"}
        stale = any(ng["id"] in finals or (ng["start"] and now_utc - ng["start"] > timedelta(hours=6))
                    for ng in self.next_games.values())
        if stale and time.time() - getattr(self, "_next_forced", 0) > 600:     # at most every 10 minutes
            self._next_forced = time.time()
            self._offday_at = 0
        self._update_offdays()

    OFFDAY_REFRESH = 3 * 3600

    def _update_offdays(self):
        """Schedules (next game) and standings, refreshed every few hours; failures never break scores."""
        c = self.cfg
        want_next = c.get("next_game", "no_games") != "off" and self.favorites()
        want_table = c.get("show_standings")
        want_po = c.get("show_playoffs") and self.league in PLAYOFF_LEAGUES
        key = (bool(want_next), bool(want_table), bool(want_po), tuple(sorted(self.favorites())),
               bool(c.get("offseason_countdown", True)))
        if not (want_next or want_table or want_po):
            return
        if key == getattr(self, "_offday_key", None) and time.time() - self._offday_at < self.OFFDAY_REFRESH:
            return
        self._offday_key = key
        from . import offdays
        from .sample_games import fetch_teams
        sport, path, _, _ = LEAGUES[self.league]
        self._offday_at = time.time()
        if want_next:
            teams = fetch_teams(sport, path)
            found, problems = {}, {}
            for fav in list(self.favorites())[:3]:
                if not teams:
                    problems[fav] = "ESPN team list unavailable"
                    continue
                tid = (teams.get(fav) or {}).get("id")
                if not tid:
                    problems[fav] = f"'{fav}' isn't an ESPN {LEAGUES[self.league][2]} abbreviation"
                    log.warning("[%s] favorite %s not found in ESPN's team list", self.name, fav)
                    continue
                try:
                    g = offdays.next_game(sport, path, self.league, tid, parse_event,
                                          scoreboard_scan=self._scan_ahead if sport != "football" else None,
                                          team_abbr=fav, preseason=bool(c.get("offseason_countdown", True)))
                except Exception as e:
                    log.info("[%s] schedule for %s unavailable: %s", self.name, fav, e)
                    problems[fav] = f"schedule unavailable ({e.__class__.__name__}: {str(e)[:80]})"
                    continue
                if g is None:
                    problems[fav] = "no upcoming game on ESPN's schedule"
                if g:
                    for side in ("home", "away"):
                        _fill_team(g[side], teams)       # team schedules carry no colors (or sometimes logos)
                        self._load_logo(g[side])
                    found[fav] = g
            self.next_games = found
            self.next_game_problems = problems
            if any(p != "no upcoming game on ESPN's schedule" for p in problems.values()):
                self._offday_at = time.time() - self.OFFDAY_REFRESH + 600      # try again in 10 minutes
        if want_table:
            try:
                self.standings_data = offdays.standings(sport, path, self.favorites())
                for _, rows in self.standings_data:
                    for row in rows:
                        self._load_logo({"logo_url": row["logo_url"]})
            except Exception as e:
                log.info("[%s] standings unavailable: %s", self.name, e)
        if want_po:
            from . import playoffs
            try:
                self.playoff_data = playoffs.picture(sport, path, self.league, self.favorites())
                for row in (self.playoff_data or {}).get("rows", []):
                    self._load_logo({"logo_url": row["logo_url"]})
            except Exception as e:
                log.info("[%s] playoff picture unavailable: %s", self.name, e)
            try:
                teams = (self.playoff_data or {}).get("teams")
                self.bracket_data = playoffs.bracket(sport, path, self.league, teams, parse_event)
                for _label, games in self.bracket_data or []:
                    for g in games:
                        for side in ("home", "away"):
                            self._load_logo(g[side])
            except Exception as e:
                log.info("[%s] playoff bracket unavailable: %s", self.name, e)

    # -- celebrations ----------------------------------------------------------
    def _maybe_celebrate(self, g, side, diff):
        from .celebrations import Celebration, classify
        c = self.cfg
        if not c.get("celebrations", True) or self.sport not in CELEBRATED or c.get("hide_favorite_score"):
            return
        if g[side]["abbr"].upper() not in self.favorites():
            return
        key = f"{g['id']}|{side}"
        recent_td = time.time() - self._last_td.get(key, 0) < 180
        kind = classify(self.sport, diff, g.get("last_play", ""), c, recent_td)
        if not kind:
            return
        if kind == "touchdown":
            self._last_td[key] = time.time()
        snap = _snapshot(g)
        queued = [Celebration(kind, side, snap, self.sport)]
        if c.get("recap", True):
            from .recap import Recap, fetch_play
            sport, path, _, _ = LEAGUES[self.league]
            text, drive = fetch_play(sport, path, g["id"], g[side].get("id"),
                                     fallback=g.get("last_play") or g.get("last_goal") or "")
            queued.append(Recap(kind, side, snap, text, drive))
        with self._cel_lock:
            self.pending.extend(queued)
            self.pending = self.pending[-6:]
        log.info("[%s] queued %s celebration for %s", self.name, kind, g[side]["abbr"])

    def intro_slides(self):
        """Celebrations to play before this board's games (consumed when taken)."""
        with self._cel_lock:
            fresh = [x for x in self.pending if time.time() - x.created < 30 * 60]
            self.pending = []
        return fresh

    def simulate(self, kind):
        """Queue a celebration for testing: uses a real game with a favorite if there is one."""
        from .celebrations import Celebration
        favs = self.favorites()
        pick, side = None, "home"
        for g in self._preview_games() + self.games:
            for sd in ("home", "away"):
                if g[sd]["abbr"].upper() in favs:
                    pick, side = g, sd
                    break
            if pick:
                break
        if pick is None and self.games:
            pick = self.games[0]
        if pick is None:
            pick = _sample_game(self.league, next(iter(favs), None))
        g = _snapshot(pick)
        pts = {"touchdown": 7, "field_goal": 3, "goal": 1, "home_run": 1, "grand_slam": 4, "run": 1}[kind]
        g[side]["score"] = str(int(_num(g[side]["score"])) + pts)
        queued = [Celebration(kind, side, g, self.sport)]
        if self.cfg.get("recap", True):
            from .recap import SAMPLE_PLAYS, Recap
            text, drive = SAMPLE_PLAYS.get(kind, ("", ""))
            if self.sport != "football":
                drive = ""
            queued.append(Recap(kind, side, g, text, drive))
        with self._cel_lock:
            self.pending.extend(queued)

    def slide_duration(self, slide):
        from .celebrations import DURATION, Celebration
        from .recap import Recap, duration
        if isinstance(slide, Celebration):
            return DURATION
        if isinstance(slide, Recap):
            return duration(slide, self.app.width)
        return self.duration

    def _logo_version(self):
        v = self.cfg.get("logo_version", "auto")
        if v == "auto":
            return "mlb_cap" if self.league in SCOREBOARD_LOGO_LEAGUES else "primary"
        return v

    def _custom_logos(self):
        """{ABBR: url} from the 'Custom logos' setting (lines like 'STL=https://...png')."""
        out = {}
        for item in self.cfg.get("logo_overrides") or []:
            if "=" in str(item):
                k, v = str(item).split("=", 1)
                if k.strip() and v.strip().startswith("http"):
                    out[k.strip().upper()] = v.strip()
        return out

    def logo_candidates(self, url, abbr, version, dark=None):
        """[(label, url)] to try, best first, for one logo version."""
        dark = self.cfg.get("use_dark_logos", True) if dark is None else dark
        out = []
        if version == "scoreboard" and "/500/" in url:
            if dark:
                out.append(("ESPN small-size (dark)", url.replace("/500/", "/500-dark/scoreboard/")))
            out.append(("ESPN small-size", url.replace("/500/", "/500/scoreboard/")))
        elif version in ("mlb_cap", "mlb_primary") and self.league == "mlb" and MLB_IDS.get(abbr):
            tid = MLB_IDS[abbr]
            if version == "mlb_cap":
                out.append(("MLB cap logo", f"https://midfield.mlbstatic.com/v1/team/{tid}/spots/128"))
                out.append(("MLB cap logo (svg)", f"https://www.mlbstatic.com/team-logos/team-cap-on-dark/{tid}.svg"))
            else:
                out.append(("MLB team logo (svg)", f"https://www.mlbstatic.com/team-logos/team-primary-on-dark/{tid}.svg"))
                out.append(("MLB team logo (svg)", f"https://www.mlbstatic.com/team-logos/{tid}.svg"))
        if dark and "/500/" in url and not _prefers_color_logo(url):
            out.append(("ESPN full (dark)", url.replace("/500/", "/500-dark/")))
        out.append(("ESPN full", url))
        return out

    def _fetch_logo(self, u, size):
        return _fetch_svg(u, size) if u.split("?")[0].endswith(".svg") else fetch_image(u, size, size, trim=True)

    def _load_logo(self, team):
        url = team.get("logo_url")
        sig = (self._logo_version(), bool(self.cfg.get("use_dark_logos", True)), tuple(sorted(self._custom_logos().items())))
        if sig != getattr(self, "_logo_sig", sig):           # logo settings changed: load them all again
            self.logos, self.logo_src, self.logo_fallback = {}, {}, {}
        self._logo_sig = sig
        if not url:
            return
        fallback = getattr(self, "logo_fallback", None)
        if fallback is None:
            fallback = self.logo_fallback = {}            # url -> when to try the preferred source again
        if url in self.logos and not (url in fallback and time.time() >= fallback[url]):
            return
        size = self.app.height * 6          # ~192 px: room to find the logo's true colors
        abbr = (team.get("abbr") or _abbr_from_logo_url(url) or "").upper()
        custom = self._custom_logos().get(abbr)
        cands = self.logo_candidates(url, abbr, self._logo_version())
        preferred = custom or cands[0][1]
        retrying = url in self.logos
        img, used = (self._fetch_logo(custom, size), custom) if custom else (None, None)
        if img is None:
            for label, u in cands:
                if retrying and u != preferred:
                    break                     # retry only the preferred source; keep what we have otherwise
                img, used = self._fetch_logo(u, size), u
                if img is None:
                    continue
                if label == "ESPN full (dark)" and colorfulness(img) < 0.12:
                    # some "dark background" logos are plain white (e.g. Rams, Jets): use the team-color
                    # logo instead when it actually has color
                    color_img = fetch_image(url, size, size, trim=True)
                    if color_img is not None and colorfulness(color_img) > colorfulness(img) + 0.15:
                        img = color_img
                break
        if img is not None and used == preferred:
            fallback.pop(url, None)
            self.logo_sources[url] = "preferred"
        else:
            # the logo we wanted couldn't be downloaded (e.g. network not ready at boot): use the fallback
            # for now and try the preferred one again in 5 minutes
            tries = self.__dict__.setdefault("_logo_tries", {})
            tries[url] = tries.get(url, 0) + 1
            fallback[url] = time.time() + 300 * min(12, 2 ** (tries[url] - 1))   # 5 min, 10, 20 ... up to 1 h
            if retrying:
                return
            self.logo_sources[url] = "fallback"
        self.set_logo(url, img)

    def set_logo(self, url, img):
        """Store a logo (already trimmed): hi-res source + a copy fitted to the display height."""
        self.logo_src[url] = img
        H = self.app.height
        self.logos[url] = fit_image(img, H, H) if img is not None else None

    def visible_games(self):
        now = datetime.now(timezone.utc)
        c = self.cfg
        out = []
        for g in self.games:
            if c.get("favorites_only") and self.favorites() and not self.is_favorite(g):
                continue
            if c.get("live_only") and g["state"] != "in":
                continue
            start = g["start"]
            if g["state"] == "pre":
                if not c.get("show_pregame", True):
                    continue
                if start and start - now > timedelta(hours=float(c.get("upcoming_hours", 48) or 48)):
                    continue
            if g["state"] == "post":
                if not c.get("show_final", True):
                    continue
                ended = self._ended_at(g)
                if ended and now - ended > timedelta(hours=float(c.get("final_hours", 24) or 24)):
                    continue
            out.append(g)
        order = {"in": 0, "pre": 1, "post": 2}
        out.sort(key=lambda g: (not self.is_favorite(g), order.get(g["state"], 3), g["start"] or now))
        out = out[: int(c.get("max_games", 20) or 20)]
        return self._preview_games() + out

    def _opener_days(self):
        try:
            return max(0.0, float(self.cfg.get("opener_days", 14) or 0))
        except (TypeError, ValueError):
            return 14.0

    def _offseason_hidden(self, g, now_utc=None):
        """Offseason countdown turned off and the opener is further away than 'opener_days'."""
        if self.cfg.get("offseason_countdown", True) or not g.get("offseason") or not g.get("start"):
            return False
        now_utc = now_utc or datetime.now(timezone.utc)
        return g["start"] - now_utc > timedelta(days=self._opener_days())

    # typical game length, to estimate when a game that was already over when we started really ended
    GAME_HOURS = {"football": 3.25, "baseball": 3.0, "basketball": 2.25, "hockey": 2.5, "soccer": 2.0}

    def _ended_at(self, g):
        """When a final game ended: the moment we saw it go final, else start + a typical game length."""
        seen = self._final_seen.get(g["id"])
        if seen:
            return seen
        if g["start"]:
            return g["start"] + timedelta(hours=self.GAME_HOURS.get(self.sport, 3.0))
        return None

    def slides(self):
        visible = self.visible_games()
        games = [g["id"] for g in visible]
        out = [g["key"] for g in self._gallery()] + \
            [sid for sid, _fav, _g in self._preview_countdowns()] + list(games)
        mode = self.cfg.get("next_game", "no_games")
        now_utc = datetime.now(timezone.utc)
        for fav, g in self.next_games.items():
            if g["id"] in games or not (g["start"] is None or g["start"] > now_utc):
                continue
            if self._offseason_hidden(g, now_utc):
                continue
            # per team: the countdown shows when this team has nothing on the board
            fav_on_board = any(v[s]["abbr"].upper() == fav for v in visible for s in ("home", "away")
                               if not v.get("preview"))
            if mode == "always" or (mode == "no_games" and not fav_on_board):
                out.append(f"next:{fav}")
        if self.cfg.get("show_standings"):
            from .offdays import ROWS_PER_PAGE
            for gi, (_, rows) in enumerate(self.standings_data):
                for p in range(max(1, -(-len(rows) // ROWS_PER_PAGE))):
                    out.append(f"standings:{gi}:{p}")
        if self.cfg.get("show_playoffs") and self.league in PLAYOFF_LEAGUES:
            if self.bracket_data:
                out.append("bracket")
            if self.playoff_data and self.playoff_data["rows"]:
                from .playoffs import ROWS
                for p in range(-(-len(self.playoff_data["rows"]) // ROWS)):
                    out.append(f"playoffs:{p}")
        return out

    def takeover_slides(self):
        if not self.cfg.get("takeover"):
            return []
        return [g["id"] for g in self.visible_games()
                if g["state"] == "in" and self.is_favorite(g) and not g.get("preview")]

    def _game(self, slide):
        return next((g for g in self._preview_games() + self.games if g["id"] == slide), None)

    # -- previews (sample games) --------------------------------------------------
    PREVIEW_SECONDS = 180

    def _preview_games(self):
        games, until = self.preview_state
        if games and time.time() < until:
            return [g for g in games if not g.get("countdown_preview")]
        return []

    def _preview_countdowns(self):
        """Sample next games for the countdown preview: [(slide id, fav abbr, game)]."""
        games, until = self.preview_state
        if not games or time.time() >= until:
            return []
        return [(f"pnext:{i}", g["countdown_fav"], g) for i, g in enumerate(games) if g.get("countdown_preview")]

    def preview(self, kind="all"):
        """Show sample games (live / pre / post / all) for a few minutes, e.g. in the off-season."""
        from .sample_games import fetch_teams, make_event, pick_teams
        sport, path, _, _ = LEAGUES[self.league]
        teams = fetch_teams(sport, path)
        away, home = pick_teams(self.league, [f.upper() for f in self.cfg.get("favorites", [])], teams)
        if kind == "countdown":
            return self._preview_countdown(sport, path, teams, away, home)
        if kind == "logos":
            for u in list(getattr(self, "logo_fallback", {})):
                self.logo_fallback[u] = 0          # retry fallbacks on the next update
            return self._preview_logos(teams)
        states = {"live": ["in"], "pre": ["pre"], "post": ["post"], "all": ["in", "pre", "post"]}[kind]
        games = []
        for st in states:
            g = parse_event(make_event(self.league, sport, st, away, home, teams), self.league)
            g["preview"] = True
            for side in ("home", "away"):
                self._load_logo(g[side])
            games.append(g)
        self.preview_state = (games, time.time() + self.PREVIEW_SECONDS)
        return [g["id"] for g in games]

    def _preview_logos(self, teams):
        """Every logo version of the first favorite team (or a sample team), one per screen, labelled
        with the setting that picks it."""
        favs = [f.upper() for f in self.cfg.get("favorites", [])]
        abbr = next((f for f in favs if f in teams), None) or next(iter(teams), "")
        info = teams.get(abbr) or {}
        url = info.get("logo") or ""
        if not url:
            raise RuntimeError("couldn't get the team list from ESPN")
        size = self.app.height * 6
        seen, gallery = set(), []
        custom = self._custom_logos().get(abbr)
        options = ([("CUSTOM", "custom logo", custom)] if custom else [])
        for version in ("mlb_cap", "mlb_primary", "scoreboard", "primary"):
            if version.startswith("mlb") and self.league != "mlb":
                continue
            for dark in (True, False):
                for label, u in self.logo_candidates(url, abbr, version, dark=dark):
                    if label.startswith("ESPN full") and version != "primary":
                        continue                 # the fallback, listed under 'primary'
                    options.append((LOGO_VERSIONS[version].split(" (")[0], label + ("" if dark else ""), u))
        for setting, label, u in options:
            if u in seen:
                continue
            seen.add(u)
            img = self._fetch_logo(u, size)
            if img is None:
                continue
            key = f"gallery:{len(gallery)}"
            self.logo_src[key] = img
            self.logos[key] = fit_image(img, self.app.height, self.app.height)
            gallery.append({"key": key, "setting": setting, "label": label, "abbr": abbr})
        if not gallery:
            raise RuntimeError("no logos could be downloaded")
        self.logo_gallery = (gallery, time.time() + self.PREVIEW_SECONDS)
        return [g["key"] for g in gallery]

    def _gallery(self):
        gal = getattr(self, "logo_gallery", None)
        if gal and time.time() < gal[1]:
            return gal[0]
        return []

    def _render_gallery(self, c, item, t):
        from .logos import logo_image
        W, H = c.width, c.height
        team = {"logo_url": item["key"]}
        big = logo_image(self, team, 30, 30)
        if big is not None:
            c.paste(big, 0, 1)
        x = 33
        for sz in (16, 10):
            im = logo_image(self, team, sz, sz)
            if im is not None:
                c.paste(im, x, (H - sz) // 2 if sz == 16 else H - sz - 1)
            x += sz + 2
        x0 = 64
        c.scroll_text(x0, W, 1, item["label"].upper(), "4x6", WHITE, t)
        c.text(x0, 9, "LOGO VERSION:", "4x6", GRAY)
        c.scroll_text(x0, W, 16, item["setting"].upper(), "5x7", YELLOW, t)
        n = len(self._gallery())
        i = next((k for k, g in enumerate(self._gallery()) if g["key"] == item["key"]), 0)
        c.text(x0, H - 6, f"{item['abbr']}  {i + 1}/{n}", "4x6", DIM)

    def _preview_countdown(self, sport, path, teams, away, home):
        """Two sample countdowns: a few days out, and later today (game day)."""
        from .sample_games import make_event
        favs = [f.upper() for f in self.cfg.get("favorites", [])]
        fav = home if home.upper() in favs or not (away.upper() in favs) else away
        now = datetime.now(timezone.utc)
        today_evening = datetime.now().astimezone().replace(hour=20, minute=15, second=0, microsecond=0)
        game_day = today_evening.astimezone(timezone.utc)
        if game_day - now < timedelta(minutes=30):          # late at night: an hour and a half from now
            game_day = now + timedelta(hours=1, minutes=30)
        games = []
        for i, start in enumerate((now + timedelta(days=3, hours=4, minutes=27), game_day)):
            g = parse_event(make_event(self.league, sport, "pre", away, home, teams), self.league)
            g.update(id=f"preview-next-{i}", start=start, preview=True, countdown_preview=True,
                     countdown_fav=fav.upper(), network=g.get("network") or "ESPN")
            for side in ("home", "away"):
                self._load_logo(g[side])
            games.append(g)
        self.preview_state = (games, time.time() + self.PREVIEW_SECONDS)
        return [g["id"] for g in games]

    def status(self):
        s = super().status()
        s["games"] = len(self.games)
        s["live"] = sum(1 for g in self.games if g["state"] == "in")
        if self.logo_sources:
            fb = sum(1 for v in self.logo_sources.values() if v == "fallback")
            if fb:
                s["logo_note"] = f"{fb} logo(s) using a fallback version (retrying)"
        s["next_games"] = {fav: (g["start"].isoformat() if g.get("start") else "TBD")
                           for fav, g in self.next_games.items()}
        hidden = {}
        for fav, g in self.next_games.items():
            if self._offseason_hidden(g):
                shows = (g["start"] - timedelta(days=self._opener_days())).astimezone()
                hidden[fav] = f"season opener; countdown starts {shows:%b %-d}"
        if hidden:
            s["next_game_hidden"] = hidden
        problems = getattr(self, "next_game_problems", {})
        if problems:
            s["next_game_problems"] = problems
        return s

    # -- drawing --------------------------------------------------------------
    def render(self, c, slide, t):
        from .celebrations import Celebration
        if isinstance(slide, Celebration):
            from . import celebrations
            celebrations.render(self, c, slide, t, team_color)
            return
        from .recap import Recap
        if isinstance(slide, Recap):
            from . import recap
            recap.render(self, c, slide, t, team_color, self._clock_text)
            return
        if isinstance(slide, str) and slide.startswith("gallery:"):
            item = next((g for g in self._gallery() if g["key"] == slide), None)
            if item:
                self._render_gallery(c, item, t)
            return
        if isinstance(slide, str) and slide.startswith("pnext:"):
            hit = next(((fav, g) for sid, fav, g in self._preview_countdowns() if sid == slide), None)
            if hit:
                from .countdown_layouts import RENDERERS
                fn = RENDERERS.get(self.cfg.get("next_game_style", "classic"))
                if fn is None:
                    from .offdays import render_next_game as fn
                fn(self, c, hit[1], hit[0], t, team_color)
            return
        if isinstance(slide, str) and slide.startswith("next:"):
            g = self.next_games.get(slide[5:])
            if g:
                from .countdown_layouts import RENDERERS
                fn = RENDERERS.get(self.cfg.get("next_game_style", "classic"))
                if fn is None:
                    from .offdays import render_next_game as fn
                fn(self, c, g, slide[5:], t, team_color)
            return
        if slide == "bracket" and self.bracket_data:
            from .playoffs import render_bracket
            render_bracket(self, c, self.bracket_data, (self.playoff_data or {}).get("group", ""))
            return
        if isinstance(slide, str) and slide.startswith("playoffs:"):
            if self.playoff_data:
                from .playoffs import render_picture
                render_picture(self, c, self.playoff_data, int(slide.split(":")[1]))
            return
        if isinstance(slide, str) and slide.startswith("standings:"):
            _, gi, page = slide.split(":")
            if int(gi) < len(self.standings_data):
                from .offdays import render_standings
                name, rows = self.standings_data[int(gi)]
                render_standings(self, c, name, rows, int(page), self.sport, team_color)
            return
        g = self._game(slide)
        if not g:
            return
        if g["state"] == "in" and self.sport in ("hockey", "soccer") and self.cfg.get("goal_animation", True):
            from .hockey_layouts import goal_side, render_goal
            side = goal_side(self, g)
            if side:
                render_goal(self, c, g, side, team_color)
                return
        if g["state"] == "pre" and self.cfg.get("upcoming_layout", "classic") != "classic":
            from .pregame_layouts import RENDERERS as PRE
            fn = PRE.get(self.cfg.get("upcoming_layout"))
            if fn:
                fn(self, c, g, t, team_color)
                return
        if g["state"] == "in" and live_layouts(self.sport):
            fn = live_renderers(self.sport).get(self.cfg.get("live_layout", "classic"))
            if fn:
                fn(self, c, g, t, team_color, _cycle)
                return
        H, W = c.height, c.width
        away, home = g["away"], g["home"]
        col = self._column_width(c, g)            # width of each team column
        x0, x1 = col, W - col                      # center area
        mid = (x0 + x1) // 2
        detailed = self.cfg.get("detailed_live", True)

        final = self.cfg.get("final_style", "plain") if g["state"] == "post" else "plain"
        if final not in FINAL_STYLES:
            final = "plain"
        winner = self._final_winner(g) if final != "plain" else None
        pre = self.cfg.get("upcoming_style", "plain") if g["state"] == "pre" else "plain"
        if pre not in UPCOMING_STYLES:
            pre = "plain"
        live = self.cfg.get("live_style", "plain") if g["state"] == "in" else "plain"
        if live not in LIVE_STYLES:
            live = "plain"
        if live != "plain":
            winner = self._final_winner(g, live=True)             # the team that's ahead (None when tied)
        if final == "tint" or pre == "tint" or live == "tint":
            self._final_tint(c, g, mid, winner)
        self._team_columns(c, g, col, detailed)
        if g["state"] == "pre":
            if self.cfg.get("show_records", True):
                rc = (self._tc("text_info"),) * 2
                if pre in ("badge", "splitbar", "trim"):
                    rc = (scale(team_color(away), 0.85), scale(team_color(home), 0.85))
                c.text(x0 + 1, 2, away["record"], "4x6", rc[0])
                c.text_right(x1 - 1, 2, home["record"], "4x6", rc[1])
            vs = "v" if self.sport == "soccer" else "@"
            if pre == "badge":
                self._vs_badge(c, g, mid)
            c.text_centered(0, vs, "6x10", WHITE if pre == "badge" else self._tc("text_info"), x0, x1)
            c.scroll_text(x0, x1, 12, self._start_text(g), "5x8", self._tc("text_score"), t, align="center")
            items = self._info_items(g)[1:] or [(self._date_text(g), self._tc("text_info"))]
            if pre != "plain":
                items = self._upcoming_accents(c, g, pre, x0, x1, mid, items)
        else:
            score_cols = None
            if final == "winner" and winner:
                lose = scale(self._tc("text_loser"), 0.8)
                score_cols = tuple(team_color(g[s]) if s == winner else lose for s in ("away", "home"))
            elif live == "leader" and winner:
                score_cols = tuple(team_color(g[s]) if s == winner else self._tc("text_score") for s in ("away", "home"))
            self._scores_draw(c, g, mid, y=2, colors=score_cols)
            if g["state"] == "in" and detailed:
                self._live_graphics(c, g, x0, x1, mid)
            if g["state"] == "post" and self.cfg.get("show_final_records", True):
                # updated records under each team's side of the score
                rc = (self._tc("text_info"),) * 2
                if final in ("splitbar", "trim"):
                    rc = (scale(team_color(away), 0.85), scale(team_color(home), 0.85))
                c.text(x0 + 1, 17, away["record"], "4x6", rc[0])
                c.text_right(x1 - 1, 17, home["record"], "4x6", rc[1])
            items = self._info_items(g)
            if final != "plain":
                items = self._final_accents(c, g, final, winner, x0, x1, mid, items)
            elif live == "banner":
                items = self._upcoming_accents(c, g, "banner", x0, x1, mid, items)
            elif live != "plain":
                items = self._final_accents(c, g, {"leader": "winner"}.get(live, live), winner, x0, x1, mid, items,
                                            live=True)
        if not items:
            return
        text, color, local_t = _cycle(c, items, t, x1 - x0)
        logo = self._network_logo(g, text)
        if logo is not None:
            c.paste(logo, x0 + (x1 - x0 - logo.width) // 2, H - 7)
            return
        c.scroll_text(x0, x1, H - 7, text, "5x7", color, local_t, speed=INFO_SPEED, align="center", pause=0.6, gap=200)

    def _network_logo(self, g, text):
        """The network's pixel logo when `text` is the network line and logos are turned on."""
        if not (self.cfg.get("network_logos", True) and g.get("network") and text == g["network"].upper()):
            return None
        from .networks import network_logo
        return network_logo(g["network"])

    MAX_COLUMN = 44

    def _column_width(self, c, g):
        """Team column width for the standard view.

        Live games keep 32 px columns (their center holds clock, possession, bases...). Upcoming
        and final games only need as much center as their text, so the columns -- and wide
        logos, which are limited by width -- grow into the rest (up to MAX_COLUMN px).
        """
        H, W = c.height, c.width
        if g["state"] == "in":
            return H
        away, home = g["away"], g["home"]
        if g["state"] == "post":
            a_s, h_s = str(away["score"]), str(home["score"])
            # the score is drawn symmetrically around the dash, so it needs twice the wider side
            score_w = 2 * max(c.text_width(a_s, "7x13B"), c.text_width(h_s, "7x13B")) + c.text_width("-", "7x13B") + 6
            status_w = c.text_width(self._info_items(g)[0][0], "5x7") + 2
            rec_w = 0
            if self.cfg.get("show_final_records", True):
                rec_w = c.text_width(away["record"], "4x6") + c.text_width(home["record"], "4x6") + 6
            need = max(score_w, status_w, rec_w)
        else:
            rec_w = 0
            if self.cfg.get("show_records", True):
                rec_w = c.text_width(away["record"], "4x6") + c.text_width(home["record"], "4x6") + 14
            need = max(c.text_width(self._start_text(g), "5x8") + 2, rec_w, 40)
        col = (W - need) // 2
        return max(H, min(self.MAX_COLUMN, col))

    def _team_columns(self, c, g, col, detailed):
        """Logo for each side, with the team abbreviation centered underneath (or a full-size
        logo when abbreviations are turned off)."""
        from .football_layouts import logo_fit
        H, W = c.height, c.width
        show_rank = self.league in COLLEGE and self.cfg.get("show_rankings", True)
        names = self.cfg.get("show_abbreviations", True)
        for side, cx0 in (("away", 0), ("home", W - col)):
            team = g[side]
            lx = cx0 if side == "away" else cx0 + 1          # 1px gap on the inner side
            rank = str(team["rank"]) if show_rank and team["rank"] else ""
            if not names:
                # logo takes the whole column; a college rank sits in the outer bottom corner
                c.paste(logo_fit(self, team, col - 1, H, g["home" if side == "away" else "away"]), lx, 0)
                if rank:
                    rw = c.text_width(rank, "4x6")
                    rx = cx0 if side == "away" else cx0 + col - rw - 1
                    c.rect(rx, H - 7, rx + rw, H - 1, (0, 0, 0))
                    c.text(rx + (1 if side == "away" else 0), H - 6, rank, "4x6", WHITE)
            else:
                # logo fills the column and everything above the name
                c.paste(logo_fit(self, team, col - 1, H - 7, g["home" if side == "away" else "away"]), lx, 0)
                aw = c.text_width(team["abbr"], "5x8")
                rw = c.text_width(rank, "4x6") + 1 if rank else 0
                x = cx0 + (col - aw - rw) // 2
                if rank:
                    c.text(x, H - 7, rank, "4x6", GRAY)
                    x += rw
                c.text(x, H - 8, team["abbr"], "5x8", self._abbr_color(team))
            if g["state"] != "in" or not detailed:
                continue
            # markers in the team's column: possession (football), red cards (soccer)
            inner = cx0 + col - 3 if side == "away" else cx0 + 1
            if g["possession"] == side:
                c.rect(inner, H - 6, inner + 1, H - 4, RED if g["redzone"] else YELLOW)
            for i in range(min(3, team["red_cards"])):
                c.rect(inner, 1 + i * 5, inner + 1, 4 + i * 5, RED)

    def _tc(self, key):
        """A user-set text color (falls back to the default)."""
        return hex_to_rgb(self.cfg.get(key) or self.COMMON[key], hex_to_rgb(self.COMMON[key]))

    def _abbr_color(self, team):
        if self.cfg.get("abbr_color_mode") == "custom":
            return self._tc("abbr_color")
        return team_color(team)

    # ---------------------------------------------------------------- final-score accents
    def _final_winner(self, g, live=False):
        """'away' / 'home' / None. None while the favorite's score is hidden (don't give it away).
        With `live`, the team that's currently ahead."""
        if self.cfg.get("hide_favorite_score") and self.is_favorite(g):
            return None
        a, h = g["away"], g["home"]
        if live:
            return "away" if _num(a["score"]) > _num(h["score"]) else ("home" if _num(h["score"]) > _num(a["score"]) else None)
        if a["winner"] or _num(a["score"]) > _num(h["score"]):
            return "away"
        if h["winner"] or _num(h["score"]) > _num(a["score"]):
            return "home"
        return None

    def _final_tint(self, c, g, mid, winner):
        from .football_layouts import panel_color
        H, W = c.height, c.width
        for side, xa, xb in (("away", 0, mid - 1), ("home", mid, W - 1)):
            k = 0.8 if side == winner else (0.4 if winner else 0.6)
            c.rect(xa, 0, xb, H - 1, scale(panel_color(g[side]), k))

    def _final_accents(self, c, g, style, winner, x0, x1, mid, items, live=False):
        """Draw the chosen final style's extras; returns the (possibly changed) info line items."""
        from .football_layouts import panel_color
        H = c.height
        away, home = g["away"], g["home"]
        font = "7x13B"
        dw = c.text_width("-", font)
        hidden = self.cfg.get("hide_favorite_score") and self.is_favorite(g)
        a_s, h_s = ("?", "?") if hidden else (str(away["score"]), str(home["score"]))
        aw, hw = c.text_width(a_s, font), c.text_width(h_s, font)
        ax1, hx0 = mid - dw // 2 - 1, mid + dw // 2 + 2          # away score right edge / home score left edge
        win_col = team_color(g[winner]) if winner else None

        def recolor(col):
            if live:                       # live games keep their clock / info colors
                return items
            return [(txt, col) for txt, _ in items]

        if style == "winner":
            if winner:
                for side, xa, xb in (("away", ax1 - aw, ax1 - 1), ("home", hx0, hx0 + hw - 1)):
                    c.rect(xa, 15, xb, 15, scale(team_color(g[side]), 1 if side == winner else 0.45))
                items = recolor(win_col)
        elif style == "splitbar":
            sa, sh = (1, 1) if hidden else (max(0, _num(away["score"])), max(0, _num(home["score"])))
            tot = (sa + sh) or 2
            sa = sa if sa + sh else 1
            bx0, bx1 = x0 + 2, x1 - 3
            split = bx0 + round((bx1 - bx0 + 1) * sa / tot)
            if split > bx0:
                c.rect(bx0, 15, split - 1, 15, team_color(away))
            if split <= bx1:
                c.rect(split, 15, bx1, 15, team_color(home))
        elif style == "tint":
            items = recolor(WHITE)
        elif style == "banner":
            if winner:
                tm = g[winner]
                c.rect(x0 + 1, H - 8, x1 - 2, H - 1, panel_color(tm))
                c.rect(x0 + 1, H - 8, x1 - 2, H - 8, win_col)
                if winner == "away":                                  # arrow pointing at the winning score
                    x = ax1 - aw - 5
                    for i in range(3):
                        c.line(x + i, 6 + i, x + i, 10 - i, win_col)
                else:
                    x = hx0 + hw + 1
                    for i in range(3):
                        c.line(x + i, 8 - i, x + i, 8 + i, win_col)
                items = [(f"{tm['abbr']} WINS", WHITE)] + recolor(WHITE)
        elif style == "trim":
            for side, xa, xb in (("away", x0, mid - 1), ("home", mid, x1 - 1)):
                tm = g[side]
                second = accent_color(tm)
                c.rect(xa, 0, xb, 0, team_color(tm))
                c.rect(xa, H - 1, xb, H - 1, second)
            if winner:
                items = recolor(win_col)
        return items

    # ---------------------------------------------------------------- upcoming-game accents
    def _vs_badge(self, c, g, mid):
        """Rounded badge behind the '@', left half in the away color, right half in the home color."""
        from .football_layouts import panel_color
        x0, x1 = mid - 5, mid + 4
        c.draw.rounded_rectangle((x0, 0, x1, 10), radius=3, fill=panel_color(g["away"]))
        c.draw.rounded_rectangle((mid, 0, x1, 10), radius=3, fill=panel_color(g["home"]))
        c.rect(mid, 0, mid + 1, 10, panel_color(g["home"]))      # square off the seam

    def _upcoming_accents(self, c, g, style, x0, x1, mid, items):
        from .football_layouts import panel_color
        H = c.height
        away, home = g["away"], g["home"]

        def recolor(col):
            return [(txt, col) for txt, _ in items]

        if style == "splitbar":
            bx0, bx1 = x0 + 2, x1 - 3
            c.rect(bx0, 21, mid - 1, 21, team_color(away))
            c.rect(mid, 21, bx1, 21, team_color(home))
        elif style == "tint":
            items = recolor(WHITE)
        elif style == "banner":
            c.rect(x0 + 1, H - 8, mid - 1, H - 1, panel_color(away))
            c.rect(mid, H - 8, x1 - 2, H - 1, panel_color(home))
            c.rect(x0 + 1, H - 8, mid - 1, H - 8, team_color(away))
            c.rect(mid, H - 8, x1 - 2, H - 8, team_color(home))
            items = recolor(WHITE)
        elif style == "trim":
            for side, xa, xb in (("away", x0, mid - 1), ("home", mid, x1 - 1)):
                tm = g[side]
                second = accent_color(tm)
                c.rect(xa, 0, xb, 0, team_color(tm))
                c.rect(xa, H - 1, xb, H - 1, second)
            c.rect(mid - 5, 0, mid + 4, 0, (0, 0, 0))                 # leave room around the '@'
        return items

    def _scores_draw(self, c, g, mid, y=9, colors=None):
        away, home = g["away"], g["home"]
        a_s, h_s = str(away["score"]), str(home["score"])
        if self.cfg.get("hide_favorite_score") and self.is_favorite(g):
            a_s = h_s = "?"
        a_col = h_col = self._tc("text_score")
        if g["state"] == "post" and a_s != "?":
            if away["winner"] or _num(a_s) > _num(h_s):
                h_col = self._tc("text_loser")
            elif home["winner"] or _num(h_s) > _num(a_s):
                a_col = self._tc("text_loser")
        if colors:
            a_col, h_col = colors
        if self.cfg.get("score_flash", True) and int(time.monotonic() * 3) % 2 == 0:
            now = time.monotonic()
            if self.flash.get(f"{g['id']}|away", 0) > now:
                a_col = YELLOW
            if self.flash.get(f"{g['id']}|home", 0) > now:
                h_col = YELLOW
        font = "7x13B"
        dash_w = c.text_width("-", font)
        c.text(mid - dash_w // 2, y, "-", font, self._tc("text_info"))
        c.text_right(mid - dash_w // 2 - 1, y, a_s, font, a_col)
        c.text(mid + dash_w // 2 + 2, y, h_s, font, h_col)

    def _live_graphics(self, c, g, x0, x1, mid):
        """Extras in the band between the score and the info line (y 16-22)."""
        if self.sport == "football":
            for side, xs in (("away", x0 + 2), ("home", x1 - 10)):
                n = g["timeouts"].get(side)
                if isinstance(n, int):
                    for i in range(3):
                        c.rect(xs + i * 3, 18, xs + i * 3 + 1, 18, YELLOW if i < n else DIM)
        elif self.sport == "baseball":
            _bases(c, mid - 4, 16, g["bases"])
            outs = g["outs"]
            if isinstance(outs, int):
                for i in range(3):
                    x = mid + 4 + i * 3
                    c.rect(x, 19, x + 1, 20, RED if i < outs else DIM)

    def _date_text(self, g):
        if not g["start"]:
            return ""
        return g["start"].astimezone().strftime("%a %b %-d").upper()

    def _info_items(self, g):
        """Lines for the bottom info row; they rotate every few seconds."""
        c = self.cfg
        items = []
        playoff = [(x.upper(), YELLOW) for x in (g.get("note"), g.get("series")) if x]
        if g["state"] == "pre":
            items.append((self._start_text(g), self._tc("text_score")))
            items += playoff
            if c.get("show_network", True) and g["network"]:
                items.append((g["network"].upper(), self._tc("text_info")))
            if c.get("show_odds"):
                parts = [g["odds"]] if g["odds"] else []
                ou = g["over_under"]
                if ou not in (None, ""):
                    parts.append(f"O/U {ou:g}" if isinstance(ou, (int, float)) else f"O/U {ou}")
                if parts:
                    items.append((" ".join(parts), (120, 200, 120)))
            return items
        if g["state"] == "post":
            d = g["detail"].upper()
            return [("FINAL" + d[5:] if d.startswith("FINAL") else d or "FINAL", self._tc("text_info"))] + \
                [(x.upper(), YELLOW) for x in (g.get("series"),) if x]

        items.append((self._clock_text(g), self._tc("text_status")))
        if g.get("series"):
            items.append((g["series"].upper(), YELLOW))
        if c.get("detailed_live", True):
            sport = self.sport
            if sport == "football":
                if g["down"] and g["distance"] is not None:
                    dist = "G" if "goal" in g["down_text"].lower() else g["distance"]
                    txt = f"{g['down']}&{dist}" + (f" {g['spot'].replace(' ', '')}" if g["spot"] else "")
                    items.append((txt, RED if g["redzone"] else ORANGE))
                elif g["down_text"]:
                    items.append((short_down(g["down_text"]), RED if g["redzone"] else ORANGE))
            elif sport == "baseball":
                if g["balls"] is not None and g["strikes"] is not None:
                    items.append((f"COUNT {g['balls']}-{g['strikes']}", ORANGE))
                if g["pitcher"]:
                    items.append((f"P: {g['pitcher']}", self._tc("text_info")))
                if g["batter"]:
                    items.append((f"AB: {g['batter']}", self._tc("text_info")))
                a, h = g["away"], g["home"]
                if a["hits"] is not None and h["hits"] is not None:
                    items.append((f"H {a['hits']}-{h['hits']}  E {a['errors'] or 0}-{h['errors'] or 0}", self._tc("text_info")))
            elif sport == "basketball":
                for side in ("away", "home"):
                    t = g[side]
                    pts = next((l for l in t["leaders"] if l[0].upper() in ("PTS", "POINTS")), None)
                    if pts:
                        items.append((f"{t['abbr']} {pts[1]} {pts[2]}", self._tc("text_info")))
            elif sport == "hockey":
                a, h = g["away"]["shots"], g["home"]["shots"]
                if a is not None and h is not None:
                    items.append((f"SOG {a}-{h}", self._tc("text_info")))
            elif sport == "soccer" and g["last_goal"]:
                items.append((g["last_goal"], ORANGE))
        if c.get("show_last_play", True) and g["last_play"] and self.sport != "soccer":
            items.append((g["last_play"], self._tc("text_score")))
        return items

    def _clock_text(self, g):
        detail = g["detail"]
        sport = self.sport
        if sport == "hockey":
            from .hockey_layouts import clock_text, period_label
            return f"{period_label(g)} {clock_text(g)}".strip() if g["period"] else detail.upper()
        if sport in ("basketball", "hockey", "football") and g["clock"] and g["period"] \
                and "half" not in detail.lower() and "end" not in detail.lower():
            prefix = {"football": "Q", "basketball": "Q", "hockey": "P"}[sport]
            if self.league in ("ncaam",):
                prefix = "H"
            per = g["period"]
            limit = {"Q": 4, "P": 3, "H": 2}[prefix]
            label = f"{prefix}{per}" if per <= limit else ("OT" if per == limit + 1 else f"{per - limit}OT")
            return f"{label} {g['clock']}"
        return detail.upper()

    def _start_text(self, g):
        if g["start"] is None:
            return g["detail"]
        local = g["start"].astimezone()
        now = datetime.now().astimezone()
        if g["time_tbd"]:
            return local.strftime("%a %-m/%-d").upper() + " TBD"
        hm = local.strftime("%I:%M%p").lstrip("0").replace(":00", "")
        if local.date() == now.date():
            return hm
        if local.date() - now.date() == timedelta(days=1):
            return "TMRW " + hm
        if (local.date() - now.date()).days < 7:
            return local.strftime("%a").upper() + " " + hm
        return local.strftime("%-m/%-d ") + hm


def live_layouts(sport):
    """{layout key: label} for sports that have graphical live layouts."""
    if sport == "football":
        from .football_layouts import LAYOUTS
        return LAYOUTS
    if sport == "baseball":
        from .baseball_layouts import LAYOUTS
        return LAYOUTS
    if sport == "hockey":
        from .hockey_layouts import LAYOUTS
        return LAYOUTS
    if sport == "soccer":
        from .soccer_layouts import LAYOUTS
        return LAYOUTS
    return {}


def live_renderers(sport):
    if sport == "football":
        from .football_layouts import RENDERERS
        return RENDERERS
    if sport == "baseball":
        from .baseball_layouts import RENDERERS
        return RENDERERS
    if sport == "hockey":
        from .hockey_layouts import RENDERERS
        return RENDERERS
    if sport == "soccer":
        from .soccer_layouts import RENDERERS
        return RENDERERS
    return {}


def _cycle(c, items, t, avail, font="5x7"):
    """Pick the info line to show at time t. Long lines get enough time to scroll fully."""
    if not items:
        return "", WHITE, 0.0
    durs = []
    for text, _ in items:
        w = c.text_width(text, font)
        durs.append(INFO_SECONDS if w <= avail else 0.6 + (w - avail) / INFO_SPEED + 1.2)
    t = t % sum(durs)
    for (text, color), d in zip(items, durs):
        if t < d:
            return text, color, t
        t -= d
    return items[0][0], items[0][1], 0.0


def _bases(c, mid, y, bases):
    """Tiny diamond: 2nd base on top, 3rd left, 1st right."""
    for (dx, dy), on in zip(((4, 3), (0, 0), (-4, 3)), bases):
        cx, cy = mid + dx, y + dy
        col = YELLOW if on else DIM
        for px, py in ((cx, cy), (cx - 1, cy + 1), (cx + 1, cy + 1), (cx, cy + 2)):
            c.pixel(px, py, col)
        if on:
            c.pixel(cx, cy + 1, col)


def _make(key):
    sport, _, title, _ = LEAGUES[key]
    cls = type(f"{key.upper()}Board", (LeagueBoard,), {
        "name": key, "league": key, "title": title,
        "description": f"{title} scores from ESPN: live, upcoming and final games.",
        "DEFAULTS": dict(LeagueBoard.COMMON, enabled=key in DEFAULT_ON),
    })
    cls.FIELDS = cls.build_fields()
    return cls


LEAGUE_BOARDS = [_make(k) for k in LEAGUES]
