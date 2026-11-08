from .base import Board
from ..gfx import hex_to_rgb


class MessageBoard(Board):
    name = "message"
    title = "Custom Message"
    description = "A custom line of text in the rotation."
    DEFAULTS = {"enabled": False, "duration": 10, "text": "Hello from LEDBoard!",
                "color": "#ff9900", "font": "7x13B", "speed": 25}
    FIELDS = [
        {"key": "duration", "label": "Seconds on screen", "type": "number"},
        {"key": "text", "label": "Text", "type": "text"},
        {"key": "color", "label": "Color", "type": "color"},
        {"key": "font", "label": "Font", "type": "select",
         "options": ["4x6", "5x7", "5x8", "6x10", "7x13B", "tom-thumb"]},
        {"key": "speed", "label": "Scroll speed (px/s)", "type": "number"},
    ]

    def slides(self):
        return [None] if self.cfg.get("text") else []

    def render(self, c, slide, t):
        draw_message(c, self.cfg.get("text", ""), hex_to_rgb(self.cfg.get("color")),
                     self.cfg.get("font", "7x13B"), t, float(self.cfg.get("speed", 25)))


def draw_message(c, text, color, font, t, speed=25):
    from .. import fonts
    f = fonts.get(font)
    y = (c.height - f.height) // 2
    c.scroll_text(0, c.width, y, text, font, color, t, speed=speed, align="center", pause=0.5)
