from .base import COMMON_DEFAULTS, COMMON_FIELDS

__all__ = ["BOARD_CLASSES", "LEAGUES", "LeagueBoard", "board_defaults", "board_fields", "migrate"]
from .clock import ClockBoard
from .homeassistant import HomeAssistantBoard
from .message import MessageBoard
from .plex import PlexBoard
from .sports import LEAGUE_BOARDS, LEAGUES, LeagueBoard
from .spotify import SpotifyBoard

BOARD_CLASSES = {cls.name: cls for cls in (
    *LEAGUE_BOARDS, SpotifyBoard, PlexBoard, HomeAssistantBoard, ClockBoard, MessageBoard)}


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
    return changed
