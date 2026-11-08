import time
from datetime import datetime

from .base import Board
from .. import fonts
from ..gfx import GRAY, hex_to_rgb

_FONT_NAMES = {
    "4x6": "Tiny (4x6)", "tom-thumb": "Tiny alt (tom-thumb)", "5x7": "Small (5x7)", "5x8": "Small (5x8)",
    "6x10": "Medium (6x10)", "6x13B": "Medium bold (6x13B)", "7x13B": "Large bold (7x13B)",
    "8x13B": "Large wide (8x13B)", "9x15B": "X-large (9x15B)", "9x18B": "XX-large (9x18B)", "10x20": "Huge (10x20)",
}


def font_options():
    avail = fonts.available()
    order = [f for f in _FONT_NAMES if f in avail] + [f for f in avail if f not in _FONT_NAMES]
    return order, {f: _FONT_NAMES.get(f, f) for f in order}


FONTS, FONT_LABELS = font_options()

DATE_FORMATS = {
    "%a %b %-d": "SUN NOV 8",
    "%A": "SUNDAY",
    "%A %-d": "SUNDAY 8",
    "%b %-d": "NOV 8",
    "%a %-m/%-d": "SUN 11/8",
    "%-m/%-d/%Y": "11/8/2026",
    "%-d %b %Y": "8 NOV 2026",
    "%B %-d, %Y": "NOVEMBER 8, 2026",
    "custom": "Custom (strftime pattern below)",
}


