"""Steam: what you're playing right now (Steam Web API).

Needs a free Web API key (https://steamcommunity.com/dev/apikey) and your Steam ID, profile
URL or custom URL name. Your Steam privacy setting 'Game details' must be Public for Steam
to report the game you're in.

Also: "friend now playing" (when a friend starts a game; needs your friends list to be public)
and "achievement unlocked" (with how rare it is) screens, shown for a few minutes.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import requests

from .base import Board, organize
from PIL import Image, ImageEnhance

from ..gfx import GRAY, WHITE, fetch_image, trim_alpha
from .steam_layouts import HERO_LAYOUTS, LAYOUTS, RENDERERS, render_party

log = logging.getLogger(__name__)

API = "https://api.steampowered.com"
ICON_URL = "https://media.steampowered.com/steamcommunity/public/images/apps/{appid}/{hash}.jpg"
COVER_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900.jpg"
HEADER_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
# Since 2024 many games' store artwork lives at addresses with a hash in them, so the fixed addresses above
# 404 for those games. Steam's store API tells us the real ones.
ASSET_BASE = "https://shared.cloudflare.steamstatic.com/store_item_assets/"
STORE_ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
GRIDDB_URL = "https://www.steamgriddb.com/api/v2"
ART_RETRY = 1800          # seconds before trying again for a game whose art couldn't be downloaded
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
    category = "Media"
    description = ("What you're playing on Steam: game icon, name, how long you've been playing and total hours. "
                   "Skipped when you're not in a game (unless 'show when online' is on).")
    refresh = 60
    DEFAULTS = {"enabled": False, "duration": 12, "refresh": 60, "api_key": "", "steam_ids": [],
                "takeover": False, "show_online": False, "art": "icon", "hero": False, "layout": "classic",
                "griddb_key": "", "art_overrides": {},
                "show_friends": True, "friend_filter": [], "show_achievements": True, "event_minutes": 10}
    FIELDS = [
        {"key": "api_key", "label": "Steam Web API key", "type": "password",
         "help": "Free at steamcommunity.com/dev/apikey (any domain name works, e.g. 'ledboard')."},
        {"key": "steam_ids", "label": "Steam IDs / profile URLs (comma-separated)", "type": "list",
         "help": "Your profile URL (steamcommunity.com/id/yourname), custom URL name or 17-digit ID. "
                 "Add more for other people in the house. Steam privacy 'Game details' must be Public."},
        {"key": "layout", "label": "Layout", "type": "select", "options": list(LAYOUTS), "option_labels": LAYOUTS,
         "help": "Cinema and Banner use the game's hero banner (games without one fall back to Classic). Party "
                 "shows everyone in the house at once when two or more people are online."},
        {"key": "art", "label": "Game art", "type": "select", "options": ["icon", "cover"],
         "option_labels": {"icon": "Game icon (made for small sizes)", "cover": "Cover art"}},
        {"key": "hero", "label": "Game hero banner as the background", "type": "bool",
         "help": "The wide banner from the top of the game's Steam library page fills the board (dimmed), with "
                 "the game art above in front of it. Games without a hero keep the plain background. "
                 "(Classic, Session timer and Stats card layouts.)"},
        {"key": "griddb_key", "label": "SteamGridDB API key (optional)", "type": "password", "section": "Custom artwork",
         "help": "Free at steamgriddb.com: log in, then Preferences → API. Lets you pick community artwork for "
                 "your games below."},
        {"key": "art_overrides", "label": "Your games", "type": "steam_art", "section": "Custom artwork"},
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
    FIELDS = organize(FIELDS, [
        ("Basics", "Account", ["api_key", "steam_ids"]),
        ("Screens", "Now playing", ["layout", "takeover", "show_online", "art", "hero"]),
        ("Screens", "Friends & achievements", ["show_achievements", "show_friends", "friend_filter", "event_minutes"]),
        ("Look", "Custom artwork", ["griddb_key", "art_overrides"]),
        ("Advanced", "Timing", ["duration", "refresh"])], {"duration": "Seconds per screen"})

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.players = {}         # steamid -> dict
        self._resolved = {}       # input text -> steamid64
        self._sessions = {}       # steamid -> (appid, first seen in-game, epoch)
        self._art = {}            # (appid, style, icon hash) -> (image or None, when)
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
                self._remember_game(appid, info["game"])
                info.update(self._game_stats(sid, appid))
                info["art"] = self._game_art(appid, info.get("icon_hash"))
                want_hero = self.cfg.get("hero") or self._layout() in HERO_LAYOUTS
                hero = self._game_hero(appid) if want_hero else None
                info["hero"] = hero["dim"] if hero and self.cfg.get("hero") else None
                info["hero_full"] = hero["full"] if hero else None
                info["hero_banner"] = hero["banner"] if hero else None
            else:
                self._sessions.pop(sid, None)
                if self.cfg.get("show_online") and info["state"] and info["avatar"]:
                    info["art"] = fetch_image(info["avatar"], self.app.height, self.app.height)
            if self._layout() == "party" and info["avatar"]:
                info["avatar_small"] = fetch_image(info["avatar"], 9, 9)
            players[sid] = info
        self.players = players
        self._expire_events()
        if self.cfg.get("show_achievements", True) or self._layout() == "stats":
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
        if stats.get("achievements"):                   # progress for the Stats card layout
            p["achievements"] = (len(got), len(stats["achievements"]))
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

    # -- games played (for picking custom artwork) ----------------------------------
    def _games_file(self):
        cfg = getattr(self.app, "config", None)
        base = os.path.dirname(getattr(cfg, "path", "") or "") or os.environ.get("LEDBOARD_CACHE") or "."
        return os.path.join(base, "steam_games.json")

    def known_games(self):
        """{appid: {"name": str, "last": epoch}} for every game LEDBoard has seen anyone play."""
        if getattr(self, "_known", None) is None:
            try:
                with open(self._games_file()) as f:
                    self._known = {str(k): v for k, v in json.load(f).items() if isinstance(v, dict)}
            except (OSError, ValueError):
                self._known = {}
        return self._known

    def _remember_game(self, appid, name):
        games = self.known_games()
        appid, now = str(appid), time.time()
        g = games.get(appid)
        # write when a game is new, renamed, or about once an hour while playing (spares the SD card)
        if g and g.get("name") == name and now - g.get("last", 0) < 3600:
            return
        games[appid] = {"name": name or (g or {}).get("name") or f"App {appid}", "last": now}
        try:
            path = self._games_file()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump(games, f, indent=1)
            os.replace(path + ".tmp", path)
        except OSError as e:
            log.info("couldn't save the Steam games list: %s", e)

    def griddb(self, appid, kind):
        """Artwork choices from SteamGridDB for a game: [{"id", "url", "thumb", "width", "height", "style"}]."""
        key = (self.cfg.get("griddb_key") or "").strip()
        if not key:
            raise RuntimeError("Add your SteamGridDB API key first (Steam → Custom artwork)")
        path, params = {"cover": ("grids", {"dimensions": "600x900,342x482,660x930"}),
                        "hero": ("heroes", {}), "icon": ("icons", {})}[kind]
        r = requests.get(f"{GRIDDB_URL}/{path}/steam/{int(appid)}", headers={"Authorization": f"Bearer {key}"},
                         params=dict(params, nsfw="false"), timeout=15)
        if r.status_code == 401:
            raise RuntimeError("SteamGridDB didn't accept the API key")
        if r.status_code == 404:
            return []
        r.raise_for_status()
        out = []
        for d in (r.json().get("data") or [])[:60]:
            if isinstance(d, dict) and d.get("url"):
                out.append({k: d.get(k) for k in ("id", "url", "thumb", "width", "height", "style")})
        return out

    def _override(self, appid, kind):
        o = (self.cfg.get("art_overrides") or {}).get(str(appid)) or {}
        u = o.get(kind)
        return u if isinstance(u, str) and u.startswith("http") else None

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
                return {"total_minutes": g.get("playtime_forever"), "recent_minutes": g.get("playtime_2weeks"),
                        "icon_hash": g.get("img_icon_url")}
        return {}

    def _store_assets(self, appid):
        """The game's current store artwork addresses from Steam: {'cover': url, 'header': url} (cached)."""
        cache = self.__dict__.setdefault("_assets", {})
        hit = cache.get(appid)
        if hit and (hit[0] or time.time() - hit[1] < ART_RETRY):
            return hit[0]
        out = {}
        try:
            params = {"input_json": json.dumps({"ids": [{"appid": int(appid)}],
                                                "context": {"language": "english", "country_code": "US"},
                                                "data_request": {"include_assets": True}})}
            r = requests.get(STORE_ITEMS_URL, params=params, timeout=10)
            r.raise_for_status()
            items = (r.json().get("response") or {}).get("store_items") or [{}]
            assets = items[0].get("assets") or {}
            fmt = assets.get("asset_url_format")              # e.g. "steam/apps/570/${FILENAME}?t=1700000000"
            if fmt:
                for kind, names in (("cover", ("library_capsule_2x", "library_capsule")),
                                    ("hero", ("library_hero", "library_hero_2x")),
                                    ("header", ("header", "main_capsule", "small_capsule"))):
                    name = next((assets[n] for n in names if assets.get(n)), None)
                    if name:
                        out[kind] = ASSET_BASE + fmt.replace("${FILENAME}", name)
        except Exception as e:
            log.info("Steam store artwork lookup failed for %s: %s", appid, e)
        if "header" not in out:
            try:
                r = requests.get(APPDETAILS_URL, params={"appids": appid, "filters": "basic"}, timeout=10)
                r.raise_for_status()
                data = (r.json().get(str(appid)) or {}).get("data") or {}
                if data.get("header_image"):
                    out["header"] = data["header_image"]
            except Exception as e:
                log.info("Steam app details lookup failed for %s: %s", appid, e)
        cache[appid] = (out, time.time())
        return out

    def _game_hero(self, appid):
        """The game's library hero banner, prepared for the board (cached): {'dim': background for text,
        'full': brighter (Cinema), 'banner': fitted to the height with a fade on its left (Banner)}, or None."""
        cache = self.__dict__.setdefault("_heroes", {})
        custom = self._override(appid, "hero")
        hit = cache.get((appid, custom))
        if hit and (hit[0] is not None or time.time() - hit[1] < ART_RETRY):
            return hit[0]
        W, H = self.app.width, self.app.height
        assets = self._store_assets(appid)
        urls = [custom, assets.get("hero"), ASSET_BASE + f"steam/apps/{appid}/library_hero.jpg",
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_hero.jpg"]
        img = None
        for u in dict.fromkeys(u for u in urls if u):
            src = fetch_image(u, W * 2, W * 2, trim=False)
            if src is None:
                continue
            src = trim_alpha(src)                       # fetch_image pads to a square
            if src.width < 8 or src.height < 4:
                continue
            src = src.convert("RGB")
            # fill the width, then keep the middle band (heroes are about 3:1, the board is 4:1)
            h = max(H, round(src.height * W / src.width))
            band = src.resize((W, h), Image.LANCZOS)
            top = (h - H) // 2
            band = band.crop((0, top, W, top + H))
            # banner: the whole hero at the board's height, fading into black on its left
            bw = max(1, min(W, round(src.width * H / src.height)))
            banner = ImageEnhance.Brightness(src.resize((bw, H), Image.LANCZOS)).enhance(0.8).convert("RGBA")
            fade = max(1, bw * 2 // 3)                  # a long fade, so the text on the left stays readable
            mask = Image.new("L", (bw, H), 255)
            for x in range(fade):
                mask.paste(round(255 * (x / fade) ** 1.8), (x, 0, x + 1, H))
            banner.putalpha(mask)
            img = {"dim": ImageEnhance.Brightness(band).enhance(0.38).convert("RGBA"),
                   "full": ImageEnhance.Brightness(band).enhance(0.85).convert("RGBA"),
                   "banner": banner}
            break
        cache[(appid, custom)] = (img, time.time())
        return img

    def _game_art(self, appid, icon_hash):
        """Art for a game: its cover (portrait) or icon, falling back step by step; the Steam badge is drawn
        only when nothing at all can be downloaded (tried again after a while)."""
        style = self.cfg.get("art", "icon")
        custom = self._override(appid, style)
        key = (appid, style, icon_hash, custom)
        hit = self._art.get(key)
        if hit and (hit[0] is not None or time.time() - hit[1] < ART_RETRY):
            return hit[0]
        H = self.app.height
        icon = ICON_URL.format(appid=appid, hash=icon_hash) if icon_hash else None
        old = [COVER_URL.format(appid=appid), ASSET_BASE + f"steam/apps/{appid}/library_600x900.jpg"]
        if style == "cover":
            assets = self._store_assets(appid)
            urls = [assets.get("cover")] + old + [assets.get("header"), HEADER_URL.format(appid=appid), icon]
        else:
            urls = [icon] + old + [HEADER_URL.format(appid=appid)]
        if custom:
            urls.insert(0, custom)                      # your pick from SteamGridDB comes first
        img = None
        for u in dict.fromkeys(u for u in urls if u):
            img = fetch_image(u, H, H)
            if img is not None:
                break
        if img is None:
            log.info("no artwork could be downloaded for Steam app %s; showing the Steam badge", appid)
        self._art[key] = (img, time.time())
        return img

    # -- slides ---------------------------------------------------------------
    def _visible(self):
        out = []
        for sid, p in self.players.items():
            if p["appid"] or (self.cfg.get("show_online") and p["state"]):
                out.append(sid)
        return out

    def _layout(self):
        lay = self.cfg.get("layout", "classic")
        return lay if lay in LAYOUTS else "classic"

    def _party_pages(self):
        vis = self._visible()
        if self._layout() != "party" or len(vis) < 2:
            return None
        return [f"party:{i}" for i in range(0, len(vis), 3)]

    def slides(self):
        now = time.time()
        ev = [e["key"] for e in self.events if e["until"] > now and
              (self.cfg.get("show_friends", True) if e["kind"] == "friend" else self.cfg.get("show_achievements", True))]
        return ev + (self._party_pages() or self._visible())

    def takeover_slides(self):
        if not self.cfg.get("takeover"):
            return []
        playing = [sid for sid in self._visible() if self.players[sid]["appid"]]
        if not playing:
            return []
        return self._party_pages() or playing

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
        if isinstance(slide, str) and slide.startswith("party:"):
            vis = [self.players[s] for s in self._visible()]
            vis.sort(key=lambda p: (not p["appid"], p["name"].lower()))
            i = int(slide.split(":")[1])
            render_party(self, c, vis[i:i + 3], t)
            return
        p = self.players.get(slide)
        if not p:
            return
        fn = RENDERERS.get(self._layout()) if p["appid"] else None
        if fn:
            fn(self, c, p, t)
        else:
            self._render_classic(c, p, t)

    def _render_classic(self, c, p, t):
        H, W = c.height, c.width
        hero = p.get("hero") if p["appid"] else None
        if hero is not None:
            c.paste(hero, 0, 0)
        art = p.get("art")
        if art is not None:
            if hero is not None:                        # a dark edge so the art stands out from the banner
                bb = art.getbbox() or (0, 0, art.width, art.height)
                c.rect(0, 0, min(W - 1, bb[2]), H - 1, (0, 0, 0))
            c.paste(art, 0, 0)
        else:
            self._steam_badge(c, 2, 2, H - 4)
        x0 = H + 3
        # over the hero, text gets a dark shadow so it reads on a busy picture
        text = self._shadow_scroll if hero is not None else (lambda c_, *a, **k: c_.scroll_text(*a, **k))
        if p["appid"]:
            text(c, x0, W, 1, p["game"] or "In game", "6x10", WHITE, t)
            mins = (time.time() - p.get("since", time.time())) / 60
            text(c, x0, W, 12, f"PLAYING · {fmt_minutes(mins)}", "5x8", INGAME_GREEN, t)
            bits = [p["name"]]
            if p.get("total_minutes"):
                bits.append(f"{round(p['total_minutes'] / 60):,} hrs total")
            text(c, x0, W, H - 7, "  ·  ".join(bits), "4x6", GRAY if hero is None else (215, 215, 215), t)
        else:
            c.scroll_text(x0, W, 3, p["name"], "6x10", WHITE, t)
            c.text(x0, 15, STATES.get(p["state"], "ONLINE"), "5x8", STEAM_BLUE)
            c.text(x0, H - 7, "STEAM", "4x6", GRAY)

    @staticmethod
    def _shadow_scroll(c, x0, x1, y, text, font, color, t, **kw):
        """scroll_text with a 1-pixel dark outline all around (for text over a picture)."""
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1), (-1, 1), (1, -1), (-1, -1)):
            c.scroll_text(x0 + dx, x1 + dx, y + dy, text, font, (0, 0, 0), t, **kw)
        c.scroll_text(x0, x1, y, text, font, color, t, **kw)

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
