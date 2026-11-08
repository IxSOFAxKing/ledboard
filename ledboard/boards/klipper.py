"""3D printer status from Klipper via the Moonraker API.

Uses:
  GET /printer/objects/query?print_stats&virtual_sdcard&display_status&extruder&heater_bed&webhooks
  GET /server/files/metadata?filename=...   (slicer time estimate, layer count, thumbnails)
  GET /server/files/gcodes/<dir>/<thumbnail>  (model thumbnail)
An API key is only needed if Moonraker's authorization is enabled for your network.
"""
from __future__ import annotations

import os
import time
import urllib.parse
from datetime import datetime, timedelta

import requests

from .base import Board
from ..gfx import DIM, GRAY, RED, WHITE, YELLOW, fetch_image

HOTEND = (255, 120, 30)
BED = (60, 150, 255)
GREEN = (40, 210, 70)
OBJECTS = ["print_stats", "virtual_sdcard", "display_status", "extruder", "heater_bed", "webhooks"]


def fmt_dur(seconds):
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


class KlipperBoard(Board):
    name = "klipper"
    title = "3D Printer (Klipper)"
    description = ("Print progress from Klipper via Moonraker: model thumbnail, progress, time left, layer "
                   "and temperatures. Skipped when the printer is idle unless you choose otherwise.")
    refresh = 5
    DEFAULTS = {
        "enabled": False, "duration": 12, "refresh": 5,
        "url": "http://printer.local:7125", "api_key": "", "printer_name": "",
        "takeover": False, "show_when_idle": False, "show_finished_minutes": 15,
        "show_thumbnail": True, "eta_style": "time_left",
    }
    FIELDS = [
        {"key": "url", "label": "Moonraker URL", "type": "text", "section": "Connection",
         "help": "Usually http://<printer-ip>:7125 (the same address Mainsail/Fluidd talk to)."},
        {"key": "api_key", "label": "API key (only if Moonraker requires one)", "type": "password", "section": "Connection",
         "help": "Mainsail/Fluidd: Settings > Moonraker API key, or from moonraker's [authorization] setup."},
        {"key": "printer_name", "label": "Printer name (optional)", "type": "text", "section": "Connection"},
        {"key": "takeover", "label": "Take over the display while printing", "type": "bool", "section": "When to show"},
        {"key": "show_when_idle", "label": "Show temperatures when the printer is idle", "type": "bool",
         "section": "When to show"},
        {"key": "show_finished_minutes", "label": "Keep showing a finished print for (minutes)", "type": "number",
         "section": "When to show"},
        {"key": "show_thumbnail", "label": "Show the model thumbnail from the slicer", "type": "bool", "section": "Display",
         "help": "Needs thumbnails enabled in your slicer (e.g. PrusaSlicer/OrcaSlicer 'G-code thumbnails')."},
        {"key": "eta_style", "label": "Time display", "type": "select", "section": "Display",
         "options": ["time_left", "finish_time"],
         "option_labels": {"time_left": "Time left (1h23m)", "finish_time": "Finish time (4:35 PM)"}},
        {"key": "duration", "label": "Seconds on screen", "type": "number", "section": "Display"},
        {"key": "refresh", "label": "Poll interval (s)", "type": "number", "section": "Display"},
    ]

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.printer = None          # parsed printer status (dict) or None
        self.meta: dict = {}         # filename -> metadata
        self.thumbs: dict = {}       # filename -> PIL image
        self.finished_at = None
        self.fetched_at = 0
        self._last_state = None

    # -- data -----------------------------------------------------------------
    def _base(self):
        return (self.cfg.get("url") or "").rstrip("/")

    def _headers(self):
        key = self.cfg.get("api_key")
        return {"X-Api-Key": key} if key else {}

    def update(self):
        if not self._base():
            self.printer = None
            return
        q = "&".join(OBJECTS)
        r = requests.get(f"{self._base()}/printer/objects/query?{q}", headers=self._headers(), timeout=8)
        if r.status_code in (401, 403):
            raise RuntimeError("Moonraker refused the request - set the API key")
        r.raise_for_status()
        st = self.parse(r.json().get("result", {}).get("status", {}))
        fn = st["filename"]
        if fn and fn not in self.meta:
            self.meta[fn] = self._metadata(fn)
            if self.cfg.get("show_thumbnail", True):
                self.thumbs[fn] = self._thumbnail(fn, self.meta[fn])
        st["meta"] = self.meta.get(fn) or {}
        if st["state"] == "complete" and self._last_state != "complete":
            self.finished_at = time.time()
        self._last_state = st["state"]
        self.printer = st
        self.fetched_at = time.time()

    @staticmethod
    def parse(s):
        ps = s.get("print_stats") or {}
        info = ps.get("info") or {}
        vsd = s.get("virtual_sdcard") or {}
        disp = s.get("display_status") or {}
        ext = s.get("extruder") or {}
        bed = s.get("heater_bed") or {}
        hooks = s.get("webhooks") or {}
        progress = vsd.get("progress")
        if not progress and disp.get("progress"):
            progress = disp.get("progress")
        klippy = hooks.get("state", "ready")
        state = ps.get("state", "standby")
        if klippy in ("shutdown", "error"):
            state = "error"
        return {
            "state": state,                                  # standby printing paused complete cancelled error
            "klippy_message": hooks.get("state_message", "") if klippy != "ready" else "",
            "message": ps.get("message") or disp.get("message") or "",
            "filename": ps.get("filename") or "",
            "print_duration": float(ps.get("print_duration") or 0),
            "total_duration": float(ps.get("total_duration") or 0),
            "progress": float(progress or 0),
            "layer": info.get("current_layer"),
            "layers": info.get("total_layer"),
            "nozzle": ext.get("temperature"), "nozzle_target": ext.get("target"),
            "bed": bed.get("temperature"), "bed_target": bed.get("target"),
        }

    def _metadata(self, fn):
        try:
            r = requests.get(f"{self._base()}/server/files/metadata", params={"filename": fn},
                             headers=self._headers(), timeout=8)
            r.raise_for_status()
            return r.json().get("result", {})
        except Exception:
            return {}

    def _thumbnail(self, fn, meta):
        thumbs = meta.get("thumbnails") or []
        if not thumbs:
            return None
        size = self.app.height
        thumbs = sorted(thumbs, key=lambda t: t.get("width") or 0)
        pick = next((t for t in thumbs if (t.get("width") or 0) >= size), thumbs[-1])
        rel = pick.get("relative_path")
        if not rel:
            return None
        path = "/".join(p for p in (os.path.dirname(fn), rel) if p)
        url = f"{self._base()}/server/files/gcodes/{urllib.parse.quote(path)}"
        return fetch_image(url, size, size, headers=self._headers(), cache_key=f"klipper|{fn}|{rel}|{meta.get('modified')}")

    # -- derived ----------------------------------------------------------------
    def eta_seconds(self, st):
        """Remaining time: average of file-progress and slicer estimates (like Mainsail)."""
        dur, prog = st["print_duration"], st["progress"]
        if st["state"] == "printing":
            dur += time.time() - self.fetched_at
        guesses = []
        if prog > 0.01:
            guesses.append(dur / prog - dur)
        est = (st.get("meta") or {}).get("estimated_time")
        if est:
            guesses.append(max(0.0, est - dur))
        return sum(guesses) / len(guesses) if guesses else None

    def _visible_state(self):
        st = self.printer
        if not st:
            return None
        s = st["state"]
        if s in ("printing", "paused", "error"):
            return s
        if s == "complete" and self.finished_at and \
                time.time() - self.finished_at < float(self.cfg.get("show_finished_minutes", 15) or 0) * 60:
            return s
        if self.cfg.get("show_when_idle"):
            return "idle"
        return None

    def slides(self):
        return ["printer"] if self._visible_state() else []

    def takeover_slides(self):
        if self.cfg.get("takeover") and self._visible_state() in ("printing", "paused"):
            return ["printer"]
        return []

    def status(self):
        s = super().status()
        p = self.printer or {}
        s["printer"] = {k: p.get(k) for k in ("state", "filename", "progress", "nozzle", "bed")} if p else None
        return s

    # -- drawing ------------------------------------------------------------------
    def render(self, c, slide, t):
        st = self.printer
        vs = self._visible_state()
        if not st or not vs:
            return
        W, H = c.width, c.height
        thumb = self.thumbs.get(st["filename"]) if self.cfg.get("show_thumbnail", True) else None
        if vs == "idle":
            self._render_idle(c, st)
            return
        if thumb is not None:
            c.paste(thumb, 0, 0)
            x0 = H + 2
        else:
            self._printer_icon(c, 2, 4, vs)
            x0 = 26
        name = os.path.splitext(os.path.basename(st["filename"]))[0] or (self.cfg.get("printer_name") or "Printer")
        c.scroll_text(x0, W, 0, name, "5x8", WHITE, t)

        color = {"printing": GREEN, "paused": YELLOW, "complete": GREEN, "error": RED}.get(vs, GRAY)
        if vs == "error":
            msg = st["klippy_message"] or st["message"] or "ERROR"
            c.text(x0, 9, "ERROR", "6x10", RED)
            c.scroll_text(x0, W, 19, msg, "4x6", RED, t)
            return
        pct = f"{st['progress'] * 100:.0f}%" if vs != "complete" else "DONE"
        pw = c.text(x0, 9, pct, "6x10", color)
        # right of the percentage: time left / finish time / paused / finished at
        if vs == "paused":
            right = "PAUSED"
        elif vs == "complete":
            right = fmt_dur(st["print_duration"])
        else:
            eta = self.eta_seconds(st)
            if eta is None:
                right = fmt_dur(st["print_duration"])
            elif self.cfg.get("eta_style") == "finish_time":
                right = (datetime.now() + timedelta(seconds=eta)).strftime("%I:%M%p").lstrip("0")
            else:
                right = fmt_dur(eta) + " left" if c.text_width(fmt_dur(eta) + " left", "5x7") < W - x0 - pw - 4 \
                    else fmt_dur(eta)
        c.text_right(W - 1, 10, right, "5x7", YELLOW if vs == "paused" else WHITE)

        # temps (and layer) line: alternate every 4 s if layer info exists
        y = 20
        show_layer = st["layer"] and st["layers"] and int(t) // 4 % 2 == 1
        if show_layer:
            c.text(x0, y, f"LAYER {st['layer']}/{st['layers']}", "4x6", GRAY)
        else:
            x = x0
            x += self._temp(c, x, y, "E", st["nozzle"], st["nozzle_target"], HOTEND) + 5
            self._temp(c, x, y, "B", st["bed"], st["bed_target"], BED)
        c.progress(x0, H - 3, W - 1, st["progress"] if vs != "complete" else 1.0, color, height=3)

    def _temp(self, c, x, y, label, cur, target, color):
        if cur is None:
            return 0
        w = c.text(x, y, label, "4x6", color) + 1
        txt = f"{cur:.0f}"
        if target:
            txt += f"/{target:.0f}"
        heating = target and abs(cur - target) > 3
        w += c.text(x + w, y, txt, "4x6", (255, 80, 80) if heating and cur < target else WHITE)
        return w

    def _render_idle(self, c, st):
        self._printer_icon(c, 2, 4, "idle")
        name = self.cfg.get("printer_name") or "Printer"
        c.text(26, 1, name, "5x8", WHITE)
        c.text(26, 11, "IDLE" if st["state"] in ("standby", "cancelled") else st["state"].upper(), "4x6", GRAY)
        x = 26
        x += self._temp(c, x, 21, "E", st["nozzle"], st["nozzle_target"], HOTEND) + 6
        self._temp(c, x, 21, "B", st["bed"], st["bed_target"], BED)

    @staticmethod
    def _printer_icon(c, x, y, state):
        """20x22 printer frame with a print-head; head color shows the state."""
        frame = (110, 110, 120)
        c.rect(x, y, x + 19, y + 1, frame)                     # top bar
        c.rect(x, y, x + 1, y + 21, frame)                     # left post
        c.rect(x + 18, y, x + 19, y + 21, frame)               # right post
        c.rect(x, y + 20, x + 19, y + 21, frame)               # base
        c.rect(x + 3, y + 17, x + 16, y + 18, BED)             # bed
        head = {"printing": GREEN, "paused": YELLOW, "error": RED}.get(state, DIM)
        c.rect(x + 2, y + 6, x + 17, y + 6, frame)             # gantry
        c.rect(x + 7, y + 4, x + 12, y + 9, head)              # head
        c.pixel(x + 9, y + 10, HOTEND)
        c.pixel(x + 10, y + 10, HOTEND)                        # nozzle
        if state in ("printing", "paused"):
            c.rect(x + 7, y + 14, x + 12, y + 16, HOTEND)      # the part

