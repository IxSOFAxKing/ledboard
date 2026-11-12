import time
from datetime import datetime

from .base import Board, organize
from .. import fonts
from ..gfx import GRAY, hex_to_rgb

DEFAULT_FONT = "ttf:inter"
#: pixel fonts the clock used to offer -> the scalable size that matches their height (for old settings)
PIXEL_FONT_SIZES = {"4x6": 7, "tom-thumb": 7, "5x7": 8, "5x8": 9, "6x10": 10, "6x13B": 12, "7x13B": 13,
                    "8x13B": 13, "9x15B": 15, "9x18B": 17, "10x20": 19}


def font_options():
    """Scalable fonts only (any size): the basic bold ones first, then the display fonts."""
    keys = list(fonts.SCALABLE)
    return [f"ttf:{k}" for k in keys], {f"ttf:{k}": fonts.SCALABLE[k][0] for k in keys}


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
    category = "Display"
    description = "Time and date, with every piece of text adjustable. Also used as the fallback when nothing else has content."
    DEFAULTS = {
        "enabled": True, "duration": 10,
        # time
        "format_24h": False, "show_seconds": False, "leading_zero": False, "blink_colon": True,
        "time_font": DEFAULT_FONT, "time_size": "", "color": "#00a0ff", "smooth": False,
        # am/pm
        "show_ampm": True, "ampm_font": DEFAULT_FONT, "ampm_size": "", "ampm_color": "", "ampm_lowercase": False,
        # date
        "show_date": True, "date_format": "%a %b %-d", "date_custom": "%a %-d %b", "date_uppercase": True,
        "date_font": DEFAULT_FONT, "date_size": "", "date_color": "#888888",
        # extra line
        "extra_text": "", "extra_font": DEFAULT_FONT, "extra_size": "", "extra_color": "#ff9900",
        # layout
        "align": "center", "line_gap": 2,
    }
    FIELDS = [
        {"key": "time_font", "label": "Time font", "type": "select", "section": "Time",
         "options": FONTS, "option_labels": FONT_LABELS},
        {"key": "time_size", "label": "Time size (pixels)", "type": "number", "section": "Time", "placeholder": "auto",
         "help": "Blank = as big as fits with the other lines."},
        {"key": "color", "label": "Time color", "type": "color", "section": "Time"},
        {"key": "format_24h", "label": "24-hour time", "type": "bool", "section": "Time"},
        {"key": "show_seconds", "label": "Show seconds", "type": "bool", "section": "Time"},
        {"key": "leading_zero", "label": "Leading zero (09:05 instead of 9:05)", "type": "bool", "section": "Time"},
        {"key": "blink_colon", "label": "Blink the colon", "type": "bool", "section": "Time"},
        {"key": "show_ampm", "label": "Show AM/PM", "type": "bool", "section": "AM / PM", "show_if": {"format_24h": False}},
        {"key": "ampm_font", "label": "AM/PM font", "type": "select", "section": "AM / PM",
         "options": FONTS, "option_labels": FONT_LABELS, "show_if": {"format_24h": False, "show_ampm": True}},
        {"key": "ampm_size", "label": "AM/PM size (pixels)", "type": "number", "section": "AM / PM", "placeholder": "auto",
         "show_if": {"format_24h": False, "show_ampm": True}, "help": "Blank = automatic (in proportion to the time)."},
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
        {"key": "date_font", "label": "Date font", "type": "select", "section": "Date",
         "options": FONTS, "option_labels": FONT_LABELS, "show_if": {"show_date": True}},
        {"key": "date_size", "label": "Date size (pixels)", "type": "number", "section": "Date", "placeholder": "auto",
         "show_if": {"show_date": True}, "help": "Blank = 10."},
        {"key": "date_color", "label": "Date color", "type": "color", "section": "Date", "show_if": {"show_date": True}},
        {"key": "extra_text", "label": "Extra line (optional)", "type": "text", "section": "Extra line",
         "help": "Any text under the date, e.g. 'Go Bills!'. Can include date codes like %A."},
        {"key": "extra_font", "label": "Extra line font", "type": "select", "section": "Extra line",
         "options": FONTS, "option_labels": FONT_LABELS},
        {"key": "extra_size", "label": "Extra line size (pixels)", "type": "number", "section": "Extra line",
         "placeholder": "auto", "help": "Blank = 9."},
        {"key": "extra_color", "label": "Extra line color", "type": "color", "section": "Extra line"},
        {"key": "smooth", "label": "Smooth edges (anti-aliasing)", "type": "bool", "section": "Layout",
         "help": "Off = crisp on/off pixels. On = softer curves using dimmer edge pixels."},
        {"key": "align", "label": "Alignment", "type": "select", "section": "Layout",
         "options": ["center", "left", "right"]},
        {"key": "line_gap", "label": "Space between lines (pixels)", "type": "number", "section": "Layout"},
        {"key": "duration", "label": "Seconds on screen", "type": "number", "section": "Layout"},
    ]
    FIELDS = organize(FIELDS, [
        ("Basics", "Time", ["time_font", "time_size", "color", "format_24h", "show_seconds", "leading_zero",
                            "blink_colon"]),
        ("Look", "AM / PM", ["show_ampm", "ampm_font", "ampm_size", "ampm_color", "ampm_lowercase"]),
        ("Look", "Date", ["show_date", "date_format", "date_custom", "date_uppercase", "date_font", "date_size",
                          "date_color"]),
        ("Look", "Extra line", ["extra_text", "extra_font", "extra_size", "extra_color"]),
        ("Look", "Layout", ["smooth", "align", "line_gap"]),
        ("Advanced", "Timing", ["duration"]),
    ])

    def _font(self, name, size, ref, default_size):
        """Font object for a setting ('ttf:<key>') at a size; anything else falls back to the default font."""
        name = name if (name or "").startswith("ttf:") and name[4:] in fonts.SCALABLE else DEFAULT_FONT
        key = name[4:]
        try:
            px = int(float(size)) if size not in (None, "") else default_size
        except (TypeError, ValueError):
            px = default_size
        grid = fonts.PIXEL_GRID.get(key)
        if grid:
            px = max(grid, px // grid * grid)
        return fonts.scalable(key, max(5, min(64, px)), ref, bool(self.cfg.get("smooth")))

    def _fit_time(self, name, avail_h, template, ampm, c_width):
        """Largest scalable size whose text fits avail_h and the panel width (cached)."""
        key = (name, avail_h, template, ampm, c_width, bool(self.cfg.get("smooth")), self.cfg.get("ampm_font"),
               self.cfg.get("ampm_size"))
        cache = self.__dict__.setdefault("_fit_cache", {})
        if key in cache:
            return cache[key]
        best = None
        for px in range(64, 4, -1):
            f = self._font(name, px, "0123456789:", px)
            if f.height > avail_h:
                continue
            w = f.width(template)
            if ampm:
                af = self._ampm_font(f)
                w += af.width(ampm) + 2
            if w <= c_width - 2:
                best = f
                break
        best = best or self._font(name, 6, "0123456789:", 6)
        cache[key] = best
        return best

    def _ampm_font(self, time_font):
        return self._font(self.cfg.get("ampm_font"), self.cfg.get("ampm_size"), "AMP",
                          max(5, round(time_font.height * 0.4)))

    def _lines(self, now):
        """[(text, font, color, kind)] top to bottom; the time line may carry an AM/PM suffix."""
        cfg = self.cfg
        h24 = cfg.get("format_24h")
        secs = cfg.get("show_seconds")
        fmt = ("%H" if h24 else "%I") + ":%M" + (":%S" if secs else "")
        txt = now.strftime(fmt)
        if not cfg.get("leading_zero", False) and txt.startswith("0") and len(txt) > 4:
            txt = txt[1:]
        # blink by skipping the colon when drawing (keeps its width, so nothing shifts)
        self._colon_off = bool(cfg.get("blink_colon", True) and not secs and int(time.time()) % 2)
        time_color = hex_to_rgb(cfg.get("color"))
        tname = cfg.get("time_font")
        tname = tname if (tname or "").startswith("ttf:") and tname[4:] in fonts.SCALABLE else DEFAULT_FONT
        lines = [(txt, tname, time_color, "time")]
        self._template = ("00" if (h24 or cfg.get("leading_zero")) else "10") + ":00" + (":00" if secs else "")
        if cfg.get("show_date", True):
            pattern = cfg.get("date_custom") if cfg.get("date_format") == "custom" else cfg.get("date_format")
            try:
                d = now.strftime(pattern or "%a %b %-d")
            except ValueError:
                d = now.strftime("%a %b %-d")
            if cfg.get("date_uppercase", True):
                d = d.upper()
            lines.append((d, self._font(cfg.get("date_font"), cfg.get("date_size"), "Ay0g", 10),
                          hex_to_rgb(cfg.get("date_color"), GRAY), "date"))
        extra = (cfg.get("extra_text") or "").strip()
        if extra:
            try:
                extra = now.strftime(extra)
            except ValueError:
                pass
            lines.append((extra, self._font(cfg.get("extra_font"), cfg.get("extra_size"), "Ay0g", 9),
                          hex_to_rgb(cfg.get("extra_color"), GRAY), "extra"))
        return lines

    @staticmethod
    def _hide_colons(c, x, y, text, font):
        """Blank out just the colon glyphs of text drawn at (x, y); every other pixel stays put."""
        for i, ch in enumerate(text):
            if ch != ":":
                continue
            prefix = text[:i]
            if isinstance(font, fonts.TTFFont):
                shift = -min(0, font.font.getbbox(text)[0])
                cx = x + shift + font.font.getlength(prefix)
                b = font.font.getbbox(":")
                c.rect(round(cx + b[0]), y + b[1] - font.top, round(cx + b[2]) - 1, y + b[3] - font.top - 1, (0, 0, 0))
            else:
                cx = x + font.width(prefix)
                c.rect(cx, y, cx + font.width(":") - 1, y + font.height - 1, (0, 0, 0))

    def render(self, c, slide, t):
        now = datetime.now()
        cfg = self.cfg
        lines = self._lines(now)
        gap = int(cfg.get("line_gap", 2) or 0)
        ampm = ""
        if not cfg.get("format_24h") and cfg.get("show_ampm", True):
            ampm = now.strftime("%p")
            ampm = ampm.lower() if cfg.get("ampm_lowercase") else ampm
        ampm_color = hex_to_rgb(cfg.get("ampm_color"), lines[0][2]) if cfg.get("ampm_color") else lines[0][2]

        # resolve the time font: fixed, explicit size, or auto-fit into what the other lines leave
        tname = lines[0][1]
        other_h = sum(f.height for _, f, _, _ in lines[1:]) + gap * (len(lines) - 1)
        if tname.startswith("ttf:") and cfg.get("time_size") in (None, ""):
            tfont = self._fit_time(tname, c.height - other_h, self._template, ampm, c.width)
        else:
            tfont = self._font(tname, cfg.get("time_size"), "0123456789:", 16)
        lines[0] = (lines[0][0], tfont, lines[0][2], "time")
        ampm_font = self._ampm_font(tfont)

        # vertical layout: center the block of lines
        heights = [f.height for _, f, _, _ in lines]
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
                if kind == "time" and getattr(self, "_colon_off", False):
                    self._hide_colons(c, x, y, text, font)
                if suffix_w:
                    # AM/PM sits on the time's baseline
                    ay = y + font.ascent - ampm_font.ascent
                    c.text(x + w + 2, ay, ampm, ampm_font, ampm_color)
            y += hgt + gap
