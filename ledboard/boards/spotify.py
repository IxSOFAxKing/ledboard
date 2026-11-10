"""Now Playing: from Home Assistant media players (e.g. HA's Spotify integration)
or directly from the Spotify Web API.

Home Assistant source: pick media_player entities; uses the URL/token from the
Home Assistant board. Works with any HA media player (Spotify, Sonos, ...).

Spotify API source setup (done from the web UI):
  1. Create an app at https://developer.spotify.com/dashboard
  2. Add the redirect URI shown in the web UI (default http://127.0.0.1:8080/spotify/callback)
  3. Paste Client ID + Secret into the web UI and click "Connect Spotify".
"""
from __future__ import annotations

import base64
import logging
import time
import urllib.parse

import requests

from .base import Board, organize
from ..gfx import GRAY, WHITE, Ease, fetch_image, fmt_ms, hex_to_rgb

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = "user-read-currently-playing user-read-playback-state"
GREEN = (30, 215, 96)


class SpotifyBoard(Board):
    name = "spotify"
    title = "Now Playing (Spotify)"
    category = "Media"
    description = ("Shows what's playing. Get it from Home Assistant (e.g. its Spotify integration, "
                   "Sonos, or any media player) or straight from Spotify. Skipped when nothing is playing.")
    refresh = 5
    DEFAULTS = {
        "enabled": False, "duration": 15, "refresh": 5, "takeover": True, "show_paused": False,
        "source": "homeassistant", "ha_entities": [],
        "client_id": "", "client_secret": "", "refresh_token": "",
        "redirect_uri": "http://127.0.0.1:8080/spotify/callback",
        "title_color": "#ffffff", "artist_color": "#1ed760", "progress_style": "wavy",
    }
    FIELDS = [
        {"key": "source", "label": "Get now-playing info from", "type": "select",
         "options": ["homeassistant", "spotify"],
         "option_labels": {"homeassistant": "Home Assistant media player", "spotify": "Spotify Web API"}},
        {"key": "ha_entities", "label": "Media player entities (comma-separated)", "type": "list",
         "show_if": {"source": "homeassistant"}, "datalist": "ha-media-players",
         "help": "e.g. media_player.spotify_adam. Uses the URL and token from the Home Assistant board. "
                 "If several are playing, each gets its own slide."},
        {"key": "spotify_auth", "label": "Account", "type": "spotify_auth", "show_if": {"source": "spotify"}},
        {"key": "client_id", "label": "Client ID", "type": "text", "show_if": {"source": "spotify"}},
        {"key": "client_secret", "label": "Client Secret", "type": "password", "show_if": {"source": "spotify"}},
        {"key": "redirect_uri", "label": "Redirect URI (must match your Spotify app)", "type": "text",
         "show_if": {"source": "spotify"}},
        {"key": "takeover", "label": "Take over the display while music is playing", "type": "bool"},
        {"key": "show_paused", "label": "Keep showing when paused", "type": "bool"},
        {"key": "duration", "label": "Seconds on screen (in rotation)", "type": "number"},
        {"key": "progress_style", "label": "Progress bar style", "type": "select",
         "options": ["wavy", "straight"],
         "option_labels": {"wavy": "Wavy (animated, Material 3 style)", "straight": "Straight"}},
        {"key": "title_color", "label": "Title color", "type": "color"},
        {"key": "artist_color", "label": "Artist color", "type": "color"},
        {"key": "refresh", "label": "Poll interval (s)", "type": "number"},
    ]
    FIELDS = organize(FIELDS, [
        ("Basics", "Source", ["source", "ha_entities", "spotify_auth", "client_id", "client_secret", "redirect_uri"]),
        ("Screens", "Now playing", ["takeover", "show_paused"]),
        ("Look", "Style", ["progress_style", "title_color", "artist_color"]),
        ("Advanced", "Timing", ["duration", "refresh"])], {})

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.access_token = None
        self.token_expiry = 0
        self.now = None      # dict describing current track (first player)
        self.fetched_at = 0
        self.players = {}    # slide key -> (now dict, fetched_at)
        self._wave = Ease()

    # -- auth -----------------------------------------------------------------
    def auth_url(self, state="ledboard"):
        q = {"client_id": self.cfg.get("client_id", ""), "response_type": "code",
             "redirect_uri": self.cfg.get("redirect_uri"), "scope": SCOPES, "state": state}
        return AUTH_URL + "?" + urllib.parse.urlencode(q)

    def _basic(self):
        raw = f"{self.cfg.get('client_id', '')}:{self.cfg.get('client_secret', '')}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    def exchange_code(self, code):
        """Swap an authorization code for tokens. Returns the refresh token."""
        r = requests.post(TOKEN_URL, headers=self._basic(), timeout=10, data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": self.cfg.get("redirect_uri")})
        if r.status_code != 200:
            raise RuntimeError(f"Spotify token exchange failed: {r.text}")
        j = r.json()
        self.access_token = j["access_token"]
        self.token_expiry = time.time() + j.get("expires_in", 3600) - 60
        return j["refresh_token"]

    def _token(self):
        if self.access_token and time.time() < self.token_expiry:
            return self.access_token
        rt = self.cfg.get("refresh_token")
        if not rt:
            raise RuntimeError("Not connected - click 'Connect Spotify' in the web UI")
        r = requests.post(TOKEN_URL, headers=self._basic(), timeout=10,
                          data={"grant_type": "refresh_token", "refresh_token": rt})
        if r.status_code != 200:
            raise RuntimeError(f"token refresh failed ({r.status_code}): {r.text[:200]}")
        j = r.json()
        self.access_token = j["access_token"]
        self.token_expiry = time.time() + j.get("expires_in", 3600) - 60
        if j.get("refresh_token") and j["refresh_token"] != rt:
            # Spotify may rotate the refresh token - persist the new one.
            self.app.save_board_value(self.name, "refresh_token", j["refresh_token"])
        return self.access_token

    @property
    def connected(self):
        return bool(self.cfg.get("refresh_token"))

    # -- data -----------------------------------------------------------------
    def update(self):
        if self.cfg.get("source", "homeassistant") == "homeassistant":
            self._update_ha()
            return
        self.players = {}
        if not (self.cfg.get("client_id") and self.cfg.get("refresh_token")):
            self.now = None
            return
        r = requests.get(f"{API}/me/player/currently-playing", timeout=10,
                         params={"additional_types": "track,episode"},
                         headers={"Authorization": f"Bearer {self._token()}"})
        if r.status_code == 401:
            self.access_token = None
            raise RuntimeError("access token rejected; will refresh")
        if r.status_code == 204 or not r.content:
            self.now = None
            return
        if r.status_code == 429:
            raise RuntimeError("rate limited by Spotify")
        r.raise_for_status()
        self.now = self.parse(r.json(), self.app.height)
        self.fetched_at = time.time()
        self.players = {"spotify": (self.now, self.fetched_at)} if self.now else {}

    def _update_ha(self):
        ha = self.app.boards.get("homeassistant")
        hcfg = ha.cfg if ha else {}
        base = (hcfg.get("url") or "").rstrip("/")
        ents = [e.strip() for e in self.cfg.get("ha_entities", []) if e and e.strip()]
        if not (base and hcfg.get("token") and ents):
            self.players, self.now = {}, None
            if ents and not (base and hcfg.get("token")):
                raise RuntimeError("Set the Home Assistant URL and token on the Home Assistant board")
            return
        headers = {"Authorization": f"Bearer {hcfg['token']}"}
        verify = bool(hcfg.get("verify_ssl", True))
        players = {}
        for ent in ents:
            r = requests.get(f"{base}/api/states/{ent}", headers=headers, timeout=10, verify=verify)
            if r.status_code == 404:
                raise RuntimeError(f"Home Assistant has no entity {ent}")
            r.raise_for_status()
            now = self.parse_ha(r.json(), base, headers, verify, self.app.height)
            if now:
                players[ent] = (now, time.time())
        self.players = players
        self.now = next(iter(players.values()))[0] if players else None
        self.fetched_at = time.time()

    @staticmethod
    def parse_ha(st, base, headers, verify, art_size=32):
        """HA media_player state -> now-playing dict (None if idle/off)."""
        state = st.get("state")
        if state not in ("playing", "paused"):
            return None
        a = st.get("attributes", {})
        title = a.get("media_title") or ""
        if not title:
            return None
        artist = a.get("media_artist") or a.get("media_series_title") or a.get("media_album_artist") or ""
        pos = float(a.get("media_position") or 0)
        upd = a.get("media_position_updated_at")
        if state == "playing" and upd:
            try:
                from datetime import datetime, timezone
                then = datetime.fromisoformat(upd.replace("Z", "+00:00"))
                pos += max(0.0, (datetime.now(timezone.utc) - then).total_seconds())
            except ValueError:
                pass
        pic = a.get("entity_picture") or ""
        art = None
        if pic:
            url = pic if pic.startswith("http") else base + pic
            # cache by the picture path minus the rotating access token
            key = "ha|" + url.split("token=")[0] + "|" + (url.split("cache=")[-1] if "cache=" in url else "")
            art = fetch_image(url, art_size, art_size, headers=headers, verify=verify, cache_key=key)
        dur = float(a.get("media_duration") or 0)
        return {"id": a.get("media_content_id") or title, "title": title, "artist": artist,
                "album": a.get("media_album_name") or "", "duration": int(dur * 1000),
                "progress": int(min(pos, dur) * 1000 if dur else pos * 1000),
                "playing": state == "playing", "art": art,
                "player": a.get("friendly_name") or st.get("entity_id", "")}

    @staticmethod
    def parse(j, art_size=32):
        item = j.get("item")
        if not item:
            return None
        if item.get("type") == "episode":
            artist = (item.get("show") or {}).get("name", "")
            images = item.get("images") or (item.get("show") or {}).get("images") or []
            album = ""
        else:
            artist = ", ".join(a.get("name", "") for a in item.get("artists", []))
            images = (item.get("album") or {}).get("images") or []
            album = (item.get("album") or {}).get("name", "")
        # pick the smallest image that is still >= our art size
        images = sorted(images, key=lambda i: i.get("width") or 0)
        img_url = next((i["url"] for i in images if (i.get("width") or 0) >= art_size),
                       images[-1]["url"] if images else None)
        return {
            "id": item.get("id"), "title": item.get("name", ""), "artist": artist, "album": album,
            "duration": item.get("duration_ms") or 0, "progress": j.get("progress_ms") or 0,
            "playing": bool(j.get("is_playing")),
            "art": fetch_image(img_url, art_size, art_size) if img_url else None,
        }

    def slides(self):
        return [k for k, (n, _) in self.players.items() if n["playing"] or self.cfg.get("show_paused")]

    def takeover_slides(self):
        return self.slides() if self.cfg.get("takeover") else []

    def slide_duration(self, slide):
        return self.duration

    def render(self, c, slide, t):
        n, fetched_at = self.players.get(slide, (None, 0))
        if not n:
            return
        H, W = c.height, c.width
        c.paste(n["art"], 0, 0)
        x0 = H + 2
        progress = n["progress"]
        if n["playing"]:
            progress += (time.time() - fetched_at) * 1000
        progress = min(progress, n["duration"] or progress)
        c.scroll_text(x0, W, 1, n["title"], "6x10", hex_to_rgb(self.cfg.get("title_color"), WHITE), t)
        c.scroll_text(x0, W, 11, n["artist"], "5x8", hex_to_rgb(self.cfg.get("artist_color"), GREEN), t)
        frac = progress / n["duration"] if n["duration"] else 0
        wavy = self.cfg.get("progress_style", "wavy") == "wavy"
        ty = H - 12 if wavy else H - 10          # the wave needs a few more rows
        c.text(x0, ty, fmt_ms(progress), "4x6", GRAY)
        if n["duration"]:
            c.text_right(W - 1, ty, fmt_ms(n["duration"]), "4x6", GRAY)
        if not n["playing"]:
            c.text_centered(ty, "PAUSED", "4x6", (200, 120, 0), x0, W)
        if wavy:
            c.wavy_progress(x0, W - 1, H - 4, frac, GREEN, amp=self._wave.value(slide, n["playing"]))
        else:
            c.progress(x0, H - 3, W - 1, frac, GREEN)

    def status(self):
        s = super().status()
        s["connected"] = self.connected
        s["source"] = self.cfg.get("source", "homeassistant")
        s["now"] = {k: v for k, v in (self.now or {}).items() if k != "art"} or None
        return s
