from .ambient import AmbientBoard
from .base import COMMON_DEFAULTS, COMMON_FIELDS

__all__ = ["BOARD_CLASSES", "LEAGUES", "LeagueBoard", "board_defaults", "board_fields", "migrate"]
from .clock import ClockBoard
from .homeassistant import HomeAssistantBoard
from .klipper import KlipperBoard
from .message import MessageBoard
from .plex import PlexBoard
from .sports import LEAGUE_BOARDS, LEAGUES, LeagueBoard
from .spotify import SpotifyBoard
from .steam import SteamBoard
from .ufc import UFCBoard
from .uptime import UptimeBoard
from .weather import WeatherBoard
from .weather_alerts import WeatherAlertsBoard

BOARD_CLASSES = {cls.name: cls for cls in (
    *LEAGUE_BOARDS, UFCBoard, WeatherBoard, WeatherAlertsBoard, SpotifyBoard, SteamBoard, PlexBoard, HomeAssistantBoard, UptimeBoard, KlipperBoard, ClockBoard, AmbientBoard, MessageBoard)}


def board_defaults():
    return {name: {**COMMON_DEFAULTS, **cls.DEFAULTS} for name, cls in BOARD_CLASSES.items()}


def board_fields(cls):
    return list(cls.FIELDS) + COMMON_FIELDS


# settings that moved from the old single "sports" board onto each league board
_OLD_SPORTS_KEYS = ("duration", "refresh", "live_refresh", "favorites_only", "show_pregame", "show_final",
                    "final_hours", "upcoming_hours", "max_games", "takeover", "use_dark_logos")


def migrate(data: dict) -> bool:
    """Upgrade a saved config in place. Returns True if anything changed."""
    changed = False
    boards = data.get("boards") or {}
    old = boards.get("sports")
    if isinstance(old, dict) and isinstance(old.get("leagues"), dict):
        # v1 had one "sports" board with a leagues table; v2 has a board per league
        new_keys = []
        for key in LEAGUES:
            lc = old["leagues"].get(key) or {}
            nb = boards.setdefault(key, {})
            for k in _OLD_SPORTS_KEYS:
                if k in old and k not in nb:
                    nb[k] = old[k]
            nb.setdefault("enabled", bool(old.get("enabled", True)) and bool(lc.get("enabled")))
            nb.setdefault("favorites", list(lc.get("favorites") or []))
            new_keys.append(key)
        del boards["sports"]
        rot = data.get("rotation") or []
        enabled_first = [k for k in new_keys if boards[k].get("enabled")] + \
                        [k for k in new_keys if not boards[k].get("enabled")]
        if "sports" in rot:
            i = rot.index("sports")
            rot[i:i + 1] = enabled_first
        else:
            rot = enabled_first + rot
        data["rotation"] = rot
        if data.get("fallback_board") == "sports":
            data["fallback_board"] = "clock"
        changed = True
    # v2 -> v3: per-board scroll speed/gap left at the old built-in defaults now follow the
    # global scroll settings instead
    for b in boards.values():
        if isinstance(b, dict):
            if b.get("scroll_speed") == 30:
                b["scroll_speed"] = ""
                changed = True
            if b.get("scroll_gap") == 16:
                b["scroll_gap"] = ""
                changed = True
    # v3 -> v4: "final_hours" now counts from the end of the game (was: from kickoff + 3 h); the
    # untouched old default of 18 becomes the new default of 24
    if not data.get("_final_hours_v4"):
        for name in LEAGUES:
            b = boards.get(name)
            if isinstance(b, dict) and b.get("final_hours") == 18:
                b["final_hours"] = 24
        data["_final_hours_v4"] = True
        changed = True
    # v4 -> v5: the upcoming-game accent ('upcoming_style') joined the single 'Design' list
    for name in LEAGUES:
        b = boards.get(name)
        if isinstance(b, dict) and b.get("upcoming_style") not in (None, "", "plain") \
                and b.get("upcoming_layout", "classic") == "classic":
            b["upcoming_layout"], b["upcoming_style"] = b["upcoming_style"], "plain"
            changed = True
    # v5 -> v6: the clock only offers scalable fonts; old pixel-font picks become Inter Bold at a matching size
    clock = boards.get("clock")
    if isinstance(clock, dict):
        from .clock import DEFAULT_FONT, PIXEL_FONT_SIZES
        for part, default_size in (("time", None), ("ampm", None), ("date", 10), ("extra", 9)):
            name = clock.get(f"{part}_font")
            if name is None or str(name).startswith("ttf:"):
                continue
            clock[f"{part}_font"] = DEFAULT_FONT
            size = PIXEL_FONT_SIZES.get(str(name))
            # the time and AM/PM size themselves; the date / extra line keep the old font's height
            if default_size and clock.get(f"{part}_size") in (None, "") and size and size != default_size:
                clock[f"{part}_size"] = size
            changed = True
    return changed
