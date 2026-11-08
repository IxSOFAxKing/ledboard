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
    """Scalable fonts first (sized by the Size setting), then the fixed-size pixel fonts."""
    avail = fonts.available()
    bdf = [f for f in _FONT_NAMES if f in avail] + [f for f in avail if f not in _FONT_NAMES]
    ttf = [f"ttf:{k}" for k in fonts.SCALABLE]
    labels = {f"ttf:{k}": f"{v[0]} (scalable)" for k, v in fonts.SCALABLE.items()}
    labels.update({f: f"{_FONT_NAMES.get(f, f)} (fixed size)" for f in bdf})
    return ttf + bdf, labels


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
        "time_font": "7x13B", "time_size": "", "color": "#00a0ff", "smooth": False,
        # am/pm
        "show_ampm": True, "ampm_font": "5x8", "ampm_size": "", "ampm_color": "", "ampm_lowercase": False,
        # date
        "show_date": True, "date_format": "%a %b %-d", "date_custom": "%a %-d %b", "date_uppercase": True,
        "date_font": "6x10", "date_size": "", "date_color": "#888888",
        # extra line
        "extra_text": "", "extra_font": "4x6", "extra_size": "", "extra_color": "#ff9900",
        # layout
        "align": "center", "line_gap": 2,
    }
    FIELDS = [
        {"key": "time_font", "label": "Time font / size", "type": "select", "section": "Time",
         "options": FONTS, "option_labels": FONT_LABELS},
        {"key": "time_size", "label": "Time size (pixels)", "type": "number", "section": "Time", "placeholder": "auto",
         "help": "For scalable fonts. Blank = as big as fits with the other lines."},
        {"key": "color", "label": "Time color", "type": "color", "section": "Time"},
        {"key": "format_24h", "label": "24-hour time", "type": "bool", "section": "Time"},
        {"key": "show_seconds", "label": "Show seconds", "type": "bool", "section": "Time"},
        {"key": "leading_zero", "label": "Leading zero (09:05 instead of 9:05)", "type": "bool", "section": "Time"},
        {"key": "blink_colon", "label": "Blink the colon", "type": "bool", "section": "Time"},
        {"key": "show_ampm", "label": "Show AM/PM", "type": "bool", "section": "AM / PM", "show_if": {"format_24h": False}},
        {"key": "ampm_font", "label": "AM/PM font / size", "type": "select", "section": "AM / PM",
         "options": FONTS, "option_labels": FONT_LABELS, "show_if": {"format_24h": False, "show_ampm": True}},
        {"key": "ampm_size", "label": "AM/PM size (pixels)", "type": "number", "section": "AM / PM", "placeholder": "auto",
         "show_if": {"format_24h": False, "show_ampm": True}, "help": "Pixels tall, for scalable fonts only. Blank = automatic."},
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
        {"key": "date_size", "label": "Date size (pixels)", "type": "number", "section": "Date", "placeholder": "auto",
         "show_if": {"show_date": True}, "help": "Pixels tall, for scalable fonts only. Blank = automatic."},
        {"key": "date_color", "label": "Date color", "type": "color", "section": "Date", "show_if": {"show_date": True}},
        {"key": "extra_text", "label": "Extra line (optional)", "type": "text", "section": "Extra line",
         "help": "Any text under the date, e.g. 'Go Bills!'. Can include date codes like %A."},
        {"key": "extra_font", "label": "Extra line font / size", "type": "select", "section": "Extra line",
         "options": FONTS, "option_labels": FONT_LABELS},
        {"key": "extra_size", "label": "Extra line size (pixels)", "type": "number", "section": "Extra line",
         "placeholder": "auto", "help": "Pixels tall, for scalable fonts only. Blank = automatic."},
        {"key": "extra_color", "label": "Extra line color", "type": "color", "section": "Extra line"},
        {"key": "smooth", "label": "Smooth edges on scalable fonts (anti-aliasing)", "type": "bool", "section": "Layout",
         "help": "Off = crisp on/off pixels. On = softer curves using dimmer edge pixels."},
        {"key": "align", "label": "Alignment", "type": "select", "section": "Layout",
         "options": ["center", "left", "right"]},
        {"key": "line_gap", "label": "Space between lines (pixels)", "type": "number", "section": "Layout"},
        {"key": "duration", "label": "Seconds on screen", "type": "number", "section": "Layout"},
    ]

    def _font(self, name, size, ref, default_size):
        """Font object for a setting: fixed-size BDF names pass through; 'ttf:<key>' gets a size."""
        name = name or "7x13B"
        if not name.startswith("ttf:"):
            return fonts.get(name)
        key = name[4:]
        if key not in fonts.SCALABLE:
            return fonts.get("7x13B")
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
        return self._font(self.cfg.get("ampm_font") or "5x8", self.cfg.get("ampm_size"), "AMP",
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
        lines = [(txt, cfg.get("time_font") or "7x13B", time_color, "time")]
        self._template = ("00" if (h24 or cfg.get("leading_zero")) else "10") + ":00" + (":00" if secs else "")
        if cfg.get("show_date", True):
            pattern = cfg.get("date_custom") if cfg.get("date_format") == "custom" else cfg.get("date_format")
            try:
                d = now.strftime(pattern or "%a %b %-d")
            except ValueError:
                d = now.strftime("%a %b %-d")
            if cfg.get("date_uppercase", True):
                d = d.upper()
            lines.append((d, self._font(cfg.get("date_font") or "6x10", cfg.get("date_size"), "Ay0g", 8),
                          hex_to_rgb(cfg.get("date_color"), GRAY), "date"))
        extra = (cfg.get("extra_text") or "").strip()
        if extra:
            try:
                extra = now.strftime(extra)
            except ValueError:
                pass
            lines.append((extra, self._font(cfg.get("extra_font") or "4x6", cfg.get("extra_size"), "Ay0g", 7),
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
