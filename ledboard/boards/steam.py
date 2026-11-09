"""Steam: what you're playing right now (Steam Web API).

Needs a free Web API key (https://steamcommunity.com/dev/apikey) and your Steam ID, profile
URL or custom URL name. Your Steam privacy setting 'Game details' must be Public for Steam
to report the game you're in.

Also: "friend now playing" (when a friend starts a game; needs your friends list to be public)
and "achievement unlocked" (with how rare it is) screens, shown for a few minutes.
"""
from __future__ import annotations

import logging
import re
import time

import requests

from .base import Board
from ..gfx import GRAY, WHITE, fetch_image

log = logging.getLogger(__name__)

API = "https://api.steampowered.com"
ICON_URL = "https://media.steampowered.com/steamcommunity/public/images/apps/{appid}/{hash}.jpg"
COVER_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900.jpg"
HEADER_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
STEAM_BLUE = (102, 192, 244)
INGAME_GREEN = (144, 186, 60)
STATES = {0: "OFFLINE", 1: "ONLINE", 2: "BUSY", 3: "AWAY", 4: "SNOOZE", 5: "LOOKING TO TRADE", 6: "LOOKING TO PLAY"}


def parse_id(text):
    """('id', steamid64) or ('vanity', name) from an ID, profile URL or custom URL name."""
    t = (text or "").strip().rstrip("/")
    m = re.search(r"steamcommunity\.com/profiles/(\d{17})", t)
    if m:
        return "id", m.group(1)
    m = re.search(r"steamcommunity\.com/id/([^/?#]+)", t)
    if m:
        return "vanity", m.group(1)
    if re.fullmatch(r"\d{17}", t):
        return "id", t
    return ("vanity", t) if t else (None, None)


