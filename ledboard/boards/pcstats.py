"""Gaming PC stats from Home Assistant (e.g. HASS.Agent sensors): CPU / GPU load and temperature, memory,
network. Values follow Home Assistant live; four layouts (bars, gauges, graph, gaming)."""
from __future__ import annotations

import logging
import re
import time
from collections import deque

import requests

from .base import Board, organize
from ..gfx import GRAY, WHITE, hex_to_rgb, scale

log = logging.getLogger(__name__)

GREEN = (60, 210, 90)
YELLOW = (255, 190, 40)
RED = (240, 50, 50)
CPU_COLOR = (80, 200, 255)
GPU_COLOR = (190, 110, 255)
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
    "graph": "Graph (the lines, with the numbers on the right)",
    "wide": "Wide graph (the lines across the whole board, numbers small in the corners)",
    "area": "Area graph (each line filled in underneath, numbers on the right)",
    "lanes": "Lanes (each stat in its own strip, with its number)",
}
# stat -> (short label on the board, name in the settings, kind, fixed range or None = scale to the data)
STATS = {
    "gpu_load": ("GPU", "GPU load", "pct", (0, 100)),
    "cpu_load": ("CPU", "CPU load", "pct", (0, 100)),
    "ram": ("RAM", "Memory used", "pct", (0, 100)),
    "vram": ("VRAM", "GPU memory used (LibreHardwareMonitor)", "pct", (0, 100)),
    "gpu_temp": ("GPU°", "GPU temperature", "temp", (30, 100)),
    "cpu_temp": ("CPU°", "CPU temperature", "temp", (30, 100)),
    "power": ("PWR", "Total power, GPU + CPU (LibreHardwareMonitor)", "watts", None),
    "gpu_power": ("GPUW", "GPU power (LibreHardwareMonitor)", "watts", None),
    "cpu_power": ("CPUW", "CPU power (LibreHardwareMonitor)", "watts", None),
    "gpu_fan": ("FAN", "GPU fan speed (LibreHardwareMonitor)", "rpm", None),
    "gpu_clock": ("GCLK", "GPU clock (LibreHardwareMonitor)", "ghz", None),
    "cpu_clock": ("CCLK", "CPU clock (LibreHardwareMonitor)", "ghz", None),
    "net_down": ("DOWN", "Network download", "rate", None),
    "net_up": ("UP", "Network upload", "rate", None),
}
LINES = 4
LINE_DEFAULTS = [("gpu_load", "#be6eff"), ("cpu_load", "#50c8ff"), ("none", "#3cd25a"), ("none", "#ffbe28")]
GRAPH_SPANS = {2: "2 minutes", 5: "5 minutes", 10: "10 minutes", 30: "30 minutes", 60: "1 hour"}
LHM_LABELS = {
    "cpu_load": "CPU load", "cpu_temp": "CPU temperature", "gpu_load": "GPU load", "gpu_temp": "GPU temperature",
    "ram": "Memory used", "net_down": "Network download", "net_up": "Network upload",
    "gpu_power": "GPU power", "cpu_power": "CPU power", "gpu_fan": "GPU fan", "gpu_clock": "GPU clock",
    "cpu_clock": "CPU clock", "vram_used": "GPU memory used", "vram_total": "GPU memory total",
}
LHM_KEYS = list(LHM_LABELS)
SHOW = {"online": "While the PC is on", "gaming": "Only while gaming", "always": "Always"}


def level_color(value, kind):
    """Green / yellow / red for a load % or a temperature."""
    if value is None:
        return GRAY
    lo, hi = (65, 80) if kind == "temp" else (60, 85)
    return GREEN if value < lo else YELLOW if value < hi else RED


RATE_FACTORS = {"b/s": 1, "B/s": 1, "kB/s": 1e3, "KB/s": 1e3, "KiB/s": 1024, "MB/s": 1e6, "MiB/s": 1024 ** 2,
                "GB/s": 1e9, "bit/s": 0.125, "kbit/s": 125, "Kbps": 125, "Mbit/s": 125e3, "Mbps": 125e3,
                "Gbit/s": 125e6}


