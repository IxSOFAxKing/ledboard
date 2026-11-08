"""Output drivers: the real RGB matrix (hzeller/rpi-rgb-led-matrix) or a simulator."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


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
        self._last = None

    def set_brightness(self, value):
        self.brightness = int(value)
        self.matrix.brightness = self.brightness
        self.offscreen.brightness = self.brightness
        self._last = None  # brightness is applied when pixels are set, so re-push

    def show(self, image):
        data = image.tobytes()
        if data == self._last:
            return  # nothing changed (e.g. the clock between seconds): skip the copy
        self._last = data
        # unsafe=False: the "unsafe" fast path reads Pillow's private C struct, whose
        # layout changes between Pillow versions; the safe path works with any version.
        self.offscreen.SetImage(image, unsafe=False)
        self.offscreen = self.matrix.SwapOnVSync(self.offscreen)

    def clear(self):
        self.matrix.Clear()

    def close(self):
        try:
            self.matrix.Clear()
        except Exception:
            pass


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
