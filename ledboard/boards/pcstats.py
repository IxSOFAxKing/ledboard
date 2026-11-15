"""Gaming PC stats from Home Assistant (e.g. HASS.Agent sensors): CPU / GPU load and temperature, memory,
network. Values follow Home Assistant live; four layouts (bars, gauges, graph, gaming)."""
from __future__ import annotations

import logging
import re
import time
from collections import deque

import requests

from .base import Board, organize
from ..gfx import GRAY, WHITE, scale

log = logging.getLogger(__name__)

GREEN = (60, 210, 90)
YELLOW = (255, 190, 40)
RED = (240, 50, 50)
CPU_COLOR = (80, 200, 255)
GPU_COLOR = (190, 110, 255)
DIM_TRACK = (35, 35, 42)
OFF_STATES = {"unavailable", "unknown", "none", ""}

METRICS = {   # key: (label, kind, search patterns for "find my sensors")
    "cpu_load": ("CPU", "load", [r"cpu.?load", r"cpu.?usage", r"processor.?(load|usage|time)"]),
    "cpu_temp": ("CPU", "temp", [r"cpu.?(package.?)?temp", r"(tctl|tdie|package).?temp", r"cpu.*temperature"]),
    "gpu_load": ("GPU", "load", [r"gpu.?(core.?)?load", r"gpu.?usage", r"gpu.?utili"]),
    "gpu_temp": ("GPU", "temp", [r"gpu.?(core.?)?temp", r"gpu.*temperature"]),
    "ram": ("RAM", "load", [r"memory.?(usage|load|used)", r"ram.?(usage|load|used)", r"used.?memory"]),
    "net_down": ("DOWN", "rate", [r"(download|received|receive|rx)(.?(rate|speed|bytes))?"]),
    "net_up": ("UP", "rate", [r"(upload|sent|send|tx)(.?(rate|speed|bytes))?"]),
}
LAYOUTS = {
    "bars": "Bars (a bar per stat with its value and temperature)",
    "gauges": "Gauges (four round dials: CPU, GPU, RAM, temperature)",
    "graph": "Graph (CPU and GPU load over the last 10 minutes, with the numbers)",
    "gaming": "Gaming (the game you're playing, with big GPU / temperature / CPU numbers)",
}
SHOW = {"online": "While the PC is on", "gaming": "Only while gaming", "always": "Always"}
HISTORY_SECONDS = 600


def level_color(value, kind):
    """Green / yellow / red for a load % or a temperature."""
    if value is None:
        return GRAY
    lo, hi = (65, 80) if kind == "temp" else (60, 85)
    return GREEN if value < lo else YELLOW if value < hi else RED


def fmt_rate(value, unit):
    """'12.3 MB/s' from a network rate in whatever unit the sensor reports."""
    if value is None:
        return "--"
    u = (unit or "").strip()
    factor = {"b/s": 1, "B/s": 1, "kB/s": 1e3, "KB/s": 1e3, "KiB/s": 1024, "MB/s": 1e6, "MiB/s": 1024 ** 2,
              "GB/s": 1e9, "bit/s": 0.125, "kbit/s": 125, "Kbps": 125, "Mbit/s": 125e3, "Mbps": 125e3,
              "Gbit/s": 125e6}.get(u)
    if factor is None:
        return f"{value:.0f}{u}" if value >= 10 else f"{value:.1f}{u}"
    b = value * factor
    for div, name in ((1e9, "GB/s"), (1e6, "MB/s"), (1e3, "KB/s")):
        if b >= div:
            v = b / div
            return f"{v:.1f}{name}" if v < 100 else f"{v:.0f}{name}"
    return f"{b:.0f}B/s"


