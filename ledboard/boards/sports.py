"""Live scores from ESPN's public scoreboard API (no API key required)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from .base import Board
from ..gfx import (GRAY, DIM, RED, WHITE, YELLOW, fetch_image, hex_to_rgb, readable, scale)

log = logging.getLogger(__name__)

ESPN = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"

# key: (sport, espn league, display name, extra query params)
LEAGUES = {
    "nfl": ("football", "nfl", "NFL", {}),
    "ncaaf": ("football", "college-football", "College Football", {"groups": "80"}),
    "nba": ("basketball", "nba", "NBA", {}),
    "wnba": ("basketball", "wnba", "WNBA", {}),
    "ncaam": ("basketball", "mens-college-basketball", "Men's College Basketball", {"groups": "50"}),
    "ncaaw": ("basketball", "womens-college-basketball", "Women's College Basketball", {"groups": "50"}),
    "mlb": ("baseball", "mlb", "MLB", {}),
    "nhl": ("hockey", "nhl", "NHL", {}),
    "mls": ("soccer", "usa.1", "MLS", {}),
    "epl": ("soccer", "eng.1", "Premier League", {}),
    "laliga": ("soccer", "esp.1", "La Liga", {}),
    "bundesliga": ("soccer", "ger.1", "Bundesliga", {}),
    "seriea": ("soccer", "ita.1", "Serie A", {}),
    "ligue1": ("soccer", "fra.1", "Ligue 1", {}),
    "ucl": ("soccer", "uefa.champions", "Champions League", {}),
}


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
        }
    if "home" not in teams or "away" not in teams:
        return None
    sit = comp.get("situation") or {}
    game = {
        "id": str(ev.get("id")),
        "league": league_key,
        "sport": LEAGUES[league_key][0],
        "state": stype.get("state", "pre"),          # pre | in | post
        "detail": stype.get("shortDetail") or stype.get("detail") or "",
        "description": stype.get("description", ""),
        "period": status.get("period", 0),
        "clock": status.get("displayClock", ""),
        "start": parse_time(ev.get("date") or comp.get("date")),
        "away": teams["away"],
        "home": teams["home"],
        "possession": None,
        "situation": "",
        "redzone": bool(sit.get("isRedZone")),
        "outs": sit.get("outs"),
        "balls": sit.get("balls"),
        "strikes": sit.get("strikes"),
        "bases": [bool(sit.get("onFirst")), bool(sit.get("onSecond")), bool(sit.get("onThird"))],
        "time_tbd": bool(comp.get("timeValid") is False),
    }
    poss = str(sit.get("possession", "") or "")
    if poss:
        game["possession"] = "home" if poss == teams["home"]["id"] else ("away" if poss == teams["away"]["id"] else None)
    game["situation"] = sit.get("shortDownDistanceText") or sit.get("downDistanceText") or ""
    return game


class SportsBoard(Board):
    name = "sports"
    title = "Sports Scores"
    description = "Live, upcoming and final scores from ESPN. Add favorite teams by abbreviation (e.g. BUF, KC, NYY)."
    refresh = 60
    DEFAULTS = {
        "enabled": True,
        "duration": 7,
        "refresh": 60,
        "live_refresh": 15,
        "favorites_only": False,
        "show_pregame": True,
        "show_final": True,
        "final_hours": 18,
        "upcoming_hours": 48,
        "max_games": 20,
        "takeover": False,
        "use_dark_logos": True,
        "leagues": {k: {"enabled": k in ("nfl", "nba", "mlb", "nhl"), "favorites": []} for k in LEAGUES},
    }
    FIELDS = [
        {"key": "duration", "label": "Seconds per game", "type": "number"},
        {"key": "leagues", "label": "Leagues & favorite teams", "type": "leagues",
         "options": {k: v[2] for k, v in LEAGUES.items()}},
        {"key": "favorites_only", "label": "Only show favorite teams (leagues with no favorites show all games)", "type": "bool"},
        {"key": "takeover", "label": "Take over the display while a favorite team is playing", "type": "bool"},
        {"key": "show_pregame", "label": "Show upcoming games", "type": "bool"},
        {"key": "show_final", "label": "Show final scores", "type": "bool"},
        {"key": "final_hours", "label": "Hide finals older than (hours)", "type": "number"},
        {"key": "upcoming_hours", "label": "Hide games starting more than (hours) away", "type": "number"},
        {"key": "max_games", "label": "Max games per rotation", "type": "number"},
        {"key": "use_dark_logos", "label": "Prefer logos designed for dark backgrounds", "type": "bool"},
        {"key": "refresh", "label": "Refresh interval (s)", "type": "number"},
        {"key": "live_refresh", "label": "Refresh interval while games are live (s)", "type": "number"},
    ]

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.games: list[dict] = []
        self.logos: dict[str, object] = {}
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "ledboard/1.0"

    def interval(self):
        if any(g["state"] == "in" for g in self.games):
            return float(self.cfg.get("live_refresh", 15))
        return float(self.cfg.get("refresh", 60))

    def _favorites(self, league):
        lc = self.cfg.get("leagues", {}).get(league, {})
        return {f.strip().upper() for f in lc.get("favorites", []) if f.strip()}

    def is_favorite(self, g):
        fav = self._favorites(g["league"])
        return g["home"]["abbr"].upper() in fav or g["away"]["abbr"].upper() in fav

    def update(self):
        games, errors = [], []
        for key, lc in self.cfg.get("leagues", {}).items():
            if not lc.get("enabled") or key not in LEAGUES:
                continue
            sport, league, _, extra = LEAGUES[key]
            params = {"limit": 200}
            if lc.get("favorites"):
                params.update(extra)  # e.g. all FBS games so non-ranked favorites show up
            try:
                r = self.session.get(ESPN.format(sport=sport, league=league), params=params, timeout=10)
                r.raise_for_status()
                for ev in r.json().get("events", []):
                    g = parse_event(ev, key)
                    if g:
                        games.append(g)
            except requests.RequestException as e:
                errors.append(f"{key}: {type(e).__name__}")
            except Exception as e:
                errors.append(f"{key}: {e}")
        for g in games:
            for side in ("home", "away"):
                self._load_logo(g[side])
        self.games = games
        if errors and not games:
            raise RuntimeError("; ".join(errors))
        if errors:
            self.last_error = "; ".join(errors)

    def _load_logo(self, team):
        url = team.get("logo_url")
        if not url:
            return
        if url in self.logos:
            return
        size = self.app.height
        img = None
        if self.cfg.get("use_dark_logos", True) and "/500/" in url:
            img = fetch_image(url.replace("/500/", "/500-dark/"), size, size)
        if img is None:
            img = fetch_image(url, size, size)
        self.logos[url] = img

    def visible_games(self):
        now = datetime.now(timezone.utc)
        fav_only = self.cfg.get("favorites_only")
        out = []
        for g in self.games:
            if fav_only and self._favorites(g["league"]) and not self.is_favorite(g):
                continue
            start = g["start"]
            if g["state"] == "pre":
                if not self.cfg.get("show_pregame", True):
                    continue
                if start and start - now > timedelta(hours=float(self.cfg.get("upcoming_hours", 48))):
                    continue
            if g["state"] == "post":
                if not self.cfg.get("show_final", True):
                    continue
                if start and now - start > timedelta(hours=float(self.cfg.get("final_hours", 18)) + 3):
                    continue
            out.append(g)
        order = {"in": 0, "pre": 1, "post": 2}
        out.sort(key=lambda g: (order.get(g["state"], 3), not self.is_favorite(g),
                                g["start"] or now))
        return out[: int(self.cfg.get("max_games", 20))]

    def slides(self):
        return [g["id"] + "|" + g["league"] for g in self.visible_games()]

    def takeover_slides(self):
        if not self.cfg.get("takeover"):
            return []
        return [g["id"] + "|" + g["league"] for g in self.visible_games()
                if g["state"] == "in" and self.is_favorite(g)]

    def _game(self, slide):
        for g in self.games:
            if g["id"] + "|" + g["league"] == slide:
                return g
        return None

    # -- drawing ------------------------------------------------------------
    def render(self, c, slide, t):
        g = self._game(slide)
        if not g:
            return
        H, W = c.height, c.width
        L = H  # logo size
        away, home = g["away"], g["home"]
        # logos sit at the far edges, nudged outward so the center has room
        c.paste(self.logos.get(away.get("logo_url")), -2, 0)
        c.paste(self.logos.get(home.get("logo_url")), W - L + 2, 0)
        x0, x1 = L - 1, W - L + 1
        # darken behind the center text so overlapping logo edges don't clash
        c.rect(x0, 0, x1 - 1, H - 1, (0, 0, 0))

        acol, hcol = team_color(away), team_color(home)
        if g["state"] == "pre":
            self._abbrs(c, g, x0, x1, acol, hcol)
            c.text(x0 + 1, 10, away["record"], "4x6", GRAY)
            c.text_right(x1 - 1, 10, home["record"], "4x6", GRAY)
            c.text_centered(11, "@" if g["sport"] != "soccer" else "v", "6x10", WHITE, x0, x1)
            c.scroll_text(x0, x1, H - 7, self._start_text(g), "5x7", WHITE, t, align="center")
            return

        self._abbrs(c, g, x0, x1, acol, hcol)
        a_s, h_s = str(away["score"]), str(home["score"])
        a_col = h_col = WHITE
        if g["state"] == "post":
            if away["winner"] or (_num(a_s) > _num(h_s)):
                h_col = GRAY
            elif home["winner"] or (_num(h_s) > _num(a_s)):
                a_col = GRAY
        score_font = "7x13B"
        mid = (x0 + x1) // 2
        dash_w = c.text_width("-", score_font)
        c.text(mid - dash_w // 2, 9, "-", score_font, GRAY)
        c.text_right(mid - dash_w // 2 - 1, 9, a_s, score_font, a_col)
        c.text(mid + dash_w // 2 + 2, 9, h_s, score_font, h_col)

        # football possession: small marker beside the team abbreviation
        if g["state"] == "in" and g["possession"]:
            color = RED if g["redzone"] else YELLOW
            if g["possession"] == "away":
                ax = x0 + 2 + c.text_width(away["abbr"], "5x8")
                c.rect(ax, 2, ax + 1, 4, color)
            else:
                hx = x1 - 3 - c.text_width(home["abbr"], "5x8")
                c.rect(hx - 1, 2, hx, 4, color)

        if g["state"] == "in" and g["sport"] == "baseball":
            self._bases(c, mid, 1, g["bases"])

        status, scol = self._status(g, t)
        c.scroll_text(x0, x1, H - 7, status, "5x7", scol, t % 3, align="center", pause=0.3)

    @staticmethod
    def _bases(c, mid, y, bases):
        # tiny diamond: 2nd base on top, 3rd left, 1st right
        for (dx, dy), on in zip(((4, 3), (0, 0), (-4, 3)), bases):
            cx, cy = mid + dx, y + dy
            col = YELLOW if on else DIM
            c.pixel(cx, cy, col)
            c.pixel(cx - 1, cy + 1, col); c.pixel(cx + 1, cy + 1, col)
            c.pixel(cx, cy + 2, col)
            if on:
                c.pixel(cx, cy + 1, col)

    def _abbrs(self, c, g, x0, x1, acol, hcol):
        c.text(x0 + 1, 0, g["away"]["abbr"], "5x8", acol)
        c.text_right(x1 - 1, 0, g["home"]["abbr"], "5x8", hcol)

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

    def _status(self, g, t):
        if g["state"] == "post":
            d = g["detail"].upper()
            return ("FINAL" + d[5:] if d.startswith("FINAL") else d or "FINAL"), GRAY
        detail = g["detail"]
        if g["sport"] == "football" and g["situation"] and int(t) // 3 % 2 == 1:
            return short_down(g["situation"]), (RED if g["redzone"] else YELLOW)
        if g["sport"] == "baseball" and g["outs"] is not None and int(t) // 3 % 2 == 1:
            return f"{g['balls'] or 0}-{g['strikes'] or 0}  {g['outs']} OUT", YELLOW
        if g["sport"] == "basketball" or g["sport"] == "hockey" or g["sport"] == "football":
            # "Q3 5:21" style is shorter than ESPN's "5:21 - 3rd Quarter"
            if g["clock"] and g["period"] and "half" not in detail.lower() and "end" not in detail.lower():
                prefix = {"football": "Q", "basketball": "Q", "hockey": "P"}[g["sport"]]
                if g["league"] in ("ncaam", "ncaaw"):
                    prefix = "H"
                per = g["period"]
                limit = 4 if prefix == "Q" else (3 if prefix == "P" else 2)
                label = f"{prefix}{per}" if per <= limit else ("OT" if per == limit + 1 else f"{per - limit}OT")
                return f"{label} {g['clock']}", YELLOW
        return detail.upper(), YELLOW


def team_color(team):
    """Pick whichever of the team's colors is most visible on black, then brighten it."""
    import colorsys
    def val(c):
        h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in c))
        return v * (0.5 + s / 2)
    base = team["color"]
    if val(base) < 0.35 and val(team["alt"]) > val(base) and team["alt"] not in ((255, 255, 255), (0, 0, 0)):
        base = team["alt"]
    return readable(base, min_value=0.9)


def short_down(text):
    """'2nd & 7 at KC 35' -> '2&7 KC35' so it fits between the logos."""
    import re
    m = re.match(r"\s*(\d)\w*\s*&\s*(\w+)(?:\s+at\s+(\w+)\s*(\d+))?", text or "")
    if not m:
        return text
    out = f"{m.group(1)}&{m.group(2).replace('Goal', 'G')}"
    if m.group(3):
        out += f" {m.group(3)}{m.group(4)}"
    return out


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0
