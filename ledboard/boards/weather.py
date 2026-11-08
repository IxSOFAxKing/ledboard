"""Weather with animated icons.

Sources:
  * Open-Meteo (default): free, no API key. Location by latitude/longitude; the web UI
    can look a place up by name (Open-Meteo geocoding).
  * Home Assistant: any weather.* entity (uses the URL/token from the Home Assistant board).
Slides: current conditions, 4-day forecast, next-24-hours graph (each optional).
"""
from __future__ import annotations

import logging
from datetime import datetime

import requests

from .base import Board
from .weather_icons import LABELS, WMO, draw_icon
from ..gfx import DIM, GRAY, WHITE

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
HI = (255, 170, 60)
LO = (90, 160, 255)
RAIN_BAR = (30, 90, 220)
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def compass(deg):
    if deg is None:
        return ""
    return COMPASS[int((float(deg) % 360) / 45 + 0.5) % 8]


def temp_color(t, unit):
    """Blue (cold) -> white -> orange/red (hot)."""
    if t is None:
        return WHITE
    f = t if unit == "fahrenheit" else t * 9 / 5 + 32
    stops = [(10, (120, 160, 255)), (40, (170, 210, 255)), (60, (255, 255, 255)), (80, (255, 190, 80)),
             (95, (255, 90, 40))]
    if f <= stops[0][0]:
        return stops[0][1]
    for (f0, c0), (f1, c1) in zip(stops, stops[1:]):
        if f <= f1:
            k = (f - f0) / (f1 - f0)
            return tuple(int(a + (b - a) * k) for a, b in zip(c0, c1))
    return stops[-1][1]


def parse_open_meteo(j):
    """Open-Meteo forecast JSON -> normalised weather dict."""
    cur = j.get("current") or {}
    daily = j.get("daily") or {}
    hourly = j.get("hourly") or {}
    units = j.get("current_units") or {}
    out = {
        "condition": WMO.get(cur.get("weather_code"), "cloudy"),
        "is_day": bool(cur.get("is_day", 1)),
        "temp": cur.get("temperature_2m"),
        "feels": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": cur.get("wind_speed_10m"),
        "wind_dir": compass(cur.get("wind_direction_10m")),
        "wind_unit": (units.get("wind_speed_10m") or "").replace("mp/h", "mph").replace("km/h", "kmh"),
        "daily": [], "hourly": [],
    }
    for i, day in enumerate(daily.get("time") or []):
        out["daily"].append({
            "date": day,
            "condition": WMO.get((daily.get("weather_code") or [None])[i], "cloudy"),
            "hi": (daily.get("temperature_2m_max") or [None])[i],
            "lo": (daily.get("temperature_2m_min") or [None])[i],
            "precip": (daily.get("precipitation_probability_max") or [None])[i],
        })
    now = datetime.now()
    for i, ts in enumerate(hourly.get("time") or []):
        try:
            when = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if when < now.replace(minute=0, second=0, microsecond=0):
            continue
        out["hourly"].append({
            "time": when,
            "temp": (hourly.get("temperature_2m") or [None])[i],
            "precip": (hourly.get("precipitation_probability") or [None])[i],
        })
        if len(out["hourly"]) >= 24:
            break
    return out