class PCStatsBoard(Board):
    name = "pcstats"
    title = "Gaming PC"
    category = "Home & status"
    description = ("Your PC's CPU / GPU load and temperatures, memory and network, from Home Assistant "
                   "(e.g. HASS.Agent sensors), updated live.")
    refresh = 30
    DEFAULTS = {"enabled": False, "duration": 10, "refresh": 30, "layout": "bars", "show": "online",
                "takeover": False, "temp_unit": "auto", **{f"{k}_entity": "" for k in METRICS}}
    FIELDS = [
        {"key": "discover", "label": "Find my PC's sensors", "type": "pc_discover",
         "help": "Looks through Home Assistant for CPU / GPU / memory / network sensors and fills in the boxes "
                 "below (check them, then Save). Uses the Home Assistant board's URL and token."},
        *[{"key": f"{k}_entity", "label": label, "type": "text", "datalist": "ha-entities", "placeholder": "sensor.…"}
          for k, label in (("cpu_load", "CPU load (%)"), ("cpu_temp", "CPU temperature"), ("gpu_load", "GPU load (%)"),
                           ("gpu_temp", "GPU temperature"), ("ram", "Memory used (%)"),
                           ("net_down", "Network download rate"), ("net_up", "Network upload rate"))],
        {"key": "layout", "label": "Layout", "type": "select", "options": list(LAYOUTS), "option_labels": LAYOUTS},
        {"key": "show", "label": "Show the board", "type": "select", "options": list(SHOW), "option_labels": SHOW,
         "help": "'While the PC is on' hides it when the sensors go unavailable (PC off or asleep). 'While gaming' "
                 "uses the Steam board (including games seen through Home Assistant)."},
        {"key": "takeover", "label": "Take over the display while gaming", "type": "bool"},
        {"key": "temp_unit", "label": "Temperatures in", "type": "select", "options": ["auto", "c", "f"],
         "option_labels": {"auto": "Whatever the sensor reports", "c": "°C", "f": "°F"}},
        {"key": "duration", "label": "Seconds on screen", "type": "number"},
        {"key": "refresh", "label": "Check every (seconds)", "type": "number",
         "help": "Changes arrive live from Home Assistant; this is a backup."},
    ]
    FIELDS = organize(FIELDS, [
        ("Basics", "Sensors", ["discover"] + [f"{k}_entity" for k in METRICS]),
        ("Screens", "Display", ["layout", "show", "takeover"]),
        ("Look", "Units", ["temp_unit"]),
        ("Advanced", "Timing", ["duration", "refresh"]),
    ])

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.states = {}                     # entity_id -> HA state dict
        self.history = deque(maxlen=900)     # (time, cpu %, gpu %)
        self._ws = None

    # -- data -------------------------------------------------------------------
    def _ha(self):
        ha = self.app.boards.get("homeassistant")
        hcfg = ha.cfg if ha else {}
        base, token = (hcfg.get("url") or "").rstrip("/"), hcfg.get("token")
        return base, token, bool(hcfg.get("verify_ssl", True))

    def _entities(self):
        return {k: (self.cfg.get(f"{k}_entity") or "").strip() for k in METRICS if (self.cfg.get(f"{k}_entity") or "").strip()}

    def update(self):
        ents = self._entities()
        if not ents:
            self.states = {}
            return
        base, token, verify = self._ha()
        if not (base and token):
            raise RuntimeError("Set the Home Assistant URL and token on the Home Assistant board")
        self._ensure_live(base, token, verify, sorted(set(ents.values())))
        headers = {"Authorization": f"Bearer {token}"}
        states = {}
        for ent in set(ents.values()):
            r = requests.get(f"{base}/api/states/{ent}", headers=headers, timeout=10, verify=verify)
            if r.status_code == 404:
                raise RuntimeError(f"Home Assistant has no entity {ent}")
            r.raise_for_status()
            states[ent] = r.json()
        self.states.update(states)
        self._sample()

    def _ensure_live(self, base, token, verify, ents):
        from ..ha_ws import HAStateStream
        sig = (base, token, verify, tuple(ents))
        if self._ws and (self._ws.sig != sig or not self._ws.is_alive()):
            self._ws.stop()
            self._ws = None
        if self._ws is None:
            self._ws = HAStateStream(base, token, verify, ents, self._on_live,
                                     keep_running=lambda: self.enabled and bool(self._entities()), name="ha-live-pc")
            self._ws.sig = sig
            self._ws.start()

    def _on_live(self, ent, st):
        self.states[ent] = st
        self._sample()

    def value(self, key):
        """(number or None, unit) for a metric."""
        ent = (self.cfg.get(f"{key}_entity") or "").strip()
        st = self.states.get(ent) if ent else None
        if not st:
            return None, ""
        try:
            v = float(st.get("state"))
        except (TypeError, ValueError):
            return None, ""
        unit = (st.get("attributes") or {}).get("unit_of_measurement") or ""
        if METRICS[key][1] == "temp":
            want = self.cfg.get("temp_unit", "auto")
            is_f = "F" in unit
            if want == "c" and is_f:
                v, unit = (v - 32) * 5 / 9, "°C"
            elif want == "f" and not is_f:
                v, unit = v * 9 / 5 + 32, "°F"
        return v, unit

    def _temp_c(self, key):
        """A temperature in °C (for the colors), whatever unit it's shown in."""
        v, unit = self.value(key)
        if v is None:
            return None
        return (v - 32) * 5 / 9 if "F" in unit else v

    def _sample(self):
        now = time.time()
        cpu, gpu = self.value("cpu_load")[0], self.value("gpu_load")[0]
        if cpu is None and gpu is None:
            return
        if self.history and now - self.history[-1][0] < 1:
            self.history[-1] = (now, cpu, gpu)
        else:
            self.history.append((now, cpu, gpu))

    def online(self):
        """True while any configured sensor has a real value (the PC is on and HASS.Agent is running)."""
        for ent in self._entities().values():
            st = self.states.get(ent)
            if st and str(st.get("state", "")).lower() not in OFF_STATES:
                return True
        return False

    def gaming(self):
        """The game being played (from the Steam board), or None."""
        st = self.app.boards.get("steam")
        if not st or not st.enabled:
            return None
        for p in getattr(st, "players", {}).values():
            if p.get("appid"):
                return p.get("game") or "In game"
        return None

    # -- discovery ----------------------------------------------------------------
    def discover(self):
        """{metric: entity_id} guessed from Home Assistant's entities (HASS.Agent, LibreHardwareMonitor...)."""
        base, token, verify = self._ha()
        if not (base and token):
            raise RuntimeError("Set the Home Assistant URL and token on the Home Assistant board first")
        r = requests.get(f"{base}/api/states", headers={"Authorization": f"Bearer {token}"}, timeout=15, verify=verify)
        r.raise_for_status()
        cands = [s for s in r.json() if isinstance(s, dict) and str(s.get("entity_id", "")).startswith("sensor.")]
        found = {}
        for key, (_label, kind, pats) in METRICS.items():
            best = None
            for st in cands:
                ent = st["entity_id"]
                text = (ent + " " + str((st.get("attributes") or {}).get("friendly_name") or "")).lower()
                unit = str((st.get("attributes") or {}).get("unit_of_measurement") or "")
                if not any(re.search(p, text) for p in pats):
                    continue
                if kind == "temp" and "°" not in unit and "temp" not in text:
                    continue
                if kind == "load" and unit and unit != "%":
                    continue
                if kind == "rate" and unit and "/s" not in unit and "ps" not in unit:
                    continue
                score = (str(st.get("state", "")).lower() not in OFF_STATES, "hass" in text or "agent" in text, -len(ent))
                if best is None or score > best[0]:
                    best = (score, ent)
            if best:
                found[key] = best[1]
        return found

    # -- slides ---------------------------------------------------------------------
    def _wanted(self):
        if not self._entities():
            return False
        show = self.cfg.get("show", "online")
        if show == "gaming":
            return self.online() and self.gaming() is not None
        if show == "online":
            return self.online()
        return True

    def slides(self):
        return ["pc"] if self._wanted() else []

    def takeover_slides(self):
        return ["pc"] if self.cfg.get("takeover") and self.online() and self.gaming() else []

    def status(self):
        s = super().status()
        s["pc"] = {k: self.value(k)[0] for k in METRICS if self.cfg.get(f"{k}_entity")}
        s["pc_online"] = self.online()
        return s

    # -- drawing ---------------------------------------------------------------------
    def render(self, c, slide, t):
        lay = self.cfg.get("layout", "bars")
        {"gauges": self._render_gauges, "graph": self._render_graph, "gaming": self._render_gaming}.get(
            lay, self._render_bars)(c, t)

    def _deg(self, key):
        v, unit = self.value(key)
        return None if v is None else f"{v:.0f}°"

    def _render_bars(self, c, t):
        W = c.width
        rows = []
        for key, temp in (("cpu_load", "cpu_temp"), ("gpu_load", "gpu_temp"), ("ram", None)):
            if self.cfg.get(f"{key}_entity") or (temp and self.cfg.get(f"{temp}_entity")):
                rows.append((key, temp))
        net = self.cfg.get("net_down_entity") or self.cfg.get("net_up_entity")
        n = len(rows) + (1 if net else 0)
        if n == 0:
            return
        step = max(8, min(11, c.height // n))
        y = (c.height - step * n) // 2 + 1
        for key, temp in rows:
            label = METRICS[key][0]
            v, _u = self.value(key)
            col = level_color(v, "load")
            c.text(1, y, label, "4x6", scale(WHITE, 0.8))
            x0, x1 = 17, 77
            c.rect(x0, y + 1, x1, y + 4, DIM_TRACK)
            if v is not None:
                c.rect(x0, y + 1, x0 + round((x1 - x0) * max(0, min(100, v)) / 100), y + 4, col)
            c.text(81, y, "--" if v is None else f"{v:.0f}%", "4x6", col)
            if temp and self.cfg.get(f"{temp}_entity"):
                deg = self._deg(temp)
                c.text_right(W - 1, y, deg or "--", "4x6", level_color(self._temp_c(temp), "temp"))
            y += step
        if net:
            dv, du = self.value("net_down")
            uv, uu = self.value("net_up")
            c.text(1, y, "NET", "4x6", scale(WHITE, 0.8))
            c.text(17, y, "↓" + fmt_rate(dv, du), "4x6", CPU_COLOR)
            c.text_right(W - 1, y, "↑" + fmt_rate(uv, uu), "4x6", GPU_COLOR)

    def _gauge(self, c, cx, cy, r, frac, col, value_text, label):
        box = (cx - r, cy - r, cx + r, cy + r)
        c.draw.arc(box, 150, 390, fill=DIM_TRACK, width=3)
        if frac is not None and frac > 0:
            c.draw.arc(box, 150, 150 + 240 * max(0.0, min(1.0, frac)), fill=col, width=3)
        c.text_centered(cy - 3, value_text, "5x7", WHITE, cx - r, cx + r + 1)
        c.text_centered(c.height - 6, label, "4x6", scale(WHITE, 0.75), cx - r - 2, cx + r + 3)

    def _render_gauges(self, c, t):
        W = c.width
        temp_key = "gpu_temp" if self.cfg.get("gpu_temp_entity") else "cpu_temp"
        dials = [(k, METRICS[k][0]) for k in ("cpu_load", "gpu_load", "ram") if self.cfg.get(f"{k}_entity")]
        if self.cfg.get(f"{temp_key}_entity"):
            dials.append((temp_key, METRICS[temp_key][0] + "°"))
        if not dials:
            return
        cell = W // len(dials)
        for i, (key, label) in enumerate(dials):
            cx = i * cell + cell // 2
            v, _u = self.value(key)
            if METRICS[key][1] == "temp":
                tc = self._temp_c(key)
                frac = None if tc is None else (tc - 30) / 70
                col = level_color(tc, "temp")
                text = "--" if v is None else f"{v:.0f}"
            else:
                frac = None if v is None else v / 100
                col = level_color(v, "load")
                text = "--" if v is None else f"{v:.0f}"
            self._gauge(c, cx, 13, 11, frac, col, text, label)

    def _render_graph(self, c, t):
        W, H = c.width, c.height
        gx0, gx1, gy0, gy1 = 0, 83, 1, H - 2
        c.rect(gx0, gy0, gx1, gy1, (10, 10, 14))
        for f in (0.5,):
            y = gy1 - round((gy1 - gy0) * f)
            for x in range(gx0, gx1, 3):
                c.pixel(x, y, (30, 30, 36))
        now = time.time()
        pts = [p for p in self.history if now - p[0] <= HISTORY_SECONDS]
        for idx, col in ((1, CPU_COLOR), (2, GPU_COLOR)):
            prev = None
            for ts, *vals in pts:
                v = vals[idx - 1]
                if v is None:
                    prev = None
                    continue
                x = gx1 - round((now - ts) / HISTORY_SECONDS * (gx1 - gx0))
                y = gy1 - round((gy1 - gy0) * max(0, min(100, v)) / 100)
                if prev:
                    c.line(prev[0], prev[1], x, prev[1], col)      # step lines: a value holds until the next one
                    c.line(x, prev[1], x, y, col)
                prev = (x, y)
            if prev:
                c.line(prev[0], prev[1], gx1, prev[1], col)
        x0 = 87
        rows = [("CPU", "cpu_load", CPU_COLOR), ("GPU", "gpu_load", GPU_COLOR)]
        for i, (label, key, col) in enumerate(rows):
            v, _u = self.value(key)
            y = 1 + i * 8
            c.text(x0, y, label, "4x6", col)
            c.text_right(W - 1, y, "--" if v is None else f"{v:.0f}%", "4x6", level_color(v, "load"))
        # temperatures: CPU on the left, GPU on the right, each in its own warning color
        ct, gt = self._deg("cpu_temp"), self._deg("gpu_temp")
        if ct:
            c.text(x0, 17, ct, "4x6", level_color(self._temp_c("cpu_temp"), "temp"))
        if gt:
            c.text_right(W - 1, 17, gt, "4x6", level_color(self._temp_c("gpu_temp"), "temp"))
        rv, _u = self.value("ram")
        if rv is not None:
            c.text(x0, 25, "RAM", "4x6", scale(WHITE, 0.7))
            c.text_right(W - 1, 25, f"{rv:.0f}%", "4x6", level_color(rv, "load"))

    def _render_gaming(self, c, t):
        W, H = c.width, c.height
        game = self.gaming() or "GAMING PC"
        c.rect(0, 0, W - 1, 8, (20, 20, 28))
        c.scroll_text(2, W - 2, 1, game.upper(), "5x7", WHITE, t)
        cells = [("GPU", self.value("gpu_load")[0], "%", "load", GPU_COLOR),
                 ("GPU", self._deg("gpu_temp"), "", "temp", GPU_COLOR),
                 ("CPU", self.value("cpu_load")[0], "%", "load", CPU_COLOR)]
        if self.value("gpu_temp")[0] is None and self.value("cpu_temp")[0] is not None:
            cells[1] = ("CPU", self._deg("cpu_temp"), "", "temp", CPU_COLOR)
        cw = W // 3
        for i, (label, v, suffix, kind, lcol) in enumerate(cells):
            x0, x1 = i * cw, (i + 1) * cw
            if kind == "temp":
                key = "gpu_temp" if label == "GPU" else "cpu_temp"
                text, col = (v or "--"), level_color(self._temp_c(key), "temp")
            else:
                text, col = ("--" if v is None else f"{v:.0f}{suffix}"), level_color(v, "load")
            c.text_centered(11, text, "7x13B" if c.text_width(text, "7x13B") <= cw - 2 else "6x10", col, x0, x1)
            c.text_centered(H - 6, label + ("°" if kind == "temp" else ""), "4x6", lcol, x0, x1)
            if i:
                c.line(x0, 12, x0, H - 3, (35, 35, 42))
