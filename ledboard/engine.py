"""The render loop: decides what to show and pushes frames to the display."""
from __future__ import annotations

import io
import logging
import threading
import time
from datetime import datetime

from . import display as display_mod
from .boards import BOARD_CLASSES
from .alerts import PRIORITIES, AlertManager, render_alert
from .boards.message import draw_message
from .gfx import Canvas, RED, hex_to_rgb

log = logging.getLogger(__name__)


class ScrollSlide:
    """All of a board's slides joined into one horizontal ticker.

    Compares equal to any other ScrollSlide of the same board, so content
    refreshes mid-scroll don't cut the scroll short.
    """
    __slots__ = ("board", "slides")

    def __init__(self, board, slides):
        self.board, self.slides = board, list(slides)

    def __eq__(self, other):
        return isinstance(other, ScrollSlide) and other.board == self.board

    def __hash__(self):
        return hash(("scroll", self.board))


class Engine:
    def __init__(self, config, simulate=False):
        self.config = config
        cfg = config.get()
        self.display = display_mod.create(cfg["display"], simulate=simulate)
        self.width, self.height = self.display.width, self.display.height
        self.canvas = Canvas(self.width, self.height)
        self.boards = {name: cls(cfg["boards"][name], self) for name, cls in BOARD_CLASSES.items()}
        self.lock = threading.RLock()
        self.running = False
        self.paused = False
        self.power = True
        self.overlay = None            # dict(text, color, font, until, started)
        self.alerts = AlertManager()
        self._at_boundary = True       # True right after a slide change (low-priority alerts wait for this)
        self.cur = None                # (board_name, [slides], index, started_monotonic)
        self.takeover_active = False
        self._last_check = 0
        self._frame_png = None
        self._frame_lock = threading.Lock()
        self._frame_image = None
        self.fps_measured = 0.0
        self.restart_requested = False
        self.perf = {"render_ms": 0.0, "push_ms": 0.0}
        self._sys_cache = (0.0, {})
        self._applied_brightness = None

    # -- config ---------------------------------------------------------------
    @property
    def cfg(self):
        return self.config.data

    def apply_config(self):
        """Called after the web UI saved settings."""
        cfg = self.config.get()
        with self.lock:
            for name, b in self.boards.items():
                b.cfg = cfg["boards"][name]
                b.start()
                b.poke()
            self._applied_brightness = None
            self.cur = None  # re-evaluate rotation right away
        if getattr(self, "mqtt", None):
            self.mqtt.reload()

    def save_board_value(self, board, key, value):
        self.config.update({"boards": {board: {key: value}}})
        self.boards[board].cfg = self.config.get()["boards"][board]

    # -- controls ---------------------------------------------------------------
    def next(self):
        with self.lock:
            self.overlay = None
            self._advance(force_next_board=False)

    def jump(self, board_name):
        with self.lock:
            b = self.boards.get(board_name)
            if not b:
                raise KeyError(board_name)
            slides = self._with_intro(b, b.slides() or [None])
            self.cur = (board_name, slides, 0, time.monotonic())
            self.overlay = None

    def set_power(self, on: bool):
        self.power = bool(on)

    def set_paused(self, paused: bool):
        self.paused = bool(paused)

    def set_brightness(self, value):
        value = max(1, min(100, int(value)))
        self.config.update({"display": {"brightness": value}})
        self._applied_brightness = None

    def show_message(self, text, seconds=10, color="#ffffff", font="7x13B"):
        self.overlay = {"text": str(text), "color": hex_to_rgb(color), "font": font,
                        "until": time.monotonic() + float(seconds), "started": time.monotonic()}

    # -- rotation ---------------------------------------------------------------
    def _candidates(self):
        order = self.cfg["rotation"]
        takeover = []
        for name in order:
            b = self.boards.get(name)
            if b and b.enabled:
                s = b.takeover_slides()
                if s:
                    takeover.append((name, s))
        if takeover:
            return takeover, True
        normal = []
        for name in order:
            b = self.boards.get(name)
            if b and b.enabled:
                s = b.slides()
                if s:
                    if b.cfg.get("scroll_mode"):
                        s = [ScrollSlide(name, s)]
                    normal.append((name, s))
        return normal, False

    def _advance(self, force_next_board=False):
        cands, takeover = self._candidates()
        self.takeover_active = takeover
        now = time.monotonic()
        if not cands:
            fb = self.cfg.get("fallback_board", "clock")
            self.cur = (fb, [None], 0, now) if fb in self.boards else None
            return
        names = [n for n, _ in cands]
        if self.cur and not force_next_board:
            name, slides, idx, _ = self.cur
            if name in names and idx + 1 < len(slides):
                fresh = dict(cands)[name]
                # keep going through the snapshot, but skip slides that vanished
                for j in range(idx + 1, len(slides)):
                    if _is_intro(slides[j]) or slides[j] in fresh:
                        self.cur = (name, slides, j, now)
                        return
        # move to the next board after the current one, in rotation order
        start = 0
        if self.cur and self.cur[0] in self.cfg["rotation"]:
            order = self.cfg["rotation"]
            pos = order.index(self.cur[0])
            after = [n for n in order[pos + 1:] + order[:pos + 1] if n in names]
            nxt = after[0] if after else names[0]
            start = names.index(nxt)
        name, slides = cands[start]
        self.cur = (name, self._with_intro(self.boards[name], slides), 0, now)
        self._at_boundary = True

    @staticmethod
    def _with_intro(board, slides):
        """Queued intro slides (e.g. scoring celebrations) play before the board's own slides."""
        intro = board.intro_slides() if hasattr(board, "intro_slides") else []
        return list(intro) + list(slides) if intro else slides

    # -- scroll mode -------------------------------------------------------------
    def _scroll_params(self, board):
        """Board's own scroll speed/gap, or the global defaults when left blank."""
        g = self.cfg.get("scroll", {})

        def pick(key, gkey, default):
            v = board.cfg.get(key)
            if v in (None, ""):
                v = g.get(gkey, default)
            try:
                return float(v)
            except (TypeError, ValueError):
                return float(default)
        return max(5.0, pick("scroll_speed", "speed", 20)), max(0, int(pick("scroll_gap", "gap", 16)))

    def _duration(self, board, slide):
        if isinstance(slide, ScrollSlide):
            speed, gap = self._scroll_params(board)
            n = len(slide.slides)
            strip = n * self.width + (n - 1) * gap
            return (strip + self.width) / speed  # enter from the right, exit left
        return board.slide_duration(slide)

    def _render_scroll(self, board, scroll, c, t):
        speed, gap = self._scroll_params(board)
        offset = int(t * speed)
        step = self.width + gap
        if not hasattr(self, "_scratch"):
            self._scratch = Canvas(self.width, self.height)
        for i, slide in enumerate(scroll.slides):
            x = self.width - offset + i * step
            if x >= self.width or x + self.width <= 0:
                continue
            self._scratch.clear()
            # t=0: text inside each slide stays still while the whole slide moves
            board.render(self._scratch, slide, 0.0)
            c.image.paste(self._scratch.image, (x, 0))

    def _check_rotation(self):
        """Once a second: handle takeover changes and vanished content."""
        cands, takeover = self._candidates()
        if self.cur is None:
            self._advance()
            return
        name, slides, idx, started = self.cur
        names = dict(cands)
        if takeover != self.takeover_active or (takeover and name not in names):
            self.cur = None
            self._advance()
            return
        if _is_intro(slides[idx]):
            return                      # an intro (celebration) plays to the end regardless
        if name in names and slides[idx] not in names[name] and slides[idx] is not None:
            self._advance()
        elif name not in names and name != self.cfg.get("fallback_board") and not self.paused:
            self._advance(force_next_board=True)
        elif name == self.cfg.get("fallback_board") and name not in names and cands:
            self._advance(force_next_board=True)

    # -- schedule ---------------------------------------------------------------
    def _in_quiet_hours(self):
        s = self.cfg.get("schedule", {})
        if not s.get("enabled"):
            return False
        try:
            now = datetime.now().strftime("%H:%M")
            a, b = s.get("start", "22:00"), s.get("end", "07:00")
            return (a <= now < b) if a <= b else (now >= a or now < b)
        except Exception:
            return False

    def _critical_alert_pending(self):
        cur = self.alerts.current
        return bool((cur and cur.priority >= PRIORITIES["critical"]) or self.alerts.next_due(PRIORITIES["critical"]))

    def _sun_location(self):
        a = self.cfg.get("auto_brightness", {})
        lat, lon = a.get("latitude"), a.get("longitude")
        if lat in ("", None) or lon in ("", None):         # fall back to the Weather board's location
            w = self.cfg["boards"].get("weather", {})
            lat, lon = w.get("latitude"), w.get("longitude")
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            return None

    def _base_brightness(self):
        """Configured brightness, or the sun-following value (recomputed once a minute)."""
        day = int(self.cfg["display"]["brightness"])
        a = self.cfg.get("auto_brightness", {})
        if not a.get("follow_sun"):
            self.sun_info = None
            return day
        now = time.monotonic()
        cache = getattr(self, "_sun_cache", None)
        if cache and now - cache[0] < 60 and cache[1] == (day, a.get("night")):
            return cache[2]
        loc = self._sun_location()
        if not loc:
            self.sun_info = {"error": "Set a location (here or on the Weather board)"}
            return day
        from .sun import solar_elevation, sun_brightness
        elev = solar_elevation(*loc)
        value = sun_brightness(elev, day, int(a.get("night", 15) or 1))
        self.sun_info = {"elevation": round(elev, 1), "brightness": value}
        self._sun_cache = (now, (day, a.get("night")), value)
        return value

    def _quiet_mode(self):
        return self.cfg["schedule"].get("mode", "dim") if self._in_quiet_hours() else None

    def _effective(self):
        """(power_on, brightness) after manual power, sun-following brightness and quiet hours."""
        bright = self._base_brightness()
        if not self.power:
            return False, bright
        mode = self._quiet_mode()
        if mode == "off":
            return False, bright
        if mode in ("dim", "ambient"):
            return True, int(self.cfg["schedule"].get("brightness", 15))
        return True, bright

    # -- main loop ---------------------------------------------------------------
    def render_frame(self):
        c = self.canvas
        c.clear()
        now = time.monotonic()
        alert, at = self.alerts.tick(at_boundary=self._at_boundary)
        self._at_boundary = False
        if alert:
            render_alert(c, alert, at)
            return c.image
        if self._quiet_mode() == "ambient" and "ambient" in self.boards:
            self.boards["ambient"].render(c, None, now)          # quiet hours: ambient only
            return c.image
        if self.overlay:
            if now < self.overlay["until"]:
                o = self.overlay
                draw_message(c, o["text"], o["color"], o["font"], now - o["started"])
                return c.image
            self.overlay = None
        with self.lock:
            if now - self._last_check >= 1.0:
                self._last_check = now
                self._check_rotation()
            if self.cur is None:
                self._advance()
            if self.cur is None:
                return c.image
            name, slides, idx, started = self.cur
            board = self.boards[name]
            if not self.paused and now - started >= self._duration(board, slides[idx]):
                self._advance()
                name, slides, idx, started = self.cur
                board = self.boards[name]
        try:
            if isinstance(slides[idx], ScrollSlide):
                self._render_scroll(board, slides[idx], c, now - started)
            else:
                board.render(c, slides[idx], now - started)
        except Exception as e:
            log.exception("render error in %s", name)
            c.clear()
            c.text(0, 0, name, "5x8", RED)
            c.scroll_text(0, c.width, 12, f"{type(e).__name__}: {e}", "5x8", RED, now - started)
        return c.image

    def run(self):
        self.running = True
        for b in self.boards.values():
            b.start()
        frames, t_fps = 0, time.monotonic()
        log.info("Display %dx%d (%s) running", self.width, self.height,
                 "simulated" if self.display.simulated else "hardware")
        while self.running:
            fps = max(1, int(self.cfg["display"].get("fps", 30)))
            t0 = time.monotonic()
            on, bright = self._effective()
            if not on and self._critical_alert_pending():
                on, bright = True, int(self.cfg["display"]["brightness"])   # critical alerts wake the display
            if bright != self._applied_brightness:
                self.display.set_brightness(bright)
                self._applied_brightness = bright
            if on:
                img = self.render_frame()
            else:
                self.canvas.clear()
                img = self.canvas.image
            t1 = time.monotonic()
            self.display.show(img)
            t2 = time.monotonic()
            # smoothed per-frame costs, shown on the Status tab
            self.perf["render_ms"] = self.perf["render_ms"] * 0.95 + (t1 - t0) * 1000 * 0.05
            self.perf["push_ms"] = self.perf["push_ms"] * 0.95 + (t2 - t1) * 1000 * 0.05
            with self._frame_lock:
                self._frame_image = img.copy()
                self._frame_png = None
            frames += 1
            if t0 - t_fps >= 2:
                self.fps_measured = frames / (t0 - t_fps)
                frames, t_fps = 0, t0
            dt = time.monotonic() - t0
            time.sleep(max(0.0, 1.0 / fps - dt) if on else 0.5)
        for b in self.boards.values():
            b.stop()
        self.display.close()

    def stop(self):
        self.running = False

    def perf_info(self):
        """Frame timings plus Pi health (CPU temperature, throttling), refreshed every 10 s."""
        now = time.monotonic()
        if now - self._sys_cache[0] > 10:
            self._sys_cache = (now, _system_health())
        fps = max(1, int(self.cfg["display"].get("fps", 30)))
        busy = (self.perf["render_ms"] + self.perf["push_ms"]) / (1000 / fps) * 100
        return {"render_ms": round(self.perf["render_ms"], 2), "push_ms": round(self.perf["push_ms"], 2),
                "frame_budget_pct": round(busy), **self._sys_cache[1]}

    def frame_png(self):
        with self._frame_lock:
            if self._frame_image is None:
                return None
            if self._frame_png is None:
                buf = io.BytesIO()
                self._frame_image.save(buf, "PNG")
                self._frame_png = buf.getvalue()
            return self._frame_png

    def state(self):
        cur = self.cur
        on, bright = self._effective()
        return {
            "width": self.width, "height": self.height,
            "simulated": self.display.simulated,
            "power": self.power, "display_on": on, "brightness": bright,
            "quiet_hours": self._in_quiet_hours(),
            "sun": getattr(self, "sun_info", None),
            "paused": self.paused, "takeover": self.takeover_active,
            "current": {"board": cur[0], "slide": cur[2] + 1, "slides": len(cur[1])} if cur else None,
            "overlay": self.overlay["text"] if self.overlay else None,
            "alert": self.alerts.current.title if self.alerts.current else None,
            "alerts": self.alerts.active(),
            "mqtt": self.mqtt.status() if getattr(self, "mqtt", None) else {"enabled": False},
            "update_available": (getattr(self, "updater", None) and self.updater.info.get("behind")) or 0,
            "fps": round(self.fps_measured, 1),
            "perf": self.perf_info(),
            "boards": {n: b.status() for n, b in self.boards.items()},
        }


def _system_health():
    """CPU temperature, Pi throttling flags, load and CPU affinity (best effort, Linux only)."""
    import os
    import shutil
    import subprocess
    info = {}
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            info["cpu_temp_c"] = round(int(f.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        pass
    if shutil.which("vcgencmd"):
        try:
            out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2).stdout
            flags = int(out.strip().split("=")[1], 16)
            now = [name for bit, name in ((0x1, "under-voltage"), (0x2, "freq capped"), (0x4, "throttled"),
                                          (0x8, "temp limit")) if flags & bit]
            past = [name for bit, name in ((0x10000, "under-voltage"), (0x40000, "throttled"),
                                           (0x80000, "temp limit")) if flags & bit]
            info["throttled_now"] = now
            info["throttled_since_boot"] = past
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass
    try:
        info["load"] = round(os.getloadavg()[0], 2)
        info["cpus_used"] = sorted(os.sched_getaffinity(0))
        info["cpu_count"] = os.cpu_count()
        with open("/sys/devices/system/cpu/isolated") as f:
            info["isolated_cpus"] = f.read().strip()
    except (OSError, AttributeError):
        pass
    return info


def _is_intro(slide):
    return bool(getattr(slide, "intro", False))