def parse_ha(state, daily, hourly, sun_up=True):
    """Home Assistant weather entity (+ forecasts from weather.get_forecasts) -> weather dict."""
    a = state.get("attributes", {})
    cond = state.get("state") or "cloudy"
    unit = a.get("wind_speed_unit") or ""
    out = {
        "condition": cond, "is_day": sun_up and cond != "clear-night",
        "temp": a.get("temperature"), "feels": a.get("apparent_temperature"),
        "humidity": a.get("humidity"), "wind": a.get("wind_speed"),
        "wind_dir": compass(a.get("wind_bearing")) if isinstance(a.get("wind_bearing"), (int, float))
        else (a.get("wind_bearing") or ""),
        "wind_unit": unit.replace("km/h", "kmh"), "daily": [], "hourly": [],
    }
    for f in daily or []:
        out["daily"].append({"date": (f.get("datetime") or "")[:10], "condition": f.get("condition"),
                             "hi": f.get("temperature"), "lo": f.get("templow"),
                             "precip": f.get("precipitation_probability")})
    for f in (hourly or [])[:24]:
        try:
            when = datetime.fromisoformat((f.get("datetime") or "").replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        except ValueError:
            continue
        out["hourly"].append({"time": when, "temp": f.get("temperature"), "precip": f.get("precipitation_probability")})
    return out


class WeatherBoard(Board):
    name = "weather"
    title = "Weather"
    description = "Current conditions, forecast and a 24-hour graph, with animated weather icons."
    refresh = 600
    DEFAULTS = {
        "enabled": False, "duration": 10, "refresh": 600,
        "source": "openmeteo", "location_name": "", "latitude": "", "longitude": "",
        "ha_entity": "", "units": "fahrenheit",
        "show_current": True, "show_daily": True, "show_hourly": True,
    }
    FIELDS = [
        {"key": "source", "label": "Weather data from", "type": "select", "section": "Source",
         "options": ["openmeteo", "homeassistant"],
         "option_labels": {"openmeteo": "Open-Meteo (free, no key)", "homeassistant": "Home Assistant weather entity"}},
        {"key": "location_name", "label": "Location", "type": "geocode", "section": "Source",
         "show_if": {"source": "openmeteo"},
         "help": "Search a city or ZIP, pick a result, then Save. Latitude/longitude fill in automatically."},
        {"key": "latitude", "label": "Latitude", "type": "number", "section": "Source", "show_if": {"source": "openmeteo"}},
        {"key": "longitude", "label": "Longitude", "type": "number", "section": "Source", "show_if": {"source": "openmeteo"}},
        {"key": "ha_entity", "label": "Weather entity", "type": "text", "section": "Source",
         "show_if": {"source": "homeassistant"}, "datalist": "ha-entities",
         "help": "e.g. weather.forecast_home. Uses the URL and token from the Home Assistant board."},
        {"key": "units", "label": "Units", "type": "select", "section": "Display", "options": ["fahrenheit", "celsius"],
         "option_labels": {"fahrenheit": "°F, mph", "celsius": "°C, km/h"}, "show_if": {"source": "openmeteo"}},
        {"key": "show_current", "label": "Show current conditions", "type": "bool", "section": "Display"},
        {"key": "show_daily", "label": "Show 4-day forecast", "type": "bool", "section": "Display"},
        {"key": "show_hourly", "label": "Show next-24-hours graph", "type": "bool", "section": "Display"},
        {"key": "duration", "label": "Seconds per screen", "type": "number", "section": "Display"},
        {"key": "refresh", "label": "Refresh interval (s)", "type": "number", "section": "Display"},
    ]

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.weather = None

    # -- data -----------------------------------------------------------------
    def update(self):
        if self.cfg.get("source") == "homeassistant":
            self.weather = self._update_ha()
        else:
            self.weather = self._update_open_meteo()

    def _update_open_meteo(self):
        lat, lon = self.cfg.get("latitude"), self.cfg.get("longitude")
        if lat in ("", None) or lon in ("", None):
            return None
        metric = self.cfg.get("units") == "celsius"
        params = {
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 5,
            "temperature_unit": "celsius" if metric else "fahrenheit",
            "wind_speed_unit": "kmh" if metric else "mph",
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,"
                       "wind_speed_10m,wind_direction_10m",
            "hourly": "temperature_2m,precipitation_probability",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        }
        r = requests.get(FORECAST_URL, params=params, timeout=15)
        r.raise_for_status()
        w = parse_open_meteo(r.json())
        w["unit"] = "celsius" if metric else "fahrenheit"
        return w

    def _update_ha(self):
        ent = (self.cfg.get("ha_entity") or "").strip()
        ha = self.app.boards.get("homeassistant")
        hcfg = ha.cfg if ha else {}
        base = (hcfg.get("url") or "").rstrip("/")
        if not ent:
            return None
        if not (base and hcfg.get("token")):
            raise RuntimeError("Set the Home Assistant URL and token on the Home Assistant board")
        h = {"Authorization": f"Bearer {hcfg['token']}", "Content-Type": "application/json"}
        verify = bool(hcfg.get("verify_ssl", True))
        r = requests.get(f"{base}/api/states/{ent}", headers=h, timeout=10, verify=verify)
        if r.status_code == 404:
            raise RuntimeError(f"Home Assistant has no entity {ent}")
        r.raise_for_status()
        state = r.json()
        forecasts = {}
        for kind in ("daily", "hourly"):
            try:
                fr = requests.post(f"{base}/api/services/weather/get_forecasts?return_response", headers=h,
                                   json={"entity_id": ent, "type": kind}, timeout=10, verify=verify)
                fr.raise_for_status()
                forecasts[kind] = ((fr.json().get("service_response") or {}).get(ent) or {}).get("forecast") or []
            except Exception as e:  # some integrations don't offer hourly
                log.debug("HA %s forecast unavailable: %s", kind, e)
                forecasts[kind] = []
        if not forecasts["daily"]:
            forecasts["daily"] = state.get("attributes", {}).get("forecast") or []   # HA < 2024.3
        sun_up = True
        try:
            s = requests.get(f"{base}/api/states/sun.sun", headers=h, timeout=10, verify=verify)
            if s.ok:
                sun_up = s.json().get("state") != "below_horizon"
        except requests.RequestException:
            pass
        w = parse_ha(state, forecasts["daily"], forecasts["hourly"], sun_up)
        unit = state.get("attributes", {}).get("temperature_unit", "°F")
        w["unit"] = "celsius" if "C" in unit else "fahrenheit"
        return w

    # -- slides ---------------------------------------------------------------
    def slides(self):
        w = self.weather
        if not w:
            return []
        out = []
        if self.cfg.get("show_current", True) and w.get("temp") is not None:
            out.append("current")
        if self.cfg.get("show_daily", True) and len(w.get("daily") or []) >= 2:
            out.append("daily")
        if self.cfg.get("show_hourly", True) and len(w.get("hourly") or []) >= 6:
            out.append("hourly")
        return out

    def render(self, c, slide, t):
        w = self.weather
        if not w:
            return
        {"current": self._current, "daily": self._daily, "hourly": self._hourly}[slide](c, w, t)

    @staticmethod
    def _deg(v):
        return "--" if v is None else f"{round(v)}°"

    def _current(self, c, w, t):
        W, H = c.width, c.height
        draw_icon(c, w["condition"], 0, 0, H, t, w["is_day"])
        x0 = H + 3
        tw = c.text(x0, 1, self._deg(w["temp"]), "7x13B", temp_color(w["temp"], w.get("unit")))
        today = (w.get("daily") or [{}])[0]
        if today.get("hi") is not None:
            c.text_right(W - 1, 1, f"H{round(today['hi'])}°", "5x7", HI)
            c.text_right(W - 1, 9, f"L{round(today['lo'])}°", "5x7", LO)
        if w.get("feels") is not None and abs(w["feels"] - (w["temp"] or 0)) >= 3:
            c.text(x0 + tw + 3, 8, f"feels {round(w['feels'])}°", "4x6", GRAY, clip=(x0, W - 22))
        label = LABELS.get(w["condition"], w["condition"].replace("-", " ").title())
        if w["condition"] == "partlycloudy" and not w["is_day"]:
            label = "Partly cloudy"
        name = self.cfg.get("location_name") if self.cfg.get("source") != "homeassistant" else ""
        line = label + (f" · {name.split(',')[0]}" if name else "")
        c.scroll_text(x0, W, 16, line, "5x8", WHITE, t)
        bits = []
        if w.get("humidity") is not None:
            bits.append(f"{round(w['humidity'])}% RH")
        if w.get("wind") is not None:
            bits.append(f"{round(w['wind'])}{w.get('wind_unit') or ''} {w.get('wind_dir') or ''}".strip())
        if today.get("precip") is not None:
            bits.append(f"{round(today['precip'])}% rain")
        c.scroll_text(x0, W, H - 7, "  ".join(bits), "4x6", GRAY, t)

    def _daily(self, c, w, t):
        W, H = c.width, c.height
        days = (w.get("daily") or [])[:4]
        col = W // max(1, len(days))
        for i, d in enumerate(days):
            x = i * col
            try:
                name = "TODAY" if i == 0 else datetime.fromisoformat(d["date"]).strftime("%a").upper()
            except (ValueError, TypeError):
                name = ""
            c.text_centered(0, name, "4x6", WHITE if i == 0 else GRAY, x, x + col)
            draw_icon(c, d.get("condition"), x + (col - 16) // 2, 7, 16, t + i * 0.9, True)
            hi = "--" if d.get("hi") is None else str(round(d["hi"]))
            lo = "--" if d.get("lo") is None else str(round(d["lo"]))
            hw, lw = c.text_width(hi, "5x7"), c.text_width(lo, "5x7")
            sx = x + (col - hw - lw - 4) // 2
            c.text(sx, H - 7, hi, "5x7", HI)
            c.text(sx + hw + 4, H - 7, lo, "5x7", LO)
            if i:
                c.line(x, 2, x, H - 3, (25, 25, 25))

    def _hourly(self, c, w, t):
        W, H = c.width, c.height
        hours = (w.get("hourly") or [])[:24]
        temps = [h["temp"] for h in hours if h.get("temp") is not None]
        if not temps:
            return
        n = len(hours)
        step = (W - 1) / max(1, n - 1)
        lo, hi = min(temps), max(temps)
        y_top, y_bot = 9, 21

        def ty(v):
            return y_bot - (v - lo) / ((hi - lo) or 1) * (y_bot - y_top)

        # rain chance bars along the bottom
        for i, h in enumerate(hours):
            p = h.get("precip")
            if p:
                x = round(i * step)
                bh = max(1, round(p / 100 * 8))
                c.rect(x, H - bh, min(W - 1, x + max(1, round(step) - 1)), H - 1, RAIN_BAR)
        # temperature line, colored by temperature, drawn in progressively so it "animates in"
        reveal = min(1.0, t / 1.5)
        pts = [(round(i * step), round(ty(h["temp"])), h["temp"]) for i, h in enumerate(hours) if h.get("temp") is not None]
        upto = max(2, int(len(pts) * reveal))
        for (x0, y0, v0), (x1, y1, v1) in zip(pts[:upto], pts[1:upto]):
            c.line(x0, y0, x1, y1, temp_color((v0 + v1) / 2, w.get("unit")))
        # labels: hour marks every 6 h, min / max values
        for i, h in enumerate(hours):
            if i % 6 == 0:
                lab = "NOW" if i == 0 else h["time"].strftime("%-I%p")[:-1]
                x = round(i * step)
                c.text(min(x, W - c.text_width(lab, "4x6")), 0, lab, "4x6", DIM if i else GRAY)
        imax = max(range(len(pts)), key=lambda k: pts[k][2])
        imin = min(range(len(pts)), key=lambda k: pts[k][2])
        for k, col in ((imax, HI), (imin, LO)):
            x, y, v = pts[k]
            lab = f"{round(v)}°"
            lw = c.text_width(lab, "4x6")
            ly = y - 7 if k == imax else y + 2
            if ly < 7:            # would collide with the hour labels on the top row
                ly = y + 2
            c.text(max(0, min(W - lw, x - lw // 2)), max(0, min(H - 6, ly)), lab, "4x6", col)

    def status(self):
        s = super().status()
        w = self.weather or {}
        s["weather"] = {k: w.get(k) for k in ("condition", "temp", "humidity")} if w else None
        return s


def geocode(query, count=5):
    """Look up places by name with Open-Meteo's free geocoding API."""
    r = requests.get(GEOCODE_URL, params={"name": query, "count": count, "language": "en", "format": "json"}, timeout=10)
    r.raise_for_status()
    out = []
    for p in r.json().get("results") or []:
        label = ", ".join(x for x in (p.get("name"), p.get("admin1"), p.get("country_code")) if x)
        out.append({"label": label, "latitude": p.get("latitude"), "longitude": p.get("longitude")})
    return out