class ClockBoard(Board):
    name = "clock"
    title = "Clock"
    description = "Time and date, with every piece of text adjustable. Also used as the fallback when nothing else has content."
    DEFAULTS = {
        "enabled": True, "duration": 10,
        # time
        "format_24h": False, "show_seconds": False, "leading_zero": False, "blink_colon": True,
        "time_font": "7x13B", "color": "#00a0ff",
        # am/pm
        "show_ampm": True, "ampm_font": "5x8", "ampm_color": "", "ampm_lowercase": False,
        # date
        "show_date": True, "date_format": "%a %b %-d", "date_custom": "%a %-d %b", "date_uppercase": True,
        "date_font": "6x10", "date_color": "#888888",
        # extra line
        "extra_text": "", "extra_font": "4x6", "extra_color": "#ff9900",
        # layout
        "align": "center", "line_gap": 2,
    }
    FIELDS = [
        {"key": "time_font", "label": "Time font / size", "type": "select", "section": "Time",
         "options": FONTS, "option_labels": FONT_LABELS},
        {"key": "color", "label": "Time color", "type": "color", "section": "Time"},
        {"key": "format_24h", "label": "24-hour time", "type": "bool", "section": "Time"},
        {"key": "show_seconds", "label": "Show seconds", "type": "bool", "section": "Time"},
        {"key": "leading_zero", "label": "Leading zero (09:05 instead of 9:05)", "type": "bool", "section": "Time"},
        {"key": "blink_colon", "label": "Blink the colon", "type": "bool", "section": "Time"},
        {"key": "show_ampm", "label": "Show AM/PM", "type": "bool", "section": "AM / PM", "show_if": {"format_24h": False}},
        {"key": "ampm_font", "label": "AM/PM font / size", "type": "select", "section": "AM / PM",
         "options": FONTS, "option_labels": FONT_LABELS, "show_if": {"format_24h": False, "show_ampm": True}},
        {"key": "ampm_color", "label": "AM/PM color (blank = same as time)", "type": "text", "section": "AM / PM",
         "show_if": {"format_24h": False, "show_ampm": True}, "help": "e.g. #ffffff"},
        {"key": "ampm_lowercase", "label": "Lowercase (am/pm)", "type": "bool", "section": "AM / PM",
         "show_if": {"format_24h": False, "show_ampm": True}},
        {"key": "show_date", "label": "Show date", "type": "bool", "section": "Date"},
        {"key": "date_format", "label": "Date format", "type": "select", "section": "Date",
         "options": list(DATE_FORMATS), "option_labels": DATE_FORMATS, "show_if": {"show_date": True}},
        {"key": "date_custom", "label": "Custom date pattern", "type": "text", "section": "Date",
         "show_if": {"show_date": True, "date_format": "custom"},
         "help": "strftime codes: %a Sun, %A Sunday, %b Nov, %B November, %-d 8, %-m 11, %Y 2026, %j day of year"},
        {"key": "date_uppercase", "label": "Uppercase date", "type": "bool", "section": "Date", "show_if": {"show_date": True}},
        {"key": "date_font", "label": "Date font / size", "type": "select", "section": "Date",
         "options": FONTS, "option_labels": FONT_LABELS, "show_if": {"show_date": True}},
        {"key": "date_color", "label": "Date color", "type": "color", "section": "Date", "show_if": {"show_date": True}},
        {"key": "extra_text", "label": "Extra line (optional)", "type": "text", "section": "Extra line",
         "help": "Any text under the date, e.g. 'Go Bills!'. Can include date codes like %A."},
        {"key": "extra_font", "label": "Extra line font / size", "type": "select", "section": "Extra line",
         "options": FONTS, "option_labels": FONT_LABELS},
        {"key": "extra_color", "label": "Extra line color", "type": "color", "section": "Extra line"},
        {"key": "align", "label": "Alignment", "type": "select", "section": "Layout",
         "options": ["center", "left", "right"]},
        {"key": "line_gap", "label": "Space between lines (pixels)", "type": "number", "section": "Layout"},
        {"key": "duration", "label": "Seconds on screen", "type": "number", "section": "Layout"},
    ]

    def _lines(self, now):
        """[(text, font, color, kind)] top to bottom; the time line may carry an AM/PM suffix."""
        cfg = self.cfg
        h24 = cfg.get("format_24h")
        secs = cfg.get("show_seconds")
        fmt = ("%H" if h24 else "%I") + ":%M" + (":%S" if secs else "")
        txt = now.strftime(fmt)
        if not cfg.get("leading_zero", False) and txt.startswith("0") and len(txt) > 4:
            txt = txt[1:]
        if cfg.get("blink_colon", True) and not secs and int(time.time()) % 2:
            txt = txt.replace(":", " ")
        time_color = hex_to_rgb(cfg.get("color"))
        lines = [(txt, cfg.get("time_font") or "7x13B", time_color, "time")]
        if cfg.get("show_date", True):
            pattern = cfg.get("date_custom") if cfg.get("date_format") == "custom" else cfg.get("date_format")
            try:
                d = now.strftime(pattern or "%a %b %-d")
            except ValueError:
                d = now.strftime("%a %b %-d")
            if cfg.get("date_uppercase", True):
                d = d.upper()
            lines.append((d, cfg.get("date_font") or "6x10", hex_to_rgb(cfg.get("date_color"), GRAY), "date"))
        extra = (cfg.get("extra_text") or "").strip()
        if extra:
            try:
                extra = now.strftime(extra)
            except ValueError:
                pass
            lines.append((extra, cfg.get("extra_font") or "4x6", hex_to_rgb(cfg.get("extra_color"), GRAY), "extra"))
        return lines

    def render(self, c, slide, t):
        now = datetime.now()
        cfg = self.cfg
        lines = self._lines(now)
        gap = int(cfg.get("line_gap", 2) or 0)
        ampm = ""
        if not cfg.get("format_24h") and cfg.get("show_ampm", True):
            ampm = now.strftime("%p")
            ampm = ampm.lower() if cfg.get("ampm_lowercase") else ampm
        ampm_font = cfg.get("ampm_font") or "5x8"
        ampm_color = hex_to_rgb(cfg.get("ampm_color"), lines[0][2]) if cfg.get("ampm_color") else lines[0][2]

        # vertical layout: center the block of lines
        heights = [fonts.get(f).height for _, f, _, _ in lines]
        total = sum(heights) + gap * (len(lines) - 1)
        y = max(0, (c.height - total) // 2)
        align = cfg.get("align", "center")
        for (text, font, color, kind), hgt in zip(lines, heights):
            w = c.text_width(text, font)
            suffix_w = 0
            if kind == "time" and ampm:
                suffix_w = c.text_width(ampm, ampm_font) + 2
            full = w + suffix_w
            if align == "left":
                x = 1
            elif align == "right":
                x = c.width - 1 - full
            else:
                x = (c.width - full) // 2
            if full > c.width and kind != "time":
                c.scroll_text(0, c.width, y, text, font, color, t, align="center")
            else:
                c.text(x, y, text, font, color)
                if suffix_w:
                    # AM/PM sits on the time's baseline
                    f_time, f_ap = fonts.get(font), fonts.get(ampm_font)
                    ay = y + f_time.ascent - f_ap.ascent
                    c.text(x + w + 2, ay, ampm, ampm_font, ampm_color)
            y += hgt + gap
