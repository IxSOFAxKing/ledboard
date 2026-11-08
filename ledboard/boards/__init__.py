from .clock import ClockBoard
from .homeassistant import HomeAssistantBoard
from .message import MessageBoard
from .plex import PlexBoard
from .sports import SportsBoard
from .spotify import SpotifyBoard

BOARD_CLASSES = {cls.name: cls for cls in (
    SportsBoard, SpotifyBoard, PlexBoard, HomeAssistantBoard, ClockBoard, MessageBoard)}


def board_defaults():
    return {name: dict(cls.DEFAULTS) for name, cls in BOARD_CLASSES.items()}
