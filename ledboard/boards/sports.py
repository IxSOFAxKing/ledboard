"""Live scores from ESPN's public scoreboard API (no API key required).

Every league is its own board (NFL, NBA, MLB...), so each one has its own
place in the rotation, its own favorites, scroll mode, takeover and display
options. All of ESPN's fields are treated as optional: when a field is missing
the corresponding detail is simply not drawn.
"""
from __future__ import annotations

import colorsys
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from .base import Board
from ..gfx import DIM, GRAY, RED, WHITE, YELLOW, fetch_image, fit_image, hex_to_rgb, readable

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
        t = c.get("team", {})
        rec = c.get("records") or []
        side = c.get("homeAway", "home")
        rank = (c.get("curatedRank") or {}).get("current")
        leaders = []
        for cat in c.get("leaders") or []:
            top = (cat.get("leaders") or [{}])[0]
            if top.get("athlete"):
                leaders.append((cat.get("abbreviation") or cat.get("shortDisplayName") or cat.get("name", ""),
                                _athlete(top), top.get("displayValue", "")))
        teams[side] = {
            "id": str(t.get("id", c.get("id", ""))),
            "abbr": t.get("abbreviation") or (t.get("shortDisplayName") or "?")[:4].upper(),
            "name": t.get("shortDisplayName") or t.get("displayName") or "",
            "score": c.get("score", ""),
            "color": hex_to_rgb(t.get("color"), GRAY),
            "alt": hex_to_rgb(t.get("alternateColor"), WHITE),
            "logo_url": t.get("logo"),
            "record": rec[0].get("summary", "") if rec else "",
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

    sit = comp.get("situation") or {}
    odds = (comp.get("odds") or [{}])[0] or {}
    network = ""
    for b in comp.get("broadcasts") or []:
        if b.get("names"):
            network = b["names"][0]
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


def team_color(team):
    """Pick whichever of the team's colors is most visible on black, then brighten it."""
    def val(c):
        h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in c))
        return v * (0.5 + s / 2)
    base = team["color"]
    if val(base) < 0.35 and val(team["alt"]) > val(base) and team["alt"] not in ((255, 255, 255), (0, 0, 0)):
        base = team["alt"]
    return readable(base, min_value=0.9)


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
class LeagueBoard(Board):
    """One league. Concrete classes are generated per league at the bottom of this file."""
    league = "nfl"
    refresh = 60

    COMMON = {
        "enabled": False, "duration": 7, "refresh": 60, "live_refresh": 15,
        "favorites": [], "favorites_only": False, "live_only": False, "takeover": False,
        "show_pregame": True, "show_final": True, "final_hours": 18, "upcoming_hours": 48, "max_games": 20,
        "show_records": True, "show_rankings": True, "show_odds": False, "show_network": True,
        "hide_favorite_score": False, "detailed_live": True, "show_last_play": True, "score_flash": True,
        "use_dark_logos": True, "live_layout": "classic", "show_win_probability": True, "goal_animation": True,
        "celebrations": True, "celebrate_field_goals": False, "celebrate_runs": False,
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
            {"key": "show_pregame", "label": "Show upcoming games", "type": "bool", "section": "Which games",
             "show_if": {"live_only": False}},
            {"key": "upcoming_hours", "label": "...starting within (hours)", "type": "number", "section": "Which games",
             "show_if": {"live_only": False, "show_pregame": True}},
            {"key": "show_final", "label": "Show final scores", "type": "bool", "section": "Which games",
             "show_if": {"live_only": False}},
            {"key": "final_hours", "label": "...that started within (hours)", "type": "number", "section": "Which games",
             "show_if": {"live_only": False, "show_final": True}},
            {"key": "max_games", "label": "Max games per rotation", "type": "number", "section": "Which games"},
            {"key": "show_records", "label": "Show team records before the game", "type": "bool", "section": "Upcoming games"},
            {"key": "show_network", "label": "Show TV network", "type": "bool", "section": "Upcoming games"},
            {"key": "show_odds", "label": "Show betting line / over-under", "type": "bool", "section": "Upcoming games"},
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
            f.append({"key": "simulate", "label": "Simulate", "type": "simulate", "section": "Celebrations",
                      "options": SIM_KINDS[sport]})
        f += [
            {"key": "preview", "label": "Preview", "type": "actions", "section": "Preview", "action": "preview",
             "options": {"live": "Live game", "pre": "Upcoming", "post": "Final", "all": "All three"},
             "help": "Shows sample games with your current settings and layout for 3 minutes, even in the "
                     "off-season. Uses your favorite teams when set."},
            {"key": "duration", "label": "Seconds per game", "type": "number", "section": "Timing"},
            {"key": "refresh", "label": "Refresh interval (s)", "type": "number", "section": "Timing"},
            {"key": "live_refresh", "label": "Refresh interval while games are live (s)", "type": "number", "section": "Timing"},
            {"key": "use_dark_logos", "label": "Prefer logos designed for dark backgrounds", "type": "bool", "section": "Logos"},
        ]
        return f

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.games: list[dict] = []
        self.logos: dict[str, object] = {}       # url -> logo fitted to the display height
        self.logo_src: dict[str, object] = {}    # url -> trimmed hi-res copy for other sizes
        self.flash: dict[str, float] = {}     # "gameid|side" -> until (monotonic)
        self._scores: dict[str, str] = {}
        self.pending = []                     # queued Celebration slides
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
    def update(self):
        sport, path, _, extra = LEAGUES[self.league]
        params = {"limit": 200}
        if self.favorites():
            params.update(extra)  # e.g. all FBS games so unranked favorites show up
        r = self.session.get(ESPN.format(sport=sport, league=path), params=params, timeout=15)
        r.raise_for_status()
        games = [g for g in (parse_event(ev, self.league) for ev in r.json().get("events", [])) if g]
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
        self.games = games

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
        with self._cel_lock:
            self.pending.append(Celebration(kind, side, _snapshot(g), self.sport))
            self.pending = self.pending[-3:]
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
        for g in self.games:
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
        with self._cel_lock:
            self.pending.append(Celebration(kind, side, g, self.sport))

    def slide_duration(self, slide):
        from .celebrations import DURATION, Celebration
        if isinstance(slide, Celebration):
            return DURATION
        return self.duration

    def _load_logo(self, team):
        url = team.get("logo_url")
        if not url or url in self.logos:
            return
        size = self.app.height * 2
        img = None
        if self.cfg.get("use_dark_logos", True) and "/500/" in url:
            img = fetch_image(url.replace("/500/", "/500-dark/"), size, size, trim=True)
        if img is None:
            img = fetch_image(url, size, size, trim=True)
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
                if start and now - start > timedelta(hours=float(c.get("final_hours", 18) or 18) + 3):
                    continue
            out.append(g)
        order = {"in": 0, "pre": 1, "post": 2}
        out.sort(key=lambda g: (not self.is_favorite(g), order.get(g["state"], 3), g["start"] or now))
        out = out[: int(c.get("max_games", 20) or 20)]
        return self._preview_games() + out

    def slides(self):
        return [g["id"] for g in self.visible_games()]

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
            return games
        return []

    def preview(self, kind="all"):
        """Show sample games (live / pre / post / all) for a few minutes, e.g. in the off-season."""
        from .sample_games import fetch_teams, make_event, pick_teams
        sport, path, _, _ = LEAGUES[self.league]
        teams = fetch_teams(sport, path)
        away, home = pick_teams(self.league, [f.upper() for f in self.cfg.get("favorites", [])], teams)
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

    def status(self):
        s = super().status()
        s["games"] = len(self.games)
        s["live"] = sum(1 for g in self.games if g["state"] == "in")
        return s

    # -- drawing --------------------------------------------------------------
    def render(self, c, slide, t):
        from .celebrations import Celebration
        if isinstance(slide, Celebration):
            from . import celebrations
            celebrations.render(self, c, slide, t, team_color)
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
        if g["state"] == "in" and live_layouts(self.sport):
            fn = live_renderers(self.sport).get(self.cfg.get("live_layout", "classic"))
            if fn:
                fn(self, c, g, t, team_color, _cycle)
                return
        H, W = c.height, c.width
        away, home = g["away"], g["home"]
        col = H                                    # width of each team column
        x0, x1 = col, W - col                      # center area
        mid = (x0 + x1) // 2
        detailed = self.cfg.get("detailed_live", True)

        self._team_columns(c, g, col, detailed)
        if g["state"] == "pre":
            if self.cfg.get("show_records", True):
                c.text(x0 + 1, 2, away["record"], "4x6", GRAY)
                c.text_right(x1 - 1, 2, home["record"], "4x6", GRAY)
            c.text_centered(0, "v" if self.sport == "soccer" else "@", "6x10", GRAY, x0, x1)
            c.scroll_text(x0, x1, 12, self._start_text(g), "5x8", WHITE, t, align="center")
            items = self._info_items(g)[1:] or [(self._date_text(g), GRAY)]
        else:
            self._scores_draw(c, g, mid, y=2)
            if g["state"] == "in" and detailed:
                self._live_graphics(c, g, x0, x1, mid)
            items = self._info_items(g)
        text, color, local_t = _cycle(c, items, t, x1 - x0)
        c.scroll_text(x0, x1, H - 7, text, "5x7", color, local_t, speed=INFO_SPEED, align="center", pause=0.6, gap=200)

    def _team_columns(self, c, g, col, detailed):
        """Logo with the team abbreviation centered underneath, for each side."""
        from .football_layouts import logo_fit
        H, W = c.height, c.width
        show_rank = self.league in COLLEGE and self.cfg.get("show_rankings", True)
        for side, cx0 in (("away", 0), ("home", W - col)):
            team = g[side]
            # logo fills the column (minus a 1px gap on the inner side) and everything above the name
            c.paste(logo_fit(self, team, col - 1, H - 7), cx0 if side == "away" else cx0 + 1, 0)
            rank = str(team["rank"]) if show_rank and team["rank"] else ""
            aw = c.text_width(team["abbr"], "5x8")
            rw = c.text_width(rank, "4x6") + 1 if rank else 0
            x = cx0 + (col - aw - rw) // 2
            if rank:
                c.text(x, H - 7, rank, "4x6", GRAY)
                x += rw
            c.text(x, H - 8, team["abbr"], "5x8", team_color(team))
            if g["state"] != "in" or not detailed:
                continue
            # markers in the team's column: possession (football), red cards (soccer)
            inner = cx0 + col - 3 if side == "away" else cx0 + 1
            if g["possession"] == side:
                c.rect(inner, H - 6, inner + 1, H - 4, RED if g["redzone"] else YELLOW)
            for i in range(min(3, team["red_cards"])):
                c.rect(inner, 1 + i * 5, inner + 1, 4 + i * 5, RED)

    def _scores_draw(self, c, g, mid, y=9):
        away, home = g["away"], g["home"]
        a_s, h_s = str(away["score"]), str(home["score"])
        if self.cfg.get("hide_favorite_score") and self.is_favorite(g):
            a_s = h_s = "?"
        a_col = h_col = WHITE
        if g["state"] == "post" and a_s != "?":
            if away["winner"] or _num(a_s) > _num(h_s):
                h_col = GRAY
            elif home["winner"] or _num(h_s) > _num(a_s):
                a_col = GRAY
        if self.cfg.get("score_flash", True) and int(time.monotonic() * 3) % 2 == 0:
            now = time.monotonic()
            if self.flash.get(f"{g['id']}|away", 0) > now:
                a_col = YELLOW
            if self.flash.get(f"{g['id']}|home", 0) > now:
                h_col = YELLOW
        font = "7x13B"
        dash_w = c.text_width("-", font)
        c.text(mid - dash_w // 2, y, "-", font, GRAY)
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
        if g["state"] == "pre":
            items.append((self._start_text(g), WHITE))
            if c.get("show_network", True) and g["network"]:
                items.append((g["network"].upper(), GRAY))
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
            return [("FINAL" + d[5:] if d.startswith("FINAL") else d or "FINAL", GRAY)]

        items.append((self._clock_text(g), YELLOW))
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
                    items.append((f"P: {g['pitcher']}", GRAY))
                if g["batter"]:
                    items.append((f"AB: {g['batter']}", GRAY))
                a, h = g["away"], g["home"]
                if a["hits"] is not None and h["hits"] is not None:
                    items.append((f"H {a['hits']}-{h['hits']}  E {a['errors'] or 0}-{h['errors'] or 0}", GRAY))
            elif sport == "basketball":
                for side in ("away", "home"):
                    t = g[side]
                    pts = next((l for l in t["leaders"] if l[0].upper() in ("PTS", "POINTS")), None)
                    if pts:
                        items.append((f"{t['abbr']} {pts[1]} {pts[2]}", GRAY))
            elif sport == "hockey":
                a, h = g["away"]["shots"], g["home"]["shots"]
                if a is not None and h is not None:
                    items.append((f"SOG {a}-{h}", GRAY))
            elif sport == "soccer" and g["last_goal"]:
                items.append((g["last_goal"], ORANGE))
        if c.get("show_last_play", True) and g["last_play"] and self.sport != "soccer":
            items.append((g["last_play"], WHITE))
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
