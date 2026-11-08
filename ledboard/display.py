"""Output drivers: the real RGB matrix (hzeller/rpi-rgb-led-matrix) or a simulator."""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

CHUNK = 8                 # pixels compared at once (as a byte slice) before going pixel by pixel
FULL_REPAINT_RATIO = 0.5  # above this share of changed chunks, a full repaint is cheaper


def changed_pixels(new: bytes, old: bytes | None, width: int, height: int):
    """Pixels that differ between two RGB frames, as [(x, y, r, g, b), ...].

    Returns None when a full repaint is the better choice (no previous frame, or most
    of the screen changed). Pure Python, but compares 8-pixel byte slices first so
    unchanged areas cost almost nothing.
    """
    if old is None or len(old) != len(new):
        return None
    if new == old:
        return []
    row_bytes = width * 3
    step = CHUNK * 3
    chunks_total = height * ((width + CHUNK - 1) // CHUNK)
    limit = chunks_total * FULL_REPAINT_RATIO
    dirty_chunks = 0
    out = []
    for y in range(height):
        r0 = y * row_bytes
        r1 = r0 + row_bytes
        if new[r0:r1] == old[r0:r1]:
            continue
        for c0 in range(r0, r1, step):
            c1 = min(c0 + step, r1)
            if new[c0:c1] == old[c0:c1]:
                continue
            dirty_chunks += 1
            if dirty_chunks > limit:
                return None
            for i in range(c0, c1, 3):
                r, g, b = new[i], new[i + 1], new[i + 2]
                if r != old[i] or g != old[i + 1] or b != old[i + 2]:
                    out.append(((i - r0) // 3, y, r, g, b))
    return out


class NullDisplay:
    """No hardware: frames are only shown in the web UI preview."""

    simulated = True

    def __init__(self, cfg):
        self.width = cfg["cols"] * cfg["chain_length"]
        self.height = cfg["rows"] * cfg["parallel"]
        self.brightness = cfg["brightness"]

    def set_brightness(self, value):
        self.brightness = int(value)

    def show(self, image):
        pass

    def clear(self):
        pass

    def close(self):
        pass


class MatrixDisplay:
    simulated = False

    def __init__(self, cfg):
        from rgbmatrix import RGBMatrix, RGBMatrixOptions  # type: ignore

        o = RGBMatrixOptions()
        o.rows = cfg["rows"]
        o.cols = cfg["cols"]
        o.chain_length = cfg["chain_length"]
        o.parallel = cfg["parallel"]
        o.hardware_mapping = cfg["hardware_mapping"]
        o.gpio_slowdown = cfg["gpio_slowdown"]
        o.brightness = cfg["brightness"]
        o.pwm_bits = cfg["pwm_bits"]
        o.pwm_lsb_nanoseconds = cfg["pwm_lsb_nanoseconds"]
        o.scan_mode = cfg["scan_mode"]
        o.row_address_type = cfg["row_address_type"]
        o.multiplexing = cfg["multiplexing"]
        o.led_rgb_sequence = cfg["led_rgb_sequence"]
        o.disable_hardware_pulsing = cfg["disable_hardware_pulsing"]
        o.show_refresh_rate = cfg["show_refresh_rate"]
        if cfg.get("pixel_mapper_config"):
            o.pixel_mapper_config = cfg["pixel_mapper_config"]
        if cfg.get("panel_type"):
            o.panel_type = cfg["panel_type"]
        if cfg.get("limit_refresh_rate_hz"):
            o.limit_refresh_rate_hz = cfg["limit_refresh_rate_hz"]
        if cfg.get("pwm_dither_bits"):
            o.pwm_dither_bits = cfg["pwm_dither_bits"]
        if cfg.get("inverse_colors"):
            o.inverse_colors = True
        # Keep root so we can keep writing the config file / cache after init.
        if hasattr(o, "drop_privileges"):
            o.drop_privileges = False
        self.matrix = RGBMatrix(options=o)
        self.offscreen = self.matrix.CreateFrameCanvas()
        self.width = self.matrix.width
        self.height = self.matrix.height
        self.brightness = cfg["brightness"]
        self._last = None          # frame currently on screen
        self._prev = None          # frame from two swaps ago = what the offscreen canvas holds
        self.stats = {"pixels": 0, "full": 0, "partial": 0}
        reserve_refresh_core(cfg)

    def set_brightness(self, value):
        self.brightness = int(value)
        self.matrix.brightness = self.brightness
        self.offscreen.brightness = self.brightness
        # brightness is applied when pixels are set, so both buffers need a full repaint
        self._last = self._prev = None

    def show(self, image):
        data = image.tobytes()
        if data == self._last:
            return  # nothing changed (e.g. the clock between seconds): skip the copy
        # Double buffering: the offscreen canvas still holds the frame from two swaps ago,
        # so only the pixels that differ from *that* frame need setting.
        diff = changed_pixels(data, self._prev, self.width, self.height)
        if diff is None:
            # unsafe=False: the "unsafe" fast path reads Pillow's private C struct, whose
            # layout changes between Pillow versions; the safe path works with any version.
            self.offscreen.SetImage(image, unsafe=False)
            self.stats["full"] += 1
            self.stats["pixels"] += self.width * self.height
        else:
            set_pixel = self.offscreen.SetPixel
            for x, y, r, g, b in diff:
                set_pixel(x, y, r, g, b)
            self.stats["partial"] += 1
            self.stats["pixels"] += len(diff)
        self._prev, self._last = self._last, data
        self.offscreen = self.matrix.SwapOnVSync(self.offscreen)

    def clear(self):
        self.matrix.Clear()

    def close(self):
        try:
            self.matrix.Clear()
        except Exception:
            pass


def reserve_refresh_core(cfg):
    """Keep LEDBoard's own threads off the core the LED refresh thread should own.

    The matrix library starts its refresh thread when the matrix is created (before this
    runs), so that thread keeps access to every core while everything LEDBoard starts
    afterwards (web server, data fetchers, render loop) avoids the last one. If the kernel
    has isolated a core (isolcpus=3), the library pins its refresh thread there itself.
    """
    if not cfg.get("reserve_cpu_core", True) or not hasattr(os, "sched_setaffinity"):
        return
    try:
        cpus = sorted(os.sched_getaffinity(0))
        if len(cpus) < 2:
            return
        keep = set(cpus[:-1])
        os.sched_setaffinity(0, keep)
        log.info("Reserved CPU %d for the LED refresh thread (LEDBoard uses CPUs %s)", cpus[-1], sorted(keep))
    except OSError as e:
        log.debug("could not set CPU affinity: %s", e)


def create(cfg, simulate=False):
    if simulate:
        log.info("Simulation mode: no LED hardware will be driven")
        return NullDisplay(cfg)
    try:
        return MatrixDisplay(cfg)
    except ImportError:
        log.warning("rgbmatrix module not installed - falling back to simulator (web preview only)")
    except Exception as e:  # pragma: no cover - hardware specific
        log.error("Could not initialise LED matrix (%s) - falling back to simulator", e)
    return NullDisplay(cfg)
