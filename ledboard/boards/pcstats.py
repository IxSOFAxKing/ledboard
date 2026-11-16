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
    "meters": "Meters (LED level meters with peak markers, like an equalizer)",
    "split": "Split (GPU and CPU panels side by side, each with a mini graph)",
    "spotlight": "Spotlight (one stat at a time in huge numbers, switching every few seconds)",
    "thermo": "Thermometers (CPU and GPU temperatures as thermometers)",
    "details": "Details (power, fans, clocks, GPU memory; needs LibreHardwareMonitor)",
}
LHM_LABELS = {
    "cpu_load": "CPU load", "cpu_temp": "CPU temperature", "gpu_load": "GPU load", "gpu_temp": "GPU temperature",
    "ram": "Memory used", "net_down": "Network download", "net_up": "Network upload",
    "gpu_power": "GPU power", "cpu_power": "CPU power", "gpu_fan": "GPU fan", "gpu_clock": "GPU clock",
    "cpu_clock": "CPU clock", "vram_used": "GPU memory used", "vram_total": "GPU memory total",
}
LHM_KEYS = list(LHM_LABELS)
PEAK_HOLD = 3.0            # seconds a meter's peak marker holds before it falls
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
                "takeover": False, "temp_unit": "auto", "source": "homeassistant", "lhm_url": "", "lhm_refresh": 3,
                **{f"{k}_entity": "" for k in METRICS}, **{f"lhm_{k}": "" for k in LHM_KEYS}}
    FIELDS = [
        {"key": "source", "label": "Get the stats from", "type": "select", "options": ["homeassistant", "lhm"],
         "option_labels": {"homeassistant": "Home Assistant (e.g. HASS.Agent sensors)",
                           "lhm": "LibreHardwareMonitor (straight from the PC)"}},
        {"key": "lhm_url", "label": "LibreHardwareMonitor address", "type": "text",
         "placeholder": "http://192.168.1.50:8085", "show_if": {"source": "lhm"},
         "help": "In LibreHardwareMonitor: Options → Remote Web Server → Run (port 8085), and allow it through the "
                 "Windows firewall when asked. Run LibreHardwareMonitor as administrator for CPU temperatures."},
        *[{"key": f"lhm_{k}", "label": label, "type": "text", "datalist": "lhm-sensors", "placeholder": "automatic",
           "show_if": {"source": "lhm"}} for k, label in LHM_LABELS.items()],
        {"key": "lhm_refresh", "label": "Read LibreHardwareMonitor every (seconds)", "type": "number",
         "show_if": {"source": "lhm"}},
        {"key": "discover", "label": "Find my PC's sensors", "type": "pc_discover",
         "help": "Looks through Home Assistant for CPU / GPU / memory / network sensors and fills in the boxes "
                 "below (check them, then Save). Uses the Home Assistant board's URL and token."},
        *[{"key": f"{k}_entity", "label": label, "type": "text", "datalist": "ha-entities", "placeholder": "sensor.…",
           "show_if": {"source": "homeassistant"}}
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
        ("Basics", "Sensors", ["source", "lhm_url", "discover"] + [f"{k}_entity" for k in METRICS]
         + [f"lhm_{k}" for k in LHM_KEYS]),
        ("Screens", "Display", ["layout", "show", "takeover"]),
        ("Look", "Units", ["temp_unit"]),
        ("Advanced", "Timing", ["duration", "refresh", "lhm_refresh"]),
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

    def _lhm_on(self):
        return self.cfg.get("source", "homeassistant") == "lhm"

    def has(self, key):
        """Is this stat set up (and, for LibreHardwareMonitor, found)?"""
        if self._lhm_on():
            return key in getattr(self, "lhm_values", {})
        return bool((self.cfg.get(f"{key}_entity") or "").strip())

    def interval(self):
        if self._lhm_on():
            try:
                return max(1.0, float(self.cfg.get("lhm_refresh") or 3))
            except (TypeError, ValueError):
                return 3.0
        return super().interval()

    def _lhm_url(self, url=None):
        u = (url if url is not None else (self.cfg.get("lhm_url") or "")).strip().rstrip("/")
        if u and "://" not in u:
            u = "http://" + u
        if u and not u.endswith("/data.json"):
            u += "/data.json"
        return u

    def _lhm_read(self, url=None):
        """All of LibreHardwareMonitor's sensors (flattened)."""
        from .lhm import flatten
        url = self._lhm_url(url)
        if not url:
            raise RuntimeError("Enter LibreHardwareMonitor's address (e.g. http://192.168.1.50:8085)")
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return flatten(r.json())

    def _update_lhm(self):
        from .lhm import auto_pick, find
        if self._ws:
            self._ws.stop()
            self._ws = None
        try:
            sensors = self._lhm_read()
        except Exception as e:
            self.lhm_error = str(e) if isinstance(e, RuntimeError) else e.__class__.__name__
            if time.time() - getattr(self, "lhm_ok_at", 0) > 30:
                self.lhm_values = {}                    # PC off / asleep: hide the board
            raise
        self.lhm_error = None
        self.lhm_ok_at = time.time()
        picks = auto_pick(sensors)
        values = {}
        for key in LHM_KEYS:
            want = (self.cfg.get(f"lhm_{key}") or "").strip()
            s = find(sensors, want) if want else picks.get(key)
            if s and s["value"] is not None:
                values[key] = (s["value"], s["unit"], s["name"])
        self.lhm_values = values
        self._sample()

    def update(self):
        if self._lhm_on():
            return self._update_lhm()
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
        if self._lhm_on():
            got = getattr(self, "lhm_values", {}).get(key)
            if not got:
                return None, ""
            v, unit = got[0], got[1]
        else:
            ent = (self.cfg.get(f"{key}_entity") or "").strip()
            st = self.states.get(ent) if ent else None
            if not st:
                return None, ""
            try:
                v = float(st.get("state"))
            except (TypeError, ValueError):
                return None, ""
            unit = (st.get("attributes") or {}).get("unit_of_measurement") or ""
        if METRICS.get(key, ("", ""))[1] == "temp":
            celsius = (v - 32) * 5 / 9 if "F" in unit else v
            if celsius <= 1 or celsius > 150:
                # HASS.Agent reports 0 when it can't read the sensor (e.g. that GPU isn't supported): no reading
                return None, ""
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
        peaks = self.__dict__.setdefault("_peaks", {})
        for key in ("cpu_load", "gpu_load", "ram", "cpu_temp", "gpu_temp"):
            f = self._frac(key)                         # where the meter is (0..1), whatever the units
            if f is not None and (key not in peaks or f >= self._peak_frac(key)):
                peaks[key] = (f, now)
        cpu, gpu = self.value("cpu_load")[0], self.value("gpu_load")[0]
        if cpu is None and gpu is None:
            return
        if self.history and now - self.history[-1][0] < 1:
            self.history[-1] = (now, cpu, gpu)
        else:
            self.history.append((now, cpu, gpu))

    def online(self):
        """True while any configured sensor has a real value (the PC is on and HASS.Agent is running)."""
        if self._lhm_on():
            return bool(getattr(self, "lhm_values", {})) and time.time() - getattr(self, "lhm_ok_at", 0) < 30
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
    def discover(self, source=None, lhm_url=None):
        """{setting: value} for the sensor boxes: Home Assistant entities guessed from their names, or (with
        LibreHardwareMonitor) the sensors it would pick automatically."""
        if (source or self.cfg.get("source", "homeassistant")) == "lhm":
            from .lhm import auto_pick
            picks = auto_pick(self._lhm_read(lhm_url))
            return {f"lhm_{k}": s["id"] for k, s in picks.items()}
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
                found[f"{key}_entity"] = best[1]
        return found

    def lhm_sensor_list(self, url=None):
        """[{"id", "label"}] of every LibreHardwareMonitor sensor (for the setting boxes' suggestions)."""
        out = []
        for s in self._lhm_read(url):
            val = "" if s["value"] is None else f"{s['value']:g} {s['unit']}".strip()
            out.append({"id": s["id"], "label": f"{s['hw']} · {s['group']} · {s['name']}" + (f" ({val})" if val else "")})
        return out

    # -- slides ---------------------------------------------------------------------
    def _wanted(self):
        if not (self._lhm_on() or self._entities()):
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
        s["pc"] = {k: self.value(k)[0] for k in METRICS if self.has(k)}
        s["pc_online"] = self.online()
        if self._lhm_on():
            s["pc_source"] = "LibreHardwareMonitor" + (f" ({self.lhm_error})" if getattr(self, "lhm_error", None) else "")
        notes = []
        for key in ("cpu_temp", "gpu_temp"):
            if self._lhm_on():
                continue
            ent = (self.cfg.get(f"{key}_entity") or "").strip()
            st = self.states.get(ent) if ent else None
            if st and self.value(key)[0] is None and str(st.get("state", "")).lower() not in OFF_STATES:
                notes.append(f"{METRICS[key][0]} temperature sensor reads {st.get('state')} (no real reading)")
        if notes:
            s["pc_note"] = "; ".join(notes)
        return s

    # -- drawing ---------------------------------------------------------------------
    def render(self, c, slide, t):
        lay = self.cfg.get("layout", "bars")
        {"gauges": self._render_gauges, "graph": self._render_graph, "gaming": self._render_gaming,
         "meters": self._render_meters, "split": self._render_split, "spotlight": self._render_spotlight,
         "thermo": self._render_thermo, "details": self._render_details}.get(lay, self._render_bars)(c, t)

    def _deg(self, key):
        v, unit = self.value(key)
        return None if v is None else f"{v:.0f}°"

    def _render_bars(self, c, t):
        W = c.width
        rows = []
        for key, temp in (("cpu_load", "cpu_temp"), ("gpu_load", "gpu_temp"), ("ram", None)):
            if self.has(key) or (temp and self.has(temp)):
                rows.append((key, temp))
        net = self.has("net_down") or self.has("net_up")
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
            if temp and self.has(temp):
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
        temp_key = "gpu_temp" if self.has("gpu_temp") else "cpu_temp"
        dials = [(k, METRICS[k][0]) for k in ("cpu_load", "gpu_load", "ram") if self.has(k)]
        if self.has(temp_key):
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

    # -- more layouts ------------------------------------------------------------------
    def _frac(self, key):
        """0..1 for a metric: loads as %, temperatures from 30 to 100 °C."""
        if METRICS[key][1] == "temp":
            tc = self._temp_c(key)
            return None if tc is None else max(0.0, min(1.0, (tc - 30) / 70))
        v = self.value(key)[0]
        return None if v is None else max(0.0, min(1.0, v / 100))

    def _peak_frac(self, key):
        """The meter's peak marker: the highest recent value, falling after PEAK_HOLD seconds."""
        pk = self.__dict__.get("_peaks", {}).get(key)
        if not pk:
            return None
        f, at = pk
        age = time.time() - at
        if age > PEAK_HOLD:
            f -= (age - PEAK_HOLD) * 0.25                  # falls a quarter of the meter per second
        cur = self._frac(key) or 0
        return max(cur, min(1.0, f))

    def _render_meters(self, c, t):
        W, H = c.width, c.height
        temp_key = "gpu_temp" if self.has("gpu_temp") else "cpu_temp"
        cols = [k for k in ("cpu_load", "gpu_load", "ram") if self.has(k)]
        if self.has(temp_key):
            cols.append(temp_key)
        if not cols:
            return
        cell = W // len(cols)
        seg_n, seg_h, gap = 8, 2, 1
        top = H - 8 - seg_n * (seg_h + gap) + gap
        for i, key in enumerate(cols):
            x = i * cell + 3
            bw = 9
            frac, peak = self._frac(key), self._peak_frac(key)
            lit = 0 if frac is None else round(frac * seg_n)
            for sgi in range(seg_n):
                y1 = H - 9 - sgi * (seg_h + gap)
                pos = (sgi + 0.5) / seg_n
                col = GREEN if pos < 0.6 else YELLOW if pos < 0.85 else RED
                c.rect(x, y1 - seg_h + 1, x + bw - 1, y1, col if sgi < lit else scale(col, 0.12))
            if peak is not None and round(peak * seg_n) > lit:     # only when the recent peak is above the level now
                pi = min(seg_n - 1, max(0, round(peak * seg_n) - 1))
                y1 = H - 9 - pi * (seg_h + gap)
                c.rect(x, y1 - seg_h + 1, x + bw - 1, y1, WHITE)
            v, _u = self.value(key)
            temp = METRICS[key][1] == "temp"
            text = "--" if v is None else (f"{v:.0f}°" if temp else f"{v:.0f}")
            col = level_color(self._temp_c(key) if temp else v, "temp" if temp else "load")
            c.text(x + bw + 2, top + 2, text, "5x7", col)
            c.text(x + bw + 2, top + 11, "%" if not temp else ("C" if "C" in (self.value(key)[1] or "°C") else "F"),
                   "4x6", scale(WHITE, 0.5))
            c.text_centered(H - 6, METRICS[key][0] + ("°" if temp else ""), "4x6", scale(WHITE, 0.75),
                            i * cell, (i + 1) * cell)

    def _spark(self, c, x0, x1, y0, y1, idx, col):
        """A small step graph of the last few minutes of CPU (idx 1) or GPU (idx 2) load."""
        now = time.time()
        span = 300
        pts = [(ts, vals[idx - 1]) for ts, *vals in self.history if now - ts <= span and vals[idx - 1] is not None]
        prev = None
        for ts, v in pts:
            x = x1 - round((now - ts) / span * (x1 - x0))
            y = y1 - round((y1 - y0) * max(0, min(100, v)) / 100)
            if prev:
                c.line(prev[0], prev[1], x, prev[1], col)
                c.line(x, prev[1], x, y, col)
            prev = (x, y)
        if prev:
            c.line(prev[0], prev[1], x1, prev[1], col)

    def _render_split(self, c, t):
        W, H = c.width, c.height
        mid = W // 2
        panels = (("GPU", "gpu_load", "gpu_temp", GPU_COLOR, 2, 0), ("CPU", "cpu_load", "cpu_temp", CPU_COLOR, 1, mid + 1))
        for label, load, temp, col, idx, x0 in panels:
            x1 = x0 + mid - 2
            c.rect(x0, 0, x0 + 1, H - 1, scale(col, 0.7))
            c.text(x0 + 4, 0, label, "5x7", col)
            deg = self._deg(temp)
            if deg:
                c.text_right(x1, 0, deg, "5x7", level_color(self._temp_c(temp), "temp"))
            v, _u = self.value(load)
            text = "--" if v is None else f"{v:.0f}%"
            c.text(x0 + 4, 9, text, "7x13B", level_color(v, "load"))
            c.rect(x0 + 4, H - 7, x1, H - 1, (12, 12, 16))
            self._spark(c, x0 + 4, x1, H - 7, H - 1, idx, scale(col, 0.9))
        c.line(mid, 0, mid, H - 1, (30, 30, 36))

    def _spot_items(self):
        out = []
        for key, label in (("gpu_load", "GPU LOAD"), ("gpu_temp", "GPU TEMP"), ("cpu_load", "CPU LOAD"),
                           ("cpu_temp", "CPU TEMP"), ("ram", "MEMORY")):
            if not self.has(key):
                continue
            v, unit = self.value(key)
            temp = METRICS[key][1] == "temp"
            big = "--" if v is None else (f"{v:.0f}°" if temp else f"{v:.0f}%")
            col = level_color(self._temp_c(key) if temp else v, "temp" if temp else "load")
            out.append((label, big, col, self._frac(key), GPU_COLOR if key.startswith("gpu") else
                        CPU_COLOR if key.startswith("cpu") else (200, 200, 200)))
        if self.has("net_down") or self.has("net_up"):
            dv, du = self.value("net_down")
            uv, uu = self.value("net_up")
            out.append(("NETWORK", "↓" + fmt_rate(dv, du), CPU_COLOR, None, (200, 200, 200)))
            out.append(("NETWORK", "↑" + fmt_rate(uv, uu), GPU_COLOR, None, (200, 200, 200)))
        return out

    def _render_spotlight(self, c, t):
        W, H = c.width, c.height
        items = self._spot_items()
        if not items:
            return
        i = int(t // 3) % len(items)
        label, big, col, frac, lcol = items[i]
        font = "10x20" if c.text_width(big, "10x20") <= 68 else "7x13B"
        c.text(2, (H - (20 if font == "10x20" else 13)) // 2, big, font, col)
        x0 = 74
        c.text(x0, 3, label.split()[0], "6x10", lcol)
        if len(label.split()) > 1:
            c.text(x0, 13, label.split()[1], "5x7", scale(WHITE, 0.7))
        if frac is not None:
            c.rect(x0, H - 6, W - 3, H - 4, DIM_TRACK)
            c.rect(x0, H - 6, x0 + round((W - 3 - x0) * frac), H - 4, col)
        for k in range(len(items)):                       # page dots
            c.pixel(W - 2 - (len(items) - 1 - k) * 2, 0, WHITE if k == i else (50, 50, 56))

    def _thermo(self, c, y, label, key, lcol):
        W = c.width
        tc = self._temp_c(key)
        col = level_color(tc, "temp")
        bx, by, r = 6, y + 6, 5
        x0, x1 = bx + 3, 78
        c.draw.rounded_rectangle((x0, by - 3, x1, by + 3), radius=3, outline=(80, 80, 90), fill=(14, 14, 18))
        c.draw.ellipse((bx - r, by - r, bx + r, by + r), outline=(80, 80, 90), fill=col if tc is not None else (30, 30, 36))
        if tc is not None:
            fill = x0 + round((x1 - x0 - 2) * max(0.0, min(1.0, (tc - 20) / 80)))
            c.rect(x0, by - 1, fill, by + 1, col)
        for f in (0.25, 0.5, 0.75):
            tx = x0 + round((x1 - x0) * f)
            c.pixel(tx, by - 4, (70, 70, 80))
        c.text(83, y + 3, label, "4x6", lcol)
        c.text_right(W - 1, y + 1, self._deg(key) or "--", "6x10", col)

    def _render_thermo(self, c, t):
        rows = [(lbl, k, col) for lbl, k, col in (("CPU", "cpu_temp", CPU_COLOR), ("GPU", "gpu_temp", GPU_COLOR))
                if self.has(k)]
        if not rows:
            return
        ys = [(c.height - 13) // 2] if len(rows) == 1 else [1, 17]
        for (lbl, k, col), y in zip(rows, ys):
            self._thermo(c, y, lbl, k, col)

    def _detail_items(self):
        """[(label, value text, color)] for the extra LibreHardwareMonitor readings."""
        out = []

        def num(key):
            got = getattr(self, "lhm_values", {}).get(key) if self._lhm_on() else None
            return (got[0], got[1]) if got else (None, "")

        def clock(v, unit):
            if v is None:
                return None
            mhz = v / 1000 if "kHz" in unit else v * 1000 if "GHz" in unit else v
            return f"{mhz / 1000:.2f} GHz" if mhz >= 1000 else f"{mhz:.0f} MHz"
        v, u = num("gpu_power")
        if v is not None:
            out.append(("GPU POWER", f"{v:.0f} W", GPU_COLOR))
        v, u = num("cpu_power")
        if v is not None:
            out.append(("CPU POWER", f"{v:.0f} W", CPU_COLOR))
        v, u = num("gpu_fan")
        if v is not None:
            out.append(("GPU FAN", f"{v:.0f} RPM" if "RPM" in u or not u else f"{v:.0f}{u}", GPU_COLOR))
        used, uu = num("vram_used")
        total, tu = num("vram_total")
        if used is not None:
            to_gb = (lambda x, unit: x / 1024 if "MB" in unit else x)
            text = f"{to_gb(used, uu):.1f}" + (f"/{to_gb(total, tu):.0f} GB" if total else " GB")
            out.append(("VRAM", text, GPU_COLOR))
        c_ = clock(*num("gpu_clock"))
        if c_:
            out.append(("GPU CLOCK", c_, GPU_COLOR))
        c_ = clock(*num("cpu_clock"))
        if c_:
            out.append(("CPU CLOCK", c_, CPU_COLOR))
        return out

    def _render_details(self, c, t):
        W, H = c.width, c.height
        items = self._detail_items()
        if not items:
            return self._render_bars(c, t)              # Home Assistant source: no extras, show the bars
        per = 4
        pages = (len(items) + per - 1) // per
        page = int(t // 4) % pages
        for i, (label, text, col) in enumerate(items[page * per:(page + 1) * per]):
            y = i * 8
            c.text(1, y + 1, label, "4x6", col)
            c.text_right(W - (3 if pages > 1 else 1), y, text, "5x7", WHITE)
        if pages > 1:
            for k in range(pages):
                c.pixel(W - 1, H - 1 - (pages - 1 - k) * 2, WHITE if k == page else (50, 50, 56))