def fmt_minutes(mins):
    mins = int(mins or 0)
    h, m = divmod(mins, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


class SteamBoard(Board):
    name = "steam"
    title = "Steam"
    description = ("What you're playing on Steam: game icon, name, how long you've been playing and total hours. "
                   "Skipped when you're not in a game (unless 'show when online' is on).")
    refresh = 60
    DEFAULTS = {"enabled": False, "duration": 12, "refresh": 60, "api_key": "", "steam_ids": [],
                "takeover": False, "show_online": False, "art": "icon",
                "show_friends": True, "friend_filter": [], "show_achievements": True, "event_minutes": 10}
    FIELDS = [
        {"key": "api_key", "label": "Steam Web API key", "type": "password",
         "help": "Free at steamcommunity.com/dev/apikey (any domain name works, e.g. 'ledboard')."},
        {"key": "steam_ids", "label": "Steam IDs / profile URLs (comma-separated)", "type": "list",
         "help": "Your profile URL (steamcommunity.com/id/yourname), custom URL name or 17-digit ID. "
                 "Add more for other people in the house. Steam privacy 'Game details' must be Public."},
        {"key": "art", "label": "Game art", "type": "select", "options": ["icon", "cover"],
         "option_labels": {"icon": "Game icon (made for small sizes)", "cover": "Cover art"}},
        {"key": "takeover", "label": "Take over the display while playing", "type": "bool"},
        {"key": "show_online", "label": "Also show when online but not in a game", "type": "bool"},
        {"key": "duration", "label": "Seconds on screen", "type": "number"},
        {"key": "refresh", "label": "Check every (seconds)", "type": "number"},
        {"key": "show_achievements", "label": "Achievement unlocked (with how rare it is)", "type": "bool",
         "section": "Friends & achievements"},
        {"key": "show_friends", "label": "Friend now playing (when a friend starts a game)", "type": "bool",
         "section": "Friends & achievements",
         "help": "Uses the first Steam ID above. Your friends list and their game details must be visible to Steam's API."},
        {"key": "friend_filter", "label": "Only these friends (names, comma-separated; blank = all)", "type": "list",
         "section": "Friends & achievements", "show_if": {"show_friends": True}},
        {"key": "event_minutes", "label": "Show these screens for (minutes)", "type": "number",
         "section": "Friends & achievements"},
    ]

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.players = {}         # steamid -> dict
        self._resolved = {}       # input text -> steamid64
        self._sessions = {}       # steamid -> (appid, first seen in-game, epoch)
        self._art = {}            # (appid, style) -> image
        self.events = []          # friend / achievement screens: dicts with key, kind, until
        self._friend_games = None  # friend steamid -> appid (None until the first poll)
        self._ach_seen = {}       # (steamid, appid) -> newest unlock time already known
        self._rarity = {}         # appid -> (fetched epoch, {apiname: percent})
        self.session = requests.Session()

    # -- data -----------------------------------------------------------------
    def _get(self, path, **params):
        params["key"] = self.cfg.get("api_key", "")
        r = self.session.get(f"{API}/{path}", params=params, timeout=12)
        if r.status_code in (401, 403):
            raise RuntimeError("Steam rejected the API key")
        r.raise_for_status()
        return r.json()

    def _resolve(self, text):
        if text in self._resolved:
            return self._resolved[text]
        kind, val = parse_id(text)
        if kind == "id":
            sid = val
        elif kind == "vanity":
            resp = self._get("ISteamUser/ResolveVanityURL/v1/", vanityurl=val).get("response", {})
            if resp.get("success") != 1:
                raise RuntimeError(f"Couldn't find a Steam profile called '{val}'")
            sid = resp["steamid"]
        else:
            return None
        self._resolved[text] = sid
        return sid

    def update(self):
        ids_in = [i for i in self.cfg.get("steam_ids", []) if i and i.strip()]
        if not (self.cfg.get("api_key") and ids_in):
            self.players = {}
            return
        ids = [s for s in (self._resolve(i) for i in ids_in) if s]
        summ = self._get("ISteamUser/GetPlayerSummaries/v2/", steamids=",".join(ids))
        players = {}
        now = time.time()
        for p in (summ.get("response") or {}).get("players") or []:
            sid = p.get("steamid")
            appid = p.get("gameid")
            info = {"steamid": sid, "name": p.get("personaname", ""), "state": p.get("personastate", 0),
                    "game": p.get("gameextrainfo") or "", "appid": appid, "avatar": p.get("avatarmedium"),
                    "private": p.get("communityvisibilitystate", 3) != 3}
            if appid:
                prev = self._sessions.get(sid)
                if not prev or prev[0] != appid:
                    self._sessions[sid] = (appid, now)
                info["since"] = self._sessions[sid][1]
                info.update(self._game_stats(sid, appid))
                info["art"] = self._game_art(appid, info.get("icon_hash"))
            else:
                self._sessions.pop(sid, None)
                if self.cfg.get("show_online") and info["state"] and info["avatar"]:
                    info["art"] = fetch_image(info["avatar"], self.app.height, self.app.height)
            players[sid] = info
        self.players = players
        self._expire_events()
        if self.cfg.get("show_achievements", True):
            for sid, p in players.items():
                if p["appid"]:
                    try:
                        self._check_achievements(sid, p)
                    except Exception as e:
                        log.debug("achievements unavailable for %s: %s", p["appid"], e)
        if self.cfg.get("show_friends", True) and ids:
            try:
                self._check_friends(ids[0], set(ids))
            except Exception as e:
                log.info("Steam friends unavailable: %s", e)

    # -- friends & achievements -------------------------------------------------
    def _event_seconds(self):
        try:
            return max(1.0, float(self.cfg.get("event_minutes") or 10)) * 60
        except (TypeError, ValueError):
            return 600.0

    def _expire_events(self):
        now = time.time()
        self.events = [e for e in self.events if e["until"] > now]

    def _add_event(self, ev):
        ev["until"] = time.time() + self._event_seconds()
        self.events = [e for e in self.events if e["key"] != ev["key"]] + [ev]

    def _check_friends(self, me, mine):
        fl = self._get("ISteamUser/GetFriendList/v1/", steamid=me, relationship="friend")
        friends = [f["steamid"] for f in (fl.get("friendslist") or {}).get("friends") or [] if f.get("steamid") not in mine]
        wanted = {n.strip().lower() for n in self.cfg.get("friend_filter") or [] if n.strip()}
        now_playing = {}
        for i in range(0, len(friends), 100):
            summ = self._get("ISteamUser/GetPlayerSummaries/v2/", steamids=",".join(friends[i:i + 100]))
            for p in (summ.get("response") or {}).get("players") or []:
                if wanted and (p.get("personaname") or "").lower() not in wanted:
                    continue
                now_playing[p["steamid"]] = (p.get("gameid"), p)
        first = self._friend_games is None
        prev = self._friend_games or {}
        for sid, (appid, p) in now_playing.items():
            if appid and prev.get(sid) != appid and not first:
                self._add_event({"key": f"friend:{sid}", "kind": "friend", "name": p.get("personaname", ""),
                                 "game": p.get("gameextrainfo") or "a game", "appid": appid,
                                 "avatar": fetch_image(p.get("avatarmedium"), 24, 24) if p.get("avatarmedium") else None})
            if not appid:
                self.events = [e for e in self.events if e["key"] != f"friend:{sid}"]   # they stopped playing
        self._friend_games = {sid: appid for sid, (appid, _p) in now_playing.items()}

    def _check_achievements(self, sid, p):
        appid = p["appid"]
        j = self._get("ISteamUserStats/GetPlayerAchievements/v1/", steamid=sid, appid=appid, l="english")
        stats = j.get("playerstats") or {}
        got = [a for a in stats.get("achievements") or [] if a.get("achieved")]
        newest = max((int(a.get("unlocktime") or 0) for a in got), default=0)
        key = (sid, appid)
        if key not in self._ach_seen:            # first look at this game: remember, don't announce old ones
            self._ach_seen[key] = newest
            return
        fresh = [a for a in got if int(a.get("unlocktime") or 0) > self._ach_seen[key]]
        self._ach_seen[key] = max(newest, self._ach_seen[key])
        if not fresh:
            return
        rarity = self._rarity_for(appid)
        for a in sorted(fresh, key=lambda a: int(a.get("unlocktime") or 0)):
            self._add_event({"key": f"ach:{sid}:{appid}:{a.get('apiname')}", "kind": "achievement",
                             "name": a.get("name") or a.get("apiname") or "Achievement",
                             "game": stats.get("gameName") or p.get("game") or "", "player": p.get("name", ""),
                             "percent": rarity.get(a.get("apiname"))})

    def _rarity_for(self, appid):
        cached = self._rarity.get(appid)
        if cached and time.time() - cached[0] < 3600:
            return cached[1]
        out = {}
        try:
            j = self._get("ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/", gameid=appid)
            for a in (j.get("achievementpercentages") or {}).get("achievements") or []:
                try:
                    out[a.get("name")] = float(a.get("percent"))
                except (TypeError, ValueError):
                    pass
        except Exception as e:
            log.debug("rarity unavailable: %s", e)
        self._rarity[appid] = (time.time(), out)
        return out

    def _game_stats(self, sid, appid):
        """Total hours and the icon hash from the player's recently played games."""
        try:
            games = self._get("IPlayerService/GetRecentlyPlayedGames/v1/", steamid=sid,
                              count=0).get("response", {}).get("games") or []
        except Exception as e:
            log.debug("recently played unavailable: %s", e)
            return {}
        for g in games:
            if str(g.get("appid")) == str(appid):
                return {"total_minutes": g.get("playtime_forever"), "icon_hash": g.get("img_icon_url")}
        return {}

    def _game_art(self, appid, icon_hash):
        style = self.cfg.get("art", "icon")
        key = (appid, style, icon_hash)
        if key in self._art:
            return self._art[key]
        H = self.app.height
        urls = []
        if style == "icon" and icon_hash:
            urls.append(ICON_URL.format(appid=appid, hash=icon_hash))
        urls += [COVER_URL.format(appid=appid), HEADER_URL.format(appid=appid)]
        img = None
        for u in urls:
            img = fetch_image(u, H, H)
            if img is not None:
                break
        self._art[key] = img
        return img

    # -- slides ---------------------------------------------------------------
    def _visible(self):
        out = []
        for sid, p in self.players.items():
            if p["appid"] or (self.cfg.get("show_online") and p["state"]):
                out.append(sid)
        return out

    def slides(self):
        now = time.time()
        ev = [e["key"] for e in self.events if e["until"] > now and
              (self.cfg.get("show_friends", True) if e["kind"] == "friend" else self.cfg.get("show_achievements", True))]
        return ev + self._visible()

    def takeover_slides(self):
        if not self.cfg.get("takeover"):
            return []
        return [sid for sid in self._visible() if self.players[sid]["appid"]]

    def _render_friend(self, c, e, t):
        W, H = c.width, c.height
        c.rect(0, 0, 1, H - 1, INGAME_GREEN)
        if e.get("avatar") is not None:
            c.paste(e["avatar"], 4, 4)
        else:
            self._steam_badge(c, 4, 4, 24)
        x0 = 33
        c.text(x0, 0, "FRIEND NOW PLAYING", "4x6", INGAME_GREEN)
        c.scroll_text(x0, W, 8, e["name"], "6x10", WHITE, t)
        c.scroll_text(x0, W, 20, e["game"], "5x7", STEAM_BLUE, t)

    def _render_achievement(self, c, e, t):
        import math
        W, H = c.width, c.height
        gold = (255, 200, 40)
        d = c.draw
        d.rectangle((8, 5, 19, 14), fill=gold)
        d.ellipse((8, 9, 19, 19), fill=gold)
        d.arc((3, 6, 11, 14), 90, 270, fill=gold)
        d.arc((16, 6, 24, 14), 270, 90, fill=gold)
        d.rectangle((12, 19, 15, 22), fill=gold)
        d.rectangle((9, 23, 18, 25), fill=gold)
        glow = 0.6 + 0.4 * math.sin(t * 4)
        for a in range(0, 360, 45):                                # sparkles circling the trophy
            ang = math.radians(a + t * 60)
            c.pixel(round(14 + 13 * math.cos(ang)), round(15 + 13 * math.sin(ang)),
                    tuple(round(v * glow) for v in gold))
        x0 = 32
        c.text(x0, 0, "ACHIEVEMENT UNLOCKED", "4x6", gold)
        c.scroll_text(x0, W, 8, e["name"], "6x10", WHITE, t)
        c.scroll_text(x0, W, 19, e["game"], "4x6", STEAM_BLUE, t)
        pct = e.get("percent")
        if pct is not None:
            tier, col = (("ULTRA RARE", (255, 90, 200)) if pct < 2 else ("RARE", (200, 120, 255)) if pct < 10
                         else ("UNCOMMON", (100, 180, 255)) if pct < 35 else ("COMMON", GRAY))
            c.text(x0, H - 6, f"{tier} · {pct:.1f}% OF PLAYERS", "4x6", col)
        elif len(self.players) > 1 and e.get("player"):
            c.text(x0, H - 6, e["player"], "4x6", GRAY)

    def render(self, c, slide, t):
        if isinstance(slide, str) and (slide.startswith("friend:") or slide.startswith("ach:")):
            e = next((x for x in self.events if x["key"] == slide), None)
            if e:
                (self._render_friend if e["kind"] == "friend" else self._render_achievement)(c, e, t)
            return
        p = self.players.get(slide)
        if not p:
            return
        H, W = c.height, c.width
        art = p.get("art")
        if art is not None:
            c.paste(art, 0, 0)
        else:
            self._steam_badge(c, 2, 2, H - 4)
        x0 = H + 3
        if p["appid"]:
            c.scroll_text(x0, W, 1, p["game"] or "In game", "6x10", WHITE, t)
            mins = (time.time() - p.get("since", time.time())) / 60
            c.scroll_text(x0, W, 12, f"PLAYING · {fmt_minutes(mins)}", "5x8", INGAME_GREEN, t)
            bits = [p["name"]]
            if p.get("total_minutes"):
                bits.append(f"{round(p['total_minutes'] / 60):,} hrs total")
            c.scroll_text(x0, W, H - 7, "  ·  ".join(bits), "4x6", GRAY, t)
        else:
            c.scroll_text(x0, W, 3, p["name"], "6x10", WHITE, t)
            c.text(x0, 15, STATES.get(p["state"], "ONLINE"), "5x8", STEAM_BLUE)
            c.text(x0, H - 7, "STEAM", "4x6", GRAY)

    @staticmethod
    def _steam_badge(c, x, y, s):
        """Simple Steam-style badge when no game art is available."""
        c.draw.ellipse((x, y, x + s - 1, y + s - 1), fill=(23, 26, 33), outline=STEAM_BLUE)
        r = s // 5
        cx, cy = x + s * 2 // 3, y + s // 3
        c.draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=WHITE)
        c.draw.ellipse((x + s // 4 - 2, y + s * 2 // 3 - 2, x + s // 4 + 2, y + s * 2 // 3 + 2), fill=WHITE)
        c.draw.line((x + s // 4, y + s * 2 // 3, cx - r, cy + r // 2), fill=WHITE)

    def status(self):
        s = super().status()
        s["players"] = [{k: p.get(k) for k in ("name", "game", "state", "private")} for p in self.players.values()]
        return s
