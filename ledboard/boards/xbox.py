"""Xbox: what you're playing, your profile and your stats, from Home Assistant's Xbox integration.

You enter your gamertag; the board finds every Home Assistant entity the Xbox integration made for it
(in game, online, status / now playing, gamerscore, gamerpic, account tier...) and follows them live.
Game artwork comes from the integration when it has a picture, otherwise from SteamGridDB through the
Steam board (its key and your artwork picks), where Xbox games are listed alongside Steam games.
"""
from __future__ import annotations

import logging
import math
import re
import time
from datetime import datetime, timezone

import requests

from .base import Board, organize
from ..gfx import GRAY, WHITE, fetch_image, fill_height, fit_image, scale, trim_dark

log = logging.getLogger(__name__)

XBOX_GREEN = (60, 200, 60)
GOLD = (255, 190, 40)
BLUE = (80, 160, 255)
OFF = {"", "unknown", "unavailable", "none", "off", "false"}
SHOW = {"online": "While you're online or playing", "playing": "Only while you're playing", "always": "Always"}
ART = {"icon": "Game icon (made for small sizes)", "cover": "Cover art",
       "xbox": "Xbox's own game picture (from Home Assistant; cover art when there's none)",
       "gamerpic": "Your gamerpic"}
LAYOUTS = {
    "classic": "Classic (game art, name, session time, gamertag and gamerscore)",
    "cinema": "Cinema (the game's hero banner, bright, with a slim bar for the name and time)",
    "banner": "Banner (hero on the right fading into black, text on the left)",
    "session": "Session timer (a big play-time clock with the game art)",
    "stats": "Stats card (the game, your gamerscore, account tier and followers)",
    "friends": "Friends (you and your Xbox friends who are online, one per row)",
}
HERO_LAYOUTS = {"cinema", "banner"}
STATUS_WORDS = {"online", "offline", "away", "busy", "in game", "appear offline", "playing"}
NOT_STATS = {"status", "now_playing", "game", "title", "in_game", "online", "gamerpic", "avatar", "picture"}


def slugify(text):
    """Home Assistant's entity id style: 'IxSOFAxKing' -> 'ixsofaxking', 'Big Gamer 99' -> 'big_gamer_99'."""
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s


# Xbox screens and apps that aren't games (the dashboard shows up as 'Home')
NOT_GAMES = {"home", "xbox", "xbox app", "xbox dashboard", "dashboard", "microsoft store", "store", "settings",
             "guide", "xbox guide", "game pass", "xbox game pass", "my games & apps", "my games and apps"}
_PRESENCE = re.compile(r"\s+[-–—|]\s+")


def split_presence(text):
    """'Grand Theft Auto VI - Exploring Port Gellhorn' -> ('Grand Theft Auto VI', 'Exploring Port Gellhorn').
    Xbox adds Rich Presence (what you're doing in the game) after the game's name."""
    t = re.sub(r"(?i)^\s*(now\s+)?playing\s*:?\s*", "", str(text or "")).strip()
    parts = _PRESENCE.split(t, maxsplit=1)
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def clean_game(text):
    """'ARC Raiders' from 'Playing ARC Raiders' or 'ARC Raiders - In a match'; None for a plain status like
    'Online' or the Xbox Home screen."""
    t, _activity = split_presence(text)
    low = t.lower()
    if not t or low in STATUS_WORDS or low in OFF or low in NOT_GAMES or low.startswith("last seen"):
        return None
    return t


def is_on(st):
    return bool(st) and str(st.get("state", "")).lower() in ("on", "true", "home", "playing")


