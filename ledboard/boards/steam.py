"""Steam: what you're playing right now (Steam Web API).

Needs a free Web API key (https://steamcommunity.com/dev/apikey) and your Steam ID, profile
URL or custom URL name. Your Steam privacy setting 'Game details' must be Public for Steam
to report the game you're in.
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
                "takeover": False, "show_online": False, "art": "icon"}
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
    ]

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.players = {}         # steamid -> dict
        self._resolved = {}       # input text -> steamid64
        self._sessions = {}       # steamid -> (appid, first seen in-game, epoch)
        self._art = {}            # (appid, style) -> image
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
        return self._visible()

    def takeover_slides(self):
        if not self.cfg.get("takeover"):
            return []
        return [sid for sid in self._visible() if self.players[sid]["appid"]]

    def render(self, c, slide, t):
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
