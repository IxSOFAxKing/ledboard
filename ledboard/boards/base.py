"""Board base class.

A board fetches data in a background thread (`update`) and draws in the render
loop (`render`). Network calls must never happen inside render().

The engine asks a board for its current `slides()` when it becomes active; each
slide is shown for `duration` seconds. A board with no slides is skipped.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback

log = logging.getLogger(__name__)


#: Settings every board gets (merged into DEFAULTS / FIELDS automatically).
COMMON_DEFAULTS = {"scroll_mode": False, "scroll_speed": "", "scroll_gap": ""}   # "" = use global
COMMON_FIELDS = [
    {"key": "scroll_mode", "label": "Scroll mode (slides scroll across like a ticker)", "type": "bool", "section": "Scroll mode"},
    {"key": "scroll_speed", "label": "Scroll speed (pixels per second)", "type": "number", "section": "Scroll mode",
     "show_if": {"scroll_mode": True}, "placeholder": "global default",
     "help": "Leave blank to use the global default (Settings tab)."},
    {"key": "scroll_gap", "label": "Gap between slides (pixels)", "type": "number", "section": "Scroll mode",
     "show_if": {"scroll_mode": True}, "placeholder": "global default"},
]


def short_error(e):
    """Readable one-line error; network errors get a short summary instead of urllib3's dump."""
    import requests
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code} from {requests.utils.urlparse(e.response.url).netloc}"
    if isinstance(e, requests.Timeout):
        return "Timed out connecting to the server"
    if isinstance(e, requests.ConnectionError):
        host = requests.utils.urlparse(getattr(e.request, "url", "") or "").netloc
        return f"Can't connect to {host or 'the server'} (check the URL / network)"
    return f"{type(e).__name__}: {e}"


class Board:
    name = "base"
    title = "Base"
    description = ""
    #: Seconds between update() calls. 0 = no background updates.
    refresh = 0
    #: Default config for this board (merged into config["boards"][name]).
    DEFAULTS: dict = {}
    #: Field descriptions used to build the settings form in the web UI.
    FIELDS: list = []

    def __init__(self, cfg: dict, app):
        self.cfg = cfg
        self.app = app  # the Engine (gives width/height and helpers)
        self.last_update = None
        self.last_error = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None

    # -- lifecycle --------------------------------------------------------
    @property
    def enabled(self):
        return bool(self.cfg.get("enabled"))

    @property
    def duration(self):
        return float(self.cfg.get("duration", 10))

    @property
    def takeover(self):
        """If true and this board has content, the rotation shows only takeover boards."""
        return bool(self.cfg.get("takeover"))

    def start(self):
        if self.refresh and not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(target=self._loop, name=f"board-{self.name}", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def poke(self):
        """Ask for an immediate refresh."""
        self._wake.set()

    def _loop(self):
        while not self._stop.is_set():
            if not self.enabled:
                self._wake.wait(5)
                self._wake.clear()
                continue
            try:
                self.update()
                self.last_update = time.time()
                self.last_error = None
            except Exception as e:
                self.last_error = short_error(e)
                log.warning("[%s] update failed: %s", self.name, self.last_error)
                log.debug(traceback.format_exc())
            self._wake.wait(self.interval())
            self._wake.clear()

    def interval(self):
        return float(self.cfg.get("refresh", self.refresh) or self.refresh)

    # -- to implement -----------------------------------------------------
    def update(self):
        """Fetch data (runs in background thread)."""

    def slides(self) -> list:
        """Return the slides to show right now (empty list = skip this board)."""
        return [None]

    def takeover_slides(self) -> list:
        """Slides that should interrupt the normal rotation (see `takeover`)."""
        return []

    def slide_duration(self, slide) -> float:
        return self.duration

    def render(self, canvas, slide, t: float):
        """Draw `slide` onto `canvas`. `t` = seconds since the slide started."""

    def status(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "last_update": self.last_update,
            "last_error": self.last_error,
            "slides": len(self.slides()) if self.enabled else 0,
        }
