import time
from datetime import datetime

from .base import Board
from ..gfx import hex_to_rgb, GRAY


class ClockBoard(Board):
    name = "clock"
    title = "Clock"
    description = "Time and date. Also used as the fallback when nothing else has content."
    DEFAULTS = {"enabled": True, "duration": 10, "format_24h": False, "show_seconds": False,
                "show_date": True, "color": "#00a0ff", "date_color": "#888888"}
    FIELDS = [
        {"key": "duration", "label": "Seconds on screen", "type": "number"},
        {"key": "format_24h", "label": "24-hour time", "type": "bool"},
        {"key": "show_seconds", "label": "Show seconds", "type": "bool"},
        {"key": "show_date", "label": "Show date", "type": "bool"},
        {"key": "color", "label": "Time color", "type": "color"},
        {"key": "date_color", "label": "Date color", "type": "color"},
    ]

    def render(self, c, slide, t):
        now = datetime.now()
        if self.cfg.get("format_24h"):
            fmt = "%H:%M:%S" if self.cfg.get("show_seconds") else "%H:%M"
            ampm = ""
        else:
            fmt = "%I:%M:%S" if self.cfg.get("show_seconds") else "%I:%M"
            ampm = now.strftime("%p")
        s = now.strftime(fmt).lstrip("0") or "0"
        if not self.cfg.get("show_seconds") and int(time.time()) % 2:
            s = s.replace(":", " ")  # blinking colon
        color = hex_to_rgb(self.cfg.get("color"))
        font = "7x13B"
        w = c.text_width(s, font) + (c.text_width(ampm, "5x8") + 2 if ampm else 0)
        show_date = self.cfg.get("show_date")
        y = 3 if show_date else (c.height - 13) // 2
        x = (c.width - w) // 2
        x += c.text(x, y, s, font, color)
        if ampm:
            c.text(x + 2, y + 5, ampm, "5x8", color)
        if show_date:
            c.text_centered(c.height - 11, now.strftime("%a %b %-d").upper(), "6x10",
                            hex_to_rgb(self.cfg.get("date_color"), GRAY))