class XboxBoard(Board):
    name = "xbox"
    title = "Xbox"
    category = "Media"
    description = ("What you're playing on Xbox (console or PC Game Pass), your profile and stats, from Home "
                   "Assistant's Xbox integration. Just enter your gamertag.")
    refresh = 60
    DEFAULTS = {"enabled": False, "duration": 10, "refresh": 60, "gamertag": "", "show": "online",
                "takeover": False, "art": "xbox", "show_profile": True, "show_stats": True,
                "layout": "classic", "hero": False, "show_gamerscore": True,
                "show_friends": True, "friend_filter": [], "show_achievements": True, "event_minutes": 10}
    FIELDS = [
        {"key": "gamertag", "label": "Your gamertag", "type": "text", "placeholder": "e.g. IxSOFAxKing"},
        {"key": "discover", "label": "Find my gamertag", "type": "xbox_discover",
         "help": "Needs Home Assistant's Xbox integration (Settings → Devices & services → Add integration → "
                 "Xbox) and the Home Assistant board's URL and token."},
        {"key": "layout", "label": "Layout", "type": "select", "options": list(LAYOUTS), "option_labels": LAYOUTS,
         "help": "Cinema and Banner use the game's hero banner from SteamGridDB (Steam board's key); games without one "
                 "use Classic. Friends shows everyone online when at least one friend is, otherwise Classic."},
        {"key": "show_gamerscore", "label": "Show gamerscore on the Now playing and Profile screens", "type": "bool"},
        {"key": "hero", "label": "Game hero banner as the background", "type": "bool",
         "help": "Classic, Session timer and Stats card: the game's wide hero banner fills the board, dimmed."},
        {"key": "show", "label": "Show the board", "type": "select", "options": list(SHOW), "option_labels": SHOW},
        {"key": "takeover", "label": "Take over the display while playing", "type": "bool"},
        {"key": "show_profile", "label": "Profile screen when online but not playing", "type": "bool"},
        {"key": "show_stats", "label": "Stats screen (gamerscore, account tier, followers...)", "type": "bool"},
        {"key": "art", "label": "Game art", "type": "select", "options": list(ART), "option_labels": ART,
         "help": "Icon and cover art come from SteamGridDB using the Steam board's key. Xbox games show up in the "
                 "Steam board's Your games list (Custom artwork), so you can rename them or pick their artwork there."},
        {"key": "show_achievements", "label": "Achievement unlocked (when your gamerscore goes up)", "type": "bool",
         "help": "Home Assistant's Xbox integration doesn't list achievements, so this shows the game and the "
                 "gamerscore you just earned."},
        {"key": "show_friends", "label": "Friend now playing (when a friend starts a game)", "type": "bool",
         "help": "Friends that Home Assistant's Xbox integration tracks (checked every refresh)."},
        {"key": "friend_filter", "label": "Only these friends (gamertags, comma-separated; blank = all)", "type": "list",
         "show_if": {"show_friends": True}},
        {"key": "event_minutes", "label": "Show these screens for (minutes)", "type": "number"},
        {"key": "duration", "label": "Seconds per screen", "type": "number"},
        {"key": "refresh", "label": "Check every (seconds)", "type": "number",
         "help": "Changes arrive live from Home Assistant; this is a backup."},
    ]
    FIELDS = organize(FIELDS, [
        ("Basics", "Account", ["gamertag", "discover"]),
        ("Screens", "Screens", ["layout", "show", "takeover", "show_profile", "show_stats"]),
        ("Screens", "Friends & achievements", ["show_achievements", "show_friends", "friend_filter", "event_minutes"]),
        ("Look", "Pictures", ["art", "hero", "show_gamerscore"]),
        ("Advanced", "Timing", ["duration", "refresh"]),
    ])

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.states = {}          # entity_id -> HA state (every entity for the gamertag)
        self.roles = {}           # role ('in_game', 'status', 'gamerscore'...) -> entity_id
        self._ws = None
        self._pics = {}           # (url, size) -> image (or None)
        self.events = []          # friend / achievement screens: dicts with key, kind, until
        self._friend_games = None  # friend gamertag -> game (None until the first look)
        self._last_score = None

    # -- Home Assistant -------------------------------------------------------------
    def _ha(self):
        ha = self.app.boards.get("homeassistant")
        hcfg = ha.cfg if ha else {}
        return (hcfg.get("url") or "").rstrip("/"), hcfg.get("token"), bool(hcfg.get("verify_ssl", True))

    def _all_states(self):
        base, token, verify = self._ha()
        if not (base and token):
            raise RuntimeError("Set the Home Assistant URL and token on the Home Assistant board")
        r = requests.get(f"{base}/api/states", headers={"Authorization": f"Bearer {token}"}, timeout=15, verify=verify)
        r.raise_for_status()
        return [s for s in r.json() if isinstance(s, dict) and s.get("entity_id")]

    @staticmethod
    def _belongs(st, slug, tag):
        """Is this entity one of the gamertag's? (entity id starts with the gamertag, or its name does)."""
        obj = st["entity_id"].split(".", 1)[1]
        name = str((st.get("attributes") or {}).get("friendly_name") or "")
        return obj == slug or obj.startswith(slug + "_") or (tag and name.lower().startswith(tag.lower() + " "))

    def _suffix(self, st, slug):
        obj = st["entity_id"].split(".", 1)[1]
        return obj[len(slug) + 1:] if obj.startswith(slug + "_") else obj

    def _classify(self, states, slug=None):
        """Work out what each of a gamertag's entities is: {role: entity_id}."""
        slug = slug or slugify(self.cfg.get("gamertag"))
        roles = {}
        for st in states:
            eid, dom = st["entity_id"], st["entity_id"].split(".")[0]
            suf = self._suffix(st, slug)
            if dom == "binary_sensor":
                for key in ("in_game", "online", "in_party", "in_multiplayer"):
                    if suf == key or suf.endswith("_" + key):
                        roles.setdefault(key, eid)
            elif dom == "image" and any(k in suf for k in ("gamerpic", "avatar", "picture")):
                roles.setdefault("gamerpic_image", eid)
            elif dom == "image" and any(k in suf for k in ("now_playing", "game", "title")):
                roles.setdefault("game_image", eid)
            elif dom == "sensor":
                if any(k in suf for k in ("now_playing", "current_game", "game_title")) or suf in ("game", "title"):
                    roles.setdefault("now_playing", eid)
                elif "status" in suf or "presence" in suf:
                    roles.setdefault("status", eid)
                elif "gamerscore" in suf:
                    roles.setdefault("gamerscore", eid)
        return roles

    def update(self):
        tag = (self.cfg.get("gamertag") or "").strip()
        if not tag:
            self.states, self.roles = {}, {}
            return
        slug = slugify(tag)
        everything = self._all_states()
        mine = [s for s in everything if self._belongs(s, slug, tag)]
        if not mine:
            self.states, self.roles = {}, {}
            raise RuntimeError(f"No Home Assistant entities for the gamertag {tag!r}. Is the Xbox integration set up?")
        self.states = {s["entity_id"]: s for s in mine}
        self.roles = self._classify(mine)
        base, token, verify = self._ha()
        self._ensure_live(base, token, verify, sorted(self.states))
        if not getattr(self, "_tidied", False):
            steam = self.app.boards.get("steam")
            if steam and hasattr(steam, "known_games"):
                self._tidy_games(steam)
            self._tidied = True
        self._track_game()
        self._check_gamerscore()
        self._load_pictures()
        self._load_friends(everything, tag)
        self._expire_events()

    def _ensure_live(self, base, token, verify, ents):
        from ..ha_ws import HAStateStream
        sig = (base, token, verify, tuple(ents))
        if self._ws and (self._ws.sig != sig or not self._ws.is_alive()):
            self._ws.stop()
            self._ws = None
        if self._ws is None:
            self._ws = HAStateStream(base, token, verify, ents, self._on_live,
                                     keep_running=lambda: self.enabled and bool(self.cfg.get("gamertag")),
                                     name="ha-live-xbox")
            self._ws.sig = sig
            self._ws.start()

    def _on_live(self, ent, st):
        self.states[ent] = st
        self._track_game()
        self._check_gamerscore()
        self.poke()                                       # pictures for a new game load in the update thread

    # -- what's going on --------------------------------------------------------------
    def _st(self, role):
        eid = self.roles.get(role)
        return self.states.get(eid) if eid else None

    def game(self):
        """The game being played, 'In game' when the name isn't known, or None."""
        return game_of(self._st)

    def online(self):
        on = self._st("online")
        if on is not None:
            return is_on(on) or self.game() is not None
        status = self._st("status")
        return self.game() is not None or (status is not None and str(status.get("state", "")).lower() not in
                                           ("offline", "unavailable", "unknown", ""))

    def _track_game(self):
        """When the current game started (for the session timer), and remember the game for artwork picks."""
        g = self.game()
        if g != getattr(self, "_game", None):
            self._game = g
            started = time.time()
            in_game = self._st("in_game")
            if g and in_game and in_game.get("last_changed"):
                try:
                    started = datetime.fromisoformat(in_game["last_changed"].replace("Z", "+00:00")).timestamp()
                except ValueError:
                    pass
            self._since = started
            if g:
                log.info("Xbox: playing %r", g)
                steam = self.app.boards.get("steam")
                if g != "In game" and steam and hasattr(steam, "_remember_game"):
                    steam._remember_game("ns:" + g, g)    # listed with the Steam board's games for artwork picks
                    self._tidy_games(steam)

    @staticmethod
    def _tidy_games(steam):
        """Forget entries older versions saved for the Xbox Home screen or 'Game - what you're doing' (Rich
        Presence), unless you picked artwork or a name for them."""
        try:
            games = steam.known_games()
        except Exception:
            return
        picked = steam.cfg.get("art_overrides") or {}
        for key in list(games):
            if not key.startswith("ns:") or picked.get(key):
                continue
            name = key[3:]
            base, act = split_presence(name)
            if name.lower() in NOT_GAMES or (act and ("ns:" + base) in games):
                steam.forget_game(key)
                log.info("Xbox: removed %r from the games list (not a separate game)", name)

    # -- pictures ------------------------------------------------------------------------
    def _picture_url(self, st):
        pic = ((st or {}).get("attributes") or {}).get("entity_picture")
        if not pic:
            return None
        base, _t, _v = self._ha()
        return pic if pic.startswith("http") else base + pic

    def _fetch(self, url, size):
        if not url:
            return None
        slot = (url, size)
        if slot in self._pics and (self._pics[slot] is not None or time.time() < self.__dict__.get("_retry", {}).get(slot, 0)):
            return self._pics[slot]
        base, token, verify = self._ha()
        headers = {"Authorization": f"Bearer {token}"} if base and url.startswith(base) else None
        key = "xbox|" + url.split("token=")[0] + "|" + (url.split("cache=")[-1] if "cache=" in url else "")
        img = fetch_image(url, 256, 256, headers=headers, verify=verify, cache_key=key, trim=True)
        if img is not None:
            img = trim_dark(img)                         # no dark frame / padding around the picture
            if img.width > img.height:                   # wide pictures fill the square (crop the sides)
                img = fill_height(img, size, size)
            if max(img.width, img.height) < size:     # small pictures grow to fill the square too
                k = size / max(img.width, img.height)
                img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))))
            img = fit_image(img, size, size)
        self._pics[slot] = img
        if img is None:
            self.__dict__.setdefault("_retry", {})[slot] = time.time() + 1800
        return img

    def _load_pictures(self):
        H = self.app.height
        # gamerpic: an image entity, else the picture most of the gamertag's entities carry
        gp = self._picture_url(self._st("gamerpic_image"))
        if not gp:
            for st in self.states.values():
                gp = self._picture_url(st)
                if gp and st["entity_id"] != self.roles.get("now_playing"):
                    break
        self.gamerpic = self._fetch(gp, H) if gp else None
        self.game_art = None
        self.hero = None
        g = self.game()
        if not g or g == "In game":
            return
        steam = self.app.boards.get("steam")
        if steam and hasattr(steam, "_game_hero") and (self.cfg.get("hero") or self.cfg.get("layout") in HERO_LAYOUTS):
            try:
                self.hero = steam._game_hero("ns:" + g)   # {'dim', 'full', 'banner'} from SteamGridDB / your pick
            except Exception as e:
                log.info("Xbox hero banner unavailable: %s", e)
        style = self._art_style()
        if style == "gamerpic":
            return
        ha_url = self._picture_url(self._st("game_image")) or self._picture_url(self._st("now_playing"))
        ha_url = ha_url if ha_url and ha_url != gp else None
        if style == "xbox" and ha_url:
            self.game_art = self._fetch(ha_url, H)
        if self.game_art is None and steam and hasattr(steam, "_override"):
            appid = "ns:" + g
            kind = "icon" if style == "icon" else "cover"
            other = "cover" if kind == "icon" else "icon"
            for pick_kind in (kind, other):              # your pick, else SteamGridDB's top match
                pick = steam._override(appid, pick_kind)
                if not pick:
                    try:
                        pick = steam._griddb_auto(appid, pick_kind)
                    except Exception as e:
                        log.info("Xbox artwork from SteamGridDB unavailable: %s", e)
                if pick:
                    self.game_art = self._fetch(pick, H)
                    if self.game_art is not None:
                        break
        if self.game_art is None and ha_url:
            self.game_art = self._fetch(ha_url, H)

    def _art_style(self):
        style = self.cfg.get("art") or "xbox"
        return "xbox" if style == "game" else style      # 'game' was the old name for Xbox's own picture

    def _load_friends(self, everything, me):
        """Every gamertag Home Assistant has (you and your friends): name, game / online, small gamerpic."""
        want_rows = self.cfg.get("layout") == "friends"
        if not want_rows and not self.cfg.get("show_friends", True):
            self.friends, self._friend_games = [], None
            return
        tags = {}
        for st in everything:
            eid = st["entity_id"]
            name = str((st.get("attributes") or {}).get("friendly_name") or "")
            for suffix, word in (("_in_game", "in game"), ("_gamerscore", "gamerscore"), ("_online", "online")):
                if eid.endswith(suffix) and name.lower().endswith(" " + word):
                    tag = name[: -len(word) - 1].strip()
                    if tag:
                        tags.setdefault(tag, eid[len(eid.split(".")[0]) + 1: -len(suffix)])
        friends = []
        for tag, slug in tags.items():
            states = [s for s in everything if self._belongs(s, slug, tag)]
            by_id = {s["entity_id"]: s for s in states}
            roles = self._classify(states, slug)
            g = game_of(lambda role: by_id.get(roles.get(role)) if roles.get(role) else None)
            on = by_id.get(roles.get("online")) if roles.get("online") else None
            online = g is not None or is_on(on)
            pic = next((self._picture_url(x) for x in states if self._picture_url(x)), None)
            friends.append({"tag": tag, "game": g, "online": online, "me": tag.lower() == me.lower(),
                            "pic": self._fetch(pic, 9) if pic and online and want_rows else None, "pic_url": pic})
        self._check_friends(friends)
        friends = [f for f in friends if f["online"]] if want_rows else []
        friends.sort(key=lambda f: (not f["me"], f["game"] is None, f["tag"].lower()))
        self.friends = friends

    # -- friends & achievements screens --------------------------------------------------
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

    def _check_friends(self, everyone):
        """A 'friend now playing' screen when a friend starts (or switches) a game."""
        if not self.cfg.get("show_friends", True):
            self._friend_games = None
            return
        wanted = {n.strip().lower() for n in self.cfg.get("friend_filter") or [] if n.strip()}
        now = {f["tag"]: f for f in everyone if not f["me"] and (not wanted or f["tag"].lower() in wanted)}
        first, prev = self._friend_games is None, self._friend_games or {}
        for tag, f in now.items():
            g = f["game"]
            if g and prev.get(tag) != g and not first:
                self._add_event({"key": f"friend:{slugify(tag)}", "kind": "friend", "name": tag, "game": g,
                                 "avatar": self._fetch(f["pic_url"], 24) if f["pic_url"] else None})
            if not g:
                self.events = [e for e in self.events if e["key"] != f"friend:{slugify(tag)}"]
        self._friend_games = {tag: f["game"] for tag, f in now.items()}

    def _check_gamerscore(self):
        """An 'achievement unlocked' screen when your gamerscore goes up."""
        st = self._st("gamerscore")
        try:
            score = float(st.get("state")) if st else None
        except (TypeError, ValueError):
            score = None
        if score is None:
            return
        last, self._last_score = self._last_score, score
        if last is None or score <= last or not self.cfg.get("show_achievements", True):
            return
        g = self.game()
        self._add_event({"key": f"ach:{int(score)}", "kind": "ach", "gained": int(score - last), "score": int(score),
                         "game": g if g and g != "In game" else ""})

    # -- stats ---------------------------------------------------------------------------
    def stats(self):
        """[(label, value)] for every other sensor the integration made for the gamertag."""
        slug = slugify(self.cfg.get("gamertag"))
        tag = (self.cfg.get("gamertag") or "").strip()
        used = set(self.roles.values())
        out = []
        for eid, st in sorted(self.states.items()):
            if not eid.startswith("sensor.") or eid in used and eid != self.roles.get("gamerscore"):
                continue
            suf = self._suffix(st, slug)
            if suf in NOT_STATS:
                continue
            attrs = st.get("attributes") or {}
            label = str(attrs.get("friendly_name") or suf.replace("_", " "))
            if tag and label.lower().startswith(tag.lower()):
                label = label[len(tag):].strip(" -:")
            val = str(st.get("state", ""))
            if val.lower() in OFF:
                continue
            if attrs.get("device_class") == "timestamp" or re.match(r"\d{4}-\d\d-\d\dT", val):
                val = _ago(val)
            else:
                try:
                    num = float(val)
                    val = f"{num:,.0f}" if num == int(num) else f"{num:,.1f}"
                except ValueError:
                    pass
                unit = attrs.get("unit_of_measurement")
                if unit and unit not in ("G", "points"):
                    val += f" {unit}"
            out.append((label.upper() or suf.upper(), val.upper()))
        return out

    def gamertags(self):
        """Gamertags Home Assistant's Xbox integration has entities for (for the 'Find my gamertag' button)."""
        found = {}
        for st in self._all_states():
            eid = st["entity_id"]
            name = str((st.get("attributes") or {}).get("friendly_name") or "")
            for suffix, word in (("_in_game", "in game"), ("_gamerscore", "gamerscore")):
                if eid.endswith(suffix) and name.lower().endswith(" " + word):
                    tag = name[: -len(word) - 1].strip()
                    if tag:
                        found[tag] = found.get(tag, 0) + 1
        return sorted(found, key=lambda k: -found[k])

    # -- slides ----------------------------------------------------------------------------
    def slides(self):
        if not self.states:
            return []
        show = self.cfg.get("show", "online")
        playing, online = self.game() is not None, self.online()
        now = time.time()
        ev = [e["key"] for e in self.events if e["until"] > now and
              self.cfg.get("show_friends" if e["kind"] == "friend" else "show_achievements", True)]
        if show == "playing" and not playing:
            return ev
        if show == "online" and not (playing or online):
            return ev
        friends = getattr(self, "friends", []) if self.cfg.get("layout") == "friends" else []
        if len(friends) >= 2:
            out = [f"friends:{i}" for i in range(0, len(friends), 3)]
        else:
            out = ["playing"] if playing else (["profile"] if self.cfg.get("show_profile", True) else [])
        if self.cfg.get("show_stats", True):
            n = len(self.stats())
            out += [f"stats:{i}" for i in range((n + 3) // 4)]
        return ev + out

    def takeover_slides(self):
        return ["playing"] if self.cfg.get("takeover") and self.game() is not None else []

    def status(self):
        s = super().status()
        if self.cfg.get("gamertag"):
            s["xbox"] = {"game": self.game(), "online": self.online(), "entities": len(self.states),
                         "found": {k: v for k, v in self.roles.items()}}
        return s

    # -- drawing ------------------------------------------------------------------------------
    def render(self, c, slide, t):
        s = str(slide or "")
        if s.startswith(("friend:", "ach:")):
            e = next((x for x in self.events if x["key"] == s), None)
            if e:
                (self._render_friend_event if e["kind"] == "friend" else self._render_achievement)(c, e, t)
            return
        if s.startswith("stats:"):
            return self._render_stats(c, int(s.split(":")[1]), t)
        if s.startswith("friends:"):
            return self._render_friends(c, int(s.split(":")[1]), t)
        if s == "playing":
            lay = self.cfg.get("layout", "classic")
            if lay in ("cinema", "banner", "session"):
                from .steam_layouts import render_banner, render_cinema, render_session
                fn = {"cinema": render_cinema, "banner": render_banner, "session": render_session}[lay]
                return fn(_SteamLook(self), c, self._player(), t)
            if lay == "stats":
                return self._render_stats_card(c, t)
            return self._render_playing(c, t)
        return self._render_profile(c, t)

    def _player(self):
        """This board's game in the shape the Steam board's layouts draw."""
        hero = getattr(self, "hero", None) or {}
        art = getattr(self, "game_art", None) if self._art_style() != "gamerpic" else None
        return {"game": self.game() or "In game", "appid": "xbox", "since": getattr(self, "_since", time.time()),
                "name": self.cfg.get("gamertag") or "", "art": art or getattr(self, "gamerpic", None),
                "hero": self._hero_bg(), "hero_full": hero.get("full"),
                "hero_banner": hero.get("banner"), "total_minutes": None}

    def _picture(self, c, prefer_game=True, hero=False):
        """The picture on the left (game art / gamerpic / an Xbox badge). Returns the text's left edge."""
        H = c.height
        img = None
        if prefer_game and self._art_style() != "gamerpic":
            img = getattr(self, "game_art", None)
        img = img or getattr(self, "gamerpic", None)
        if img is not None:
            c.paste(img, (H - img.width) // 2 if img.width < H else 0, (H - img.height) // 2)
            return H + 3
        self._badge(c, 2, 2, H - 4)
        return H + 3

    @staticmethod
    def _badge(c, x, y, s):
        """A simple Xbox-style sphere: a green circle with a white X."""
        c.draw.ellipse((x, y, x + s - 1, y + s - 1), fill=scale(XBOX_GREEN, 0.75), outline=XBOX_GREEN)
        m = s // 4
        c.draw.line((x + m, y + m, x + s - 1 - m, y + s - 1 - m), fill=WHITE, width=2)
        c.draw.line((x + s - 1 - m, y + m, x + m, y + s - 1 - m), fill=WHITE, width=2)

    def _hero_bg(self):
        """The dimmed hero for the background, fading in from black on the left (behind the picture) like the
        Steam board's Banner, or None when the switch is off or the game has no hero."""
        dim = (getattr(self, "hero", None) or {}).get("dim") if self.cfg.get("hero") else None
        if dim is None:
            return None
        hit = getattr(self, "_hero_faded", None)
        if hit and hit[0] is dim:
            return hit[1]
        from PIL import Image
        W, H = dim.size
        start, fade = H - 4, max(1, W // 2)               # clear behind the picture, then a long fade in
        mask = Image.new("L", (W, H), 255)
        for x in range(min(W, start + fade)):
            a = 0 if x < start else round(255 * ((x - start) / fade) ** 1.8)
            mask.paste(a, (x, 0, x + 1, H))
        alpha = dim.getchannel("A") if dim.mode == "RGBA" else Image.new("L", (W, H), 255)
        from PIL import ImageChops
        out = dim.convert("RGBA")
        out.putalpha(ImageChops.multiply(alpha, mask))
        self._hero_faded = (dim, out)
        return out

    @staticmethod
    def _txt(hero):
        """Outlined text over the hero background, plain text otherwise: f(c, x0, x1, y, text, font, color, t)."""
        if hero is not None:
            from .steam import SteamBoard
            return SteamBoard._shadow_scroll
        return lambda c, *a, **k: c.scroll_text(*a, **k)

    def _gamerscore_text(self):
        st = self._st("gamerscore")
        if not st:
            return None
        try:
            return f"{float(st.get('state')):,.0f}"
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _g_icon(c, x, y):
        c.draw.ellipse((x, y, x + 6, y + 6), outline=GOLD)
        c.text(x + 2, y, "G", "4x6", GOLD)

    def _render_playing(self, c, t):
        W, H = c.width, c.height
        hero = self._hero_bg()
        if hero is not None:
            c.paste(hero, 0, 0)
        x0 = self._picture(c, hero=hero is not None)
        txt = self._txt(hero)
        g = self.game() or "In game"
        txt(c, x0, W, 1, g, "6x10", WHITE, t)
        mins = int((time.time() - getattr(self, "_since", time.time())) // 60)
        h, m = divmod(mins, 60)
        clock = f"{h}h {m:02d}m" if h else f"{m}m"
        badges = []
        if is_on(self._st("in_multiplayer")):
            badges.append("MULTIPLAYER")
        if is_on(self._st("in_party")):
            badges.append("PARTY")
        act = activity_of(self._st)
        if act:
            badges.append(act.upper())
        txt(c, x0, W, 12, "  ·  ".join([f"PLAYING · {clock}"] + badges), "5x8", XBOX_GREEN, t)
        tag = (self.cfg.get("gamertag") or "").upper()
        gs = self._gamerscore_text() if self.cfg.get("show_gamerscore", True) else None
        tw = c.text_width(tag, "4x6") if tag else 0
        if tag:
            txt(c, x0, W, H - 7, tag, "4x6", GRAY if hero is None else (215, 215, 215), 0)
        if gs:
            gx = max(x0 + tw + 4, W - c.text_width(gs, "4x6") - 10)
            if gx + 8 + c.text_width(gs, "4x6") <= W:
                if hero is not None:
                    c.rect(gx - 1, H - 8, W - 1, H - 1, (0, 0, 0))
                self._g_icon(c, gx, H - 7)
                c.text(gx + 9, H - 7, gs, "4x6", GOLD)

    def _render_profile(self, c, t):
        W, H = c.width, c.height
        x0 = self._picture(c, prefer_game=False)
        c.scroll_text(x0, W, 1, self.cfg.get("gamertag") or "Xbox", "6x10", WHITE, t)
        status = self._st("status")
        text = str((status or {}).get("state") or ("ONLINE" if self.online() else "OFFLINE"))
        on = self.online()
        pulse = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(t * 3))
        c.draw.ellipse((x0, 13, x0 + 4, 17), fill=scale(XBOX_GREEN, pulse) if on else (90, 90, 96))
        c.scroll_text(x0 + 7, W, 12, text.upper(), "5x8", XBOX_GREEN if on else GRAY, t)
        gs = self._gamerscore_text() if self.cfg.get("show_gamerscore", True) else None
        if gs:
            self._g_icon(c, x0, H - 7)
            c.text(x0 + 9, H - 7, gs, "4x6", GOLD)

    def _render_friend_event(self, c, e, t):
        W, H = c.width, c.height
        c.rect(0, 0, 1, H - 1, XBOX_GREEN)
        if e.get("avatar") is not None:
            c.paste(e["avatar"], 4, 4)
        else:
            self._badge(c, 4, 4, 24)
        x0 = 33
        c.text(x0, 0, "FRIEND NOW PLAYING", "4x6", XBOX_GREEN)
        c.scroll_text(x0, W, 8, e["name"], "6x10", WHITE, t)
        c.scroll_text(x0, W, 20, e["game"], "5x7", BLUE, t)

    def _render_achievement(self, c, e, t):
        W, H = c.width, c.height
        glow = 0.6 + 0.4 * math.sin(t * 4)
        d = c.draw
        d.ellipse((4, 4, 27, 27), outline=GOLD)                      # a gold medal with a G
        d.ellipse((7, 7, 24, 24), fill=scale(GOLD, 0.35 + 0.25 * glow), outline=GOLD)
        c.text_centered(9, "G", "7x13B", WHITE, x0=4, x1=28)
        for a in range(0, 360, 45):                                # sparkles circling it
            ang = math.radians(a + t * 60)
            c.pixel(round(15.5 + 14 * math.cos(ang)), round(15.5 + 14 * math.sin(ang)), scale(GOLD, glow))
        x0 = 33
        c.text(x0, 0, "ACHIEVEMENT UNLOCKED", "4x6", GOLD)
        c.text(x0, 8, f"+{e['gained']:,} G", "6x10", WHITE)
        c.scroll_text(x0, W, 19, e["game"] or "Xbox", "4x6", XBOX_GREEN, t)
        c.text(x0, H - 6, f"GAMERSCORE {e['score']:,}", "4x6", GRAY)

    def _render_stats_card(self, c, t):
        """Playing: the game and session time, your gamerscore big, and account tier / followers."""
        W, H = c.width, c.height
        hero = self._hero_bg()
        if hero is not None:
            c.paste(hero, 0, 0)
        x0 = self._picture(c, hero=hero is not None)
        txt = self._txt(hero)
        txt(c, x0, W, 0, self.game() or "In game", "5x7", WHITE, t)
        gs = self._gamerscore_text()
        if gs:
            if hero is not None:
                c.rect(x0 - 1, 8, x0 + 9 + c.text_width(gs, "6x10"), 17, (0, 0, 0))
            self._g_icon(c, x0, 10)
            c.text(x0 + 9, 8, gs, "6x10", GOLD)
        mins = int((time.time() - getattr(self, "_since", time.time())) // 60)
        h, m = divmod(mins, 60)
        txt(c, x0, W, 19, f"PLAYING {h}H {m:02d}M" if h else f"PLAYING {m}M", "4x6", XBOX_GREEN, 0)
        extra = []
        for label, val in self.stats():
            if any(k in label for k in ("TIER", "FOLLOWER")):
                extra.append(val if "TIER" in label else f"{val} FOLLOWERS")
        if extra:
            txt(c, x0, W, H - 6, " · ".join(extra), "4x6", scale(WHITE, 0.75), t)

    def _render_friends(self, c, page, t):
        """You and your friends who are online, one per row: gamerpic, gamertag, game (or ONLINE)."""
        W = c.width
        rows = getattr(self, "friends", [])[page * 3:page * 3 + 3]
        for i, f in enumerate(rows):
            y = 1 + i * 11
            if f["pic"] is not None:
                c.paste(f["pic"], 1, y)
            else:
                c.rect(1, y, 9, y + 8, scale(XBOX_GREEN, 0.4))
            name = f["tag"].upper()
            name = name if c.text_width(name, "5x7") <= 48 else name[:8]
            nw = c.text(13, y + 1, name, "5x7", WHITE if not f["me"] else GOLD)
            if f["game"]:
                c.scroll_text(13 + nw + 4, W, y + 1, f["game"], "5x7", XBOX_GREEN, t)
            else:
                c.text(13 + nw + 4, y + 1, "ONLINE", "5x7", BLUE)

    def _render_stats(self, c, page, t):
        W = c.width
        rows = self.stats()[page * 4:(page + 1) * 4]
        for i, (label, val) in enumerate(rows):
            y = i * 8
            vw = c.text_width(val, "5x7")
            c.scroll_text(1, max(20, W - vw - 4), y + 1, label, "4x6", XBOX_GREEN, t)
            c.text_right(W - 1, y, val, "5x7", GOLD if "GAMERSCORE" in label else WHITE)


def activity_of(st):
    """The Rich Presence text after the game's name ('Exploring Port Gellhorn'), or ''."""
    for ent in (st("now_playing"), st("status")):
        if not ent:
            continue
        attrs = ent.get("attributes") or {}
        for text in (ent.get("state"), attrs.get("title"), attrs.get("rich_presence"), attrs.get("activity")):
            if clean_game(text):
                act = split_presence(text)[1]
                if act:
                    return act
    return ""


def game_of(st):
    """The game a gamertag is playing, from its entities (st(role) -> state or None); 'In game' when the name
    isn't known; None when not playing."""
    np = st("now_playing")
    if np:
        name = clean_game(np.get("state")) or clean_game((np.get("attributes") or {}).get("title"))
        if name:
            return name
    status = st("status")
    attrs = (status or {}).get("attributes") or {}
    for k in ("game", "title", "now_playing", "rich_presence", "activity"):
        name = clean_game(attrs.get(k))
        if name:
            return name
    from_status = clean_game((status or {}).get("state"))
    in_game = st("in_game")
    if from_status and (in_game is None or is_on(in_game)):
        return from_status
    if is_on(in_game):
        return "In game"
    return None


class _SteamLook:
    """Lets the Steam board's layout functions draw for the Xbox board."""

    def __init__(self, xbox):
        self.x = xbox

    def _render_classic(self, c, p, t):
        self.x._render_playing(c, t)

    @staticmethod
    def _shadow_scroll(c, x0, x1, y, text, font, color, t, **kw):
        from .steam import SteamBoard
        SteamBoard._shadow_scroll(c, x0, x1, y, text, font, color, t, **kw)

    def _steam_badge(self, c, x, y, s):
        self.x._badge(c, x, y, s)


def _ago(stamp):
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return str(stamp)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    mins = int((datetime.now(timezone.utc) - then).total_seconds() // 60)
    if mins < 2:
        return "now"
    if mins < 60:
        return f"{mins}m ago"
    if mins < 48 * 60:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"