def to_bytes(value, unit):
    """A network rate in bytes/s, or None."""
    if value is None:
        return None
    return value * RATE_FACTORS.get((unit or "").strip(), 1)


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
    description = ("A live graph of your PC's stats (load, temperatures, memory, power, fans, clocks, network), "
                   "from LibreHardwareMonitor or Home Assistant. You pick the lines and their colors.")
    refresh = 30
    DEFAULTS = {"enabled": False, "duration": 10, "refresh": 30, "layout": "graph", "show": "online",
                "takeover": False, "temp_unit": "auto", "source": "homeassistant", "lhm_url": "", "lhm_refresh": 3, "graph_minutes": 10,
                **{f"{k}_entity": "" for k in METRICS}, **{f"lhm_{k}": "" for k in LHM_KEYS},
                **{f"line{n + 1}": st for n, (st, _c) in enumerate(LINE_DEFAULTS)},
                **{f"line{n + 1}_color": col for n, (_s, col) in enumerate(LINE_DEFAULTS)}}
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
        *[f for n in range(1, LINES + 1) for f in (
            {"key": f"line{n}", "label": f"Line {n}", "type": "select", "options": ["none"] + list(STATS),
             "option_labels": {"none": "(not used)", **{k: v[1] for k, v in STATS.items()}}},
            {"key": f"line{n}_color", "label": f"Line {n} color", "type": "color"})],
        {"key": "show", "label": "Show the board", "type": "select", "options": list(SHOW), "option_labels": SHOW,
         "help": "'While the PC is on' hides it when the sensors go unavailable (PC off or asleep). 'While gaming' "
                 "uses the Steam board (including games seen through Home Assistant)."},
        {"key": "takeover", "label": "Take over the display while gaming", "type": "bool"},
        {"key": "graph_minutes", "label": "Graph time span", "type": "select", "options": list(GRAPH_SPANS),
         "option_labels": GRAPH_SPANS, "help": "How far back the graph looks. History starts when LEDBoard starts."},
        {"key": "temp_unit", "label": "Temperatures in", "type": "select", "options": ["auto", "c", "f"],
         "option_labels": {"auto": "Whatever the sensor reports", "c": "°C", "f": "°F"}},
        {"key": "duration", "label": "Seconds on screen", "type": "number"},
        {"key": "refresh", "label": "Check every (seconds)", "type": "number",
         "help": "Changes arrive live from Home Assistant; this is a backup."},
    ]
    FIELDS = organize(FIELDS, [
        ("Basics", "Sensors", ["source", "lhm_url", "discover"] + [f"{k}_entity" for k in METRICS]
         + [f"lhm_{k}" for k in LHM_KEYS]),
        ("Screens", "Graph", ["layout", "graph_minutes"] + [k for n in range(1, LINES + 1)
                                                           for k in (f"line{n}", f"line{n}_color")]),
        ("Screens", "When to show", ["show", "takeover"]),
        ("Look", "Units", ["temp_unit"]),
        ("Advanced", "Timing", ["duration", "refresh", "lhm_refresh"]),
    ])

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.states = {}                     # entity_id -> HA state dict
        self.series = {}                     # stat -> deque of (time, value): up to an hour at one sample a second
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
        values, hw = {}, {}
        for key in LHM_KEYS:
            want = (self.cfg.get(f"lhm_{key}") or "").strip()
            s = find(sensors, want) if want else picks.get(key)
            if s and s["value"] is not None:
                values[key] = (s["value"], s["unit"], s["name"])
                hw[key] = s["hw"]
        self.lhm_values = values
        self.lhm_hw = hw
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

    def stat(self, key):
        """A stat's current value in its graph units: % / °C / W / RPM / GHz / bytes per second (or None)."""
        if key in ("cpu_load", "gpu_load", "ram"):
            return self.value(key)[0]
        if key in ("cpu_temp", "gpu_temp"):
            return self._temp_c(key)
        if key in ("net_down", "net_up"):
            return to_bytes(*self.value(key))
        if not self._lhm_on():
            return None                                  # the rest only come from LibreHardwareMonitor
        if key == "power":
            gw, cw = self._x("gpu_power")[0], self._x("cpu_power")[0]
            return None if gw is None and cw is None else (gw or 0) + (cw or 0)
        if key in ("gpu_power", "cpu_power", "gpu_fan"):
            return self._x(key)[0]
        if key in ("gpu_clock", "cpu_clock"):
            v, u = self._x(key)
            return None if v is None else v / 1e6 if "kHz" in u else v if "GHz" in u else v / 1000
        if key == "vram":
            used, total = self._x("vram_used")[0], self._x("vram_total")[0]
            return None if used is None or not total else 100 * used / total
        return None

    def _x(self, key):
        """(value, unit) of a LibreHardwareMonitor reading, or (None, '')."""
        got = getattr(self, "lhm_values", {}).get(key) if self._lhm_on() else None
        return (got[0], got[1]) if got else (None, "")

    def _sample(self):
        """Record every stat (at most once a second) for the graph."""
        now = time.time()
        for k in STATS:
            v = self.stat(k)
            if v is None:
                continue
            q = self.series.setdefault(k, deque(maxlen=4000))
            if q and now - q[-1][0] < 1:
                q[-1] = (now, v)
            else:
                q.append((now, v))

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
    def _lines(self):
        """The graph's lines: [(stat, color)] from the Line 1-4 settings."""
        out = []
        for n in range(1, LINES + 1):
            key = self.cfg.get(f"line{n}", LINE_DEFAULTS[n - 1][0])
            if key in STATS and key not in [k for k, _c in out]:
                out.append((key, hex_to_rgb(self.cfg.get(f"line{n}_color") or LINE_DEFAULTS[n - 1][1])))
        return out

    def _deg(self, key):
        v, unit = self.value(key)
        return None if v is None else f"{v:.0f}°"

    def _text(self, key):
        """A stat's current value, short enough for the number panel."""
        kind = STATS[key][2]
        if kind == "temp":
            return self._deg(key) or "--"
        v = self.stat(key)
        if v is None:
            return "--"
        if kind == "pct":
            return f"{v:.0f}%"
        if kind == "watts":
            return f"{v:.0f}W"
        if kind == "rpm":
            return f"{v:.0f}"
        if kind == "ghz":
            return f"{v:.2f}G"
        return fmt_rate(v, "B/s").replace("/s", "")       # rate: 18.7MB

    def _value_color(self, key, line_color):
        """Loads and temperatures warn green / yellow / red; everything else is white."""
        kind = STATS[key][2]
        v = self.stat(key)
        if kind == "pct" and key != "vram":
            return level_color(v, "load")
        if kind == "temp":
            return level_color(v, "temp")
        return WHITE

    def _span(self):
        try:
            return max(60, int(float(self.cfg.get("graph_minutes") or 10)) * 60)
        except (TypeError, ValueError):
            return 600

    def _pts(self, key):
        now, span = time.time(), self._span()
        return [(ts, v) for ts, v in self.series.get(key, ()) if now - ts <= span]

    def _range(self, key):
        """(low, high) for a stat's line: fixed for % and temperatures, otherwise scaled to the data shown."""
        fixed = STATS[key][3]
        if fixed:
            return fixed
        top = max([v for _ts, v in self._pts(key)] + [0])
        floor = {"watts": 50, "rpm": 500, "ghz": 1, "rate": 1e5}.get(STATS[key][2], 1)
        return 0, max(floor, top * 1.1)

    def _plot(self, c, box, key, color, fill=None):
        """A step graph of a stat's history inside box=(x0, y0, x1, y1); fill = a color under the line."""
        x0, y0, x1, y1 = box
        now, span = time.time(), self._span()
        pts = self._pts(key)
        if not pts:
            return
        lo, hi = self._range(key)
        rng = (hi - lo) or 1

        def xy(ts, v):
            return (x1 - round((now - ts) / span * (x1 - x0)),
                    y1 - round((y1 - y0) * max(0.0, min(1.0, (v - lo) / rng))))
        coords = [xy(ts, v) for ts, v in pts]
        coords.append((x1, coords[-1][1]))              # the latest value holds to the right edge
        if fill:
            for (xa, ya), (xb, _yb) in zip(coords, coords[1:]):
                for x in range(max(x0, xa), xb + 1):
                    c.line(x, ya, x, y1, fill)
        for (xa, ya), (xb, yb) in zip(coords, coords[1:]):
            c.line(xa, ya, xb, ya, color)
            c.line(xb, ya, xb, yb, color)

    @staticmethod
    def _grid(c, box, fracs=(0.5,)):
        x0, y0, x1, y1 = box
        c.rect(x0, y0, x1, y1, (10, 10, 14))
        for f in fracs:
            y = y1 - round((y1 - y0) * f)
            for x in range(x0, x1, 3):
                c.pixel(x, y, (30, 30, 36))

    def _numbers(self, c, lines, x0=87):
        """The number panel on the right: a row per line (label in the line's color, then the value)."""
        W, H = c.width, c.height
        step = max(8, min(10, H // max(1, len(lines))))
        y = (H - step * len(lines)) // 2 + 1
        for key, col in lines:
            c.text(x0, y, STATS[key][0], "4x6", col)
            c.text_right(W - 1, y, self._text(key), "4x6", self._value_color(key, col))
            y += step

    def render(self, c, slide, t):
        lines = self._lines()
        if not lines:
            c.text_centered(12, "PICK GRAPH LINES", "5x7", GRAY)
            return
        lay = self.cfg.get("layout", "graph")
        {"wide": self._render_wide, "area": self._render_area, "lanes": self._render_lanes}.get(
            lay, self._render_graph)(c, lines, t)

    def _render_graph(self, c, lines, t, fill=False):
        H = c.height
        box = (0, 1, 83, H - 2)
        self._grid(c, box)
        for key, col in reversed(lines):                 # line 1 drawn last, on top
            self._plot(c, box, key, col, fill=scale(col, 0.28) if fill else None)
        self._numbers(c, lines)

    def _render_area(self, c, lines, t):
        self._render_graph(c, lines, t, fill=True)

    def _render_wide(self, c, lines, t):
        W, H = c.width, c.height
        box = (0, 0, W - 1, H - 1)
        self._grid(c, box, (0.25, 0.5, 0.75))
        for key, col in reversed(lines):
            self._plot(c, box, key, col)
        # the numbers in the corners (top-left, top-right, bottom-left, bottom-right), on a dark backing
        spots = [(False, 0), (True, 0), (False, H - 6), (True, H - 6)]
        for (key, col), (right, y) in zip(lines, spots):
            label, val = STATS[key][0], self._text(key)
            w = c.text_width(f"{label} {val}", "4x6")
            x = W - w - 1 if right else 1
            c.rect(x - 1, y, x + w, y + 6, (0, 0, 0))
            lw = c.text(x, y, label + " ", "4x6", col)
            c.text(x + lw, y, val, "4x6", self._value_color(key, col))

    def _render_lanes(self, c, lines, t):
        W, H = c.width, c.height
        n = len(lines)
        lane = H // n
        for i, (key, col) in enumerate(lines):
            y0 = i * lane
            y1 = y0 + lane - 2 if i < n - 1 else H - 1
            box = (0, y0, 99, y1)
            c.rect(*box, (10, 10, 14))
            self._plot(c, box, key, col, fill=scale(col, 0.3))
            if lane >= 14:                               # roomy lanes: label above the number
                c.text(102, y0, STATS[key][0], "4x6", col)
                c.text_right(W - 1, y0 + lane - 8, self._text(key), "5x7" if lane >= 16 else "4x6",
                             self._value_color(key, col))
            else:                                        # tight lanes: just the number, in the line's color
                c.text_right(W - 1, y0 + (lane - 7) // 2, self._text(key), "5x7" if lane >= 9 else "4x6", col)
