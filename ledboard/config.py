"""Configuration: a single JSON file, deep-merged over defaults."""
from __future__ import annotations

import copy
import json
import logging
import os
import threading

log = logging.getLogger(__name__)

DEFAULT_PATH = os.environ.get("LEDBOARD_CONFIG", "/etc/ledboard/config.json")

DEFAULTS = {
    "display": {
        "rows": 32,
        "cols": 64,
        "chain_length": 2,
        "parallel": 1,
        "hardware_mapping": "adafruit-hat",
        "gpio_slowdown": 4,
        "brightness": 60,
        "pwm_bits": 11,
        "pwm_lsb_nanoseconds": 130,
        "pwm_dither_bits": 0,
        "scan_mode": 0,
        "row_address_type": 0,
        "multiplexing": 0,
        "led_rgb_sequence": "RGB",
        "pixel_mapper_config": "",
        "panel_type": "",
        "limit_refresh_rate_hz": 0,
        "disable_hardware_pulsing": False,
        "show_refresh_rate": False,
        "inverse_colors": False,
        "fps": 30,
        "reserve_cpu_core": True,
    },
    "web": {"host": "0.0.0.0", "port": 8080, "password": ""},
    "mqtt": {"enabled": False, "host": "", "port": 1883, "username": "", "password": "",
             "discovery_prefix": "homeassistant", "base_topic": "ledboard", "device_name": "LEDBoard"},
    "schedule": {
        "enabled": False,
        "start": "22:00",
        "end": "07:00",
        "mode": "dim",          # "dim", "off" or "ambient"
        "brightness": 15,
    },
    "scroll": {"speed": 20, "gap": 16},
    "transitions": {"style": "slide", "seconds": 0.6, "scope": "boards"},   # between boards (or every slide)
    "combined": {"enabled": False, "speed": "", "gap": "", "pause": 0},   # all boards in one ticker
    "auto_brightness": {"follow_sun": False, "night": 15, "latitude": "", "longitude": ""},   # used by boards whose own scroll settings are blank
    "fallback_board": "clock",  # shown when no board has anything to display
    "rotation": [],             # defaults to the board registry order
    "boards": {},               # filled from each board's DEFAULTS
}

# Keys whose values must never be returned to the browser in clear text.
SECRET_KEYS = {"client_secret", "refresh_token", "token", "password", "api_key"}
MASK = "••••••••"


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    def __init__(self, path=DEFAULT_PATH, board_defaults=None, migrate=None):
        self.path = path
        self.migrate = migrate
        self.lock = threading.RLock()
        defaults = copy.deepcopy(DEFAULTS)
        defaults["boards"] = copy.deepcopy(board_defaults or {})
        defaults["rotation"] = list(defaults["boards"])
        self.defaults = defaults
        self.data = copy.deepcopy(defaults)
        self.load()

    def load(self):
        with self.lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path) as f:
                        raw = json.load(f)
                    migrated = bool(self.migrate and self.migrate(raw))
                    self.data = deep_merge(self.defaults, raw)
                    if migrated:
                        import shutil
                        shutil.copy2(self.path, self.path + ".bak")
                        log.info("Upgraded config format in %s (old copy saved as .bak)", self.path)
                        self.load_rotation_fixup()
                        self.save()
                except Exception as e:
                    log.error("Failed to read %s (%s); using defaults", self.path, e)
            else:
                log.info("No config at %s yet; using defaults", self.path)
            self.load_rotation_fixup()

    def save(self):
        with self.lock:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)  # contains API tokens
            except OSError:
                pass

    def get(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def update(self, patch):
        """Merge a (possibly partial) config from the web UI. Masked secrets are ignored."""
        with self.lock:
            patch = _strip_masked(patch)
            self.data = deep_merge(self.data, patch)
            # lists are replaced wholesale by deep_merge, which is what we want
            self.load_rotation_fixup()
            self.save()

    def load_rotation_fixup(self):
        """Make sure every known board appears in the rotation exactly once."""
        rot = [b for b in self.data["rotation"] if b in self.data["boards"]]
        rot += [b for b in self.data["boards"] if b not in rot]
        self.data["rotation"] = list(dict.fromkeys(rot))

    def replace(self, data):
        """Restore a whole config (from a backup): migrate, merge over defaults, save."""
        if not isinstance(data, dict) or not isinstance(data.get("boards", {}), dict):
            raise ValueError("not a LEDBoard backup")
        with self.lock:
            if self.migrate:
                self.migrate(data)
            self.data = deep_merge(self.defaults, data)
            self.load_rotation_fixup()
            self.save()

    def public(self):
        """Config with secrets masked, for the web UI."""
        return _mask(self.get())


def _mask(obj):
    if isinstance(obj, dict):
        return {k: (MASK if k in SECRET_KEYS and v else _mask(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask(v) for v in obj]
    return obj


def _strip_masked(obj):
    if isinstance(obj, dict):
        return {k: _strip_masked(v) for k, v in obj.items() if v != MASK}
    if isinstance(obj, list):
        return [_strip_masked(v) for v in obj]
    return obj


def import_sportsmatrix(path, cfg: Config):
    """Copy hardware settings from robbydyer's /etc/sportsmatrix.conf (YAML) into our config.

    Uses a tiny line parser so we don't need PyYAML.
    """
    mapping = {
        "rows": ("rows", int), "cols": ("cols", int), "brightness": ("brightness", int),
        "hardwareMapping": ("hardware_mapping", str), "chainLength": ("chain_length", int),
        "parallel": ("parallel", int), "pwmBits": ("pwm_bits", int),
        "pwmlsbNanoseconds": ("pwm_lsb_nanoseconds", int), "pwmDitherBits": ("pwm_dither_bits", int),
        "ledRgbSequence": ("led_rgb_sequence", lambda v: v.upper()),
        "pixelMapperConfig": ("pixel_mapper_config", str),
        "limitRefreshRateHz": ("limit_refresh_rate_hz", int),
        "gpioSlowdown": ("gpio_slowdown", int), "slowdownGPIO": ("gpio_slowdown", int),
        "disableHardwarePulsing": ("disable_hardware_pulsing", lambda v: v.lower() == "true"),
        "inverseColors": ("inverse_colors", lambda v: v.lower() == "true"),
        "scanMode": ("scan_mode", lambda v: 1 if v.strip().lower() == "interlaced" else 0),
        "panelType": ("panel_type", str), "multiplexing": ("multiplexing", int),
        "rowAddressType": ("row_address_type", int),
    }
    found = {}
    in_hw, indent = False, None
    with open(path) as f:
        for raw in f:
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            lead = len(line) - len(line.lstrip())
            if line.strip().startswith("hardwareConfig:"):
                in_hw, indent = True, lead
                continue
            if in_hw:
                if lead <= indent:
                    break
                if ":" in line:
                    k, v = line.strip().split(":", 1)
                    v = v.strip().strip('"').strip("'")
                    if k in mapping and v != "":
                        key, conv = mapping[k]
                        try:
                            found[key] = conv(v)
                        except ValueError:
                            pass
    if found:
        cfg.update({"display": found})
    return found
