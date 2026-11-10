"""Weather with animated icons.

Sources:
  * Open-Meteo (default): free, no API key. Location by latitude/longitude; the web UI
    can look a place up by name (Open-Meteo geocoding).
  * Home Assistant: any weather.* entity (uses the URL/token from the Home Assistant board).
Slides: current conditions, 4-day forecast, next-24-hours graph (each optional).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import requests

from .base import Board, organize
from .weather_extra import SCREENS
from .weather_icons import LABELS, WMO, draw_icon
from ..gfx import DIM, GRAY, WHITE

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
HI = (255, 170, 60)
LO = (90, 160, 255)
RAIN_BAR = (30, 90, 220)
RAIN_PEAK = (110, 170, 255)
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
        "wind_deg": cur.get("wind_direction_10m"), "gust": cur.get("wind_gusts_10m"),
        "daily": [], "hourly": [], "hours": [], "rain15": [],
    }

    def col(block, key, i):
        v = block.get(key) or []
        return v[i] if i < len(v) else None

    today = datetime.now().date().isoformat()
    for i, day in enumerate(daily.get("time") or []):
        d = {
            "date": day,
            "condition": WMO.get(col(daily, "weather_code", i), "cloudy"),
            "hi": col(daily, "temperature_2m_max", i),
            "lo": col(daily, "temperature_2m_min", i),
            "precip": col(daily, "precipitation_probability_max", i),
            "uv": col(daily, "uv_index_max", i),
            "sunrise": _local(col(daily, "sunrise", i)),
            "sunset": _local(col(daily, "sunset", i)),
        }
        if day < today:                        # past_days=1 -> yesterday, kept for "compared to yesterday"
            out["yesterday"] = d
            continue
        out["daily"].append(d)
    if out["daily"]:
        out["sunrise"], out["sunset"] = out["daily"][0]["sunrise"], out["daily"][0]["sunset"]
        if len(out["daily"]) > 1:
            out["sunrise_next"] = out["daily"][1]["sunrise"]
    now = datetime.now()
    hour0 = now.replace(minute=0, second=0, microsecond=0)
    for i, ts in enumerate(hourly.get("time") or []):
        when = _local(ts)
        if when is None or when < hour0 - timedelta(hours=1) or when > hour0 + timedelta(hours=40):
            continue
        h = {"time": when, "temp": col(hourly, "temperature_2m", i), "precip": col(hourly, "precipitation_probability", i),
             "condition": WMO.get(col(hourly, "weather_code", i), None), "uv": col(hourly, "uv_index", i),
             "is_day": bool(col(hourly, "is_day", i) if col(hourly, "is_day", i) is not None else 1)}
        out["hours"].append(h)
        if when >= hour0 and len(out["hourly"]) < 24:
            out["hourly"].append(h)
    m15 = j.get("minutely_15") or {}
    for i, ts in enumerate(m15.get("time") or []):
        when = _local(ts)
        if when is None or when < now - timedelta(minutes=15):
            continue
        out["rain15"].append({"time": when, "mm": col(m15, "precipitation", i) or 0.0})
        if len(out["rain15"]) >= 8:
            break
    return out


def _local(ts):
    """ISO time string (local, or with an offset) -> naive local datetime."""
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone().replace(tzinfo=None) if d.tzinfo else d


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
        "wind_unit": unit.replace("km/h", "kmh"), "daily": [], "hourly": [], "hours": [], "rain15": [],
        "wind_deg": a.get("wind_bearing") if isinstance(a.get("wind_bearing"), (int, float)) else None,
        "gust": a.get("wind_gust_speed"), "uv_now": a.get("uv_index"),
    }
    for f in daily or []:
        out["daily"].append({"date": (f.get("datetime") or "")[:10], "condition": f.get("condition"),
                             "hi": f.get("temperature"), "lo": f.get("templow"),
                             "precip": f.get("precipitation_probability"), "uv": f.get("uv_index")})
    for f in (hourly or [])[:40]:
        when = _local(f.get("datetime"))
        if when is None:
            continue
        h = {"time": when, "temp": f.get("temperature"), "precip": f.get("precipitation_probability"),
             "condition": f.get("condition"), "uv": f.get("uv_index"),
             "is_day": f.get("is_daytime", 6 <= when.hour < 19) if f.get("is_daytime") is not None else 6 <= when.hour < 19}
        out["hours"].append(h)
        if len(out["hourly"]) < 24:
            out["hourly"].append(h)
    return out


ALL_SCREENS = [("current", "show_current", "Current conditions"),
               ("daily", "show_daily", "4-day forecast"),
               ("hourly", "show_hourly", "Next 24 hours graph")] + \
              [(slide, key, label) for slide, (key, label, _h, _r) in SCREENS.items()]


def screen_order(cfg):
    """Every slide id, in the user's order (new / unknown ids fall back to the default order)."""
    known = [s for s, _k, _l in ALL_SCREENS]
    order = [s for s in (cfg.get("screen_order") or []) if s in known]
    return order + [s for s in known if s not in order]


class WeatherBoard(Board):
    name = "weather"
    title = "Weather"
    description = ("Current conditions, forecast and a 24-hour graph with animated icons, plus optional screens: sun arc, "
                   "rain soon, wind, week, living scene, UV + air quality, moon phase, day parts and vs. yesterday.")
    refresh = 600
    DEFAULTS = {
        "enabled": False, "duration": 10, "refresh": 600,
        "source": "openmeteo", "location_name": "", "latitude": "", "longitude": "",
        "ha_entity": "", "units": "fahrenheit",
        "show_current": True, "show_daily": True, "show_hourly": True,
        "show_sun": False, "show_rain_soon": False, "show_wind": False, "show_week": False, "show_scene": False,
        "show_air": False, "show_moon": False, "show_parts": False, "show_yesterday": False,
        "aqi_scale": "us", "yesterday_threshold": 8,
        "hourly_rain": True, "hourly_rain_min": 20, "hourly_rain_legend": True,
        "screen_order": [],          # slide ids in the order to show them ([] = default order)
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
        {"key": "screen_order", "label": "Screens", "type": "screens", "section": "Screens",
         "items": [{"slide": slide, "toggle": key, "label": label} for slide, key, label in ALL_SCREENS],
         "help": "Switch screens on or off, and use the arrows to set the order they're shown in."},
        {"key": "aqi_scale", "label": "Air quality scale", "type": "select", "section": "Screens",
         "options": ["us", "european"], "option_labels": {"us": "US AQI (0-500)", "european": "European AQI (0-100+)"},
         "show_if": {"show_air": True}},
        {"key": "yesterday_threshold", "label": "Show 'compared to yesterday' when highs differ by at least (degrees)",
         "type": "number", "section": "Screens", "show_if": {"show_yesterday": True}},
        {"key": "hourly_rain", "label": "24-hour graph: show rain chance bars", "type": "bool", "section": "Screens",
         "show_if": {"show_hourly": True},
         "help": "Blue bars along the bottom; taller = more likely to rain that hour."},
        {"key": "hourly_rain_min", "label": "...only for hours with at least this chance (%)", "type": "number",
         "section": "Screens", "show_if": {"show_hourly": True, "hourly_rain": True},
         "help": "Hides the faint 1-pixel bars small chances make (0 = show every hour)."},
        {"key": "hourly_rain_legend", "label": "...and a rain legend (drop + the highest chance, top right)",
         "type": "bool", "section": "Screens", "show_if": {"show_hourly": True, "hourly_rain": True}},
        {"key": "duration", "label": "Seconds per screen", "type": "number", "section": "Display"},
        {"key": "refresh", "label": "Refresh interval (s)", "type": "number", "section": "Display"},
    ]
    FIELDS = organize(FIELDS, [('Source', ['source', 'location_name', 'latitude', 'longitude', 'ha_entity', 'units']), ('Screens', ['screen_order', 'aqi_scale', 'yesterday_threshold']), ('Timing', ['duration', 'refresh'])], {})

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
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 8, "past_days": 1,
            "temperature_unit": "celsius" if metric else "fahrenheit",
            "wind_speed_unit": "kmh" if metric else "mph",
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,"
                       "wind_speed_10m,wind_direction_10m,wind_gusts_10m",
            "hourly": "temperature_2m,precipitation_probability,weather_code,uv_index,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "sunrise,sunset,uv_index_max",
            "minutely_15": "precipitation", "forecast_minutely_15": 12,
        }
        r = requests.get(FORECAST_URL, params=params, timeout=15)
        r.raise_for_status()
        w = parse_open_meteo(r.json())
        w["unit"] = "celsius" if metric else "fahrenheit"
        if self.cfg.get("show_air"):
            w["aqi"] = self._air_quality(lat, lon)
        return w

    def _air_quality(self, lat, lon):
        """Current air quality index from Open-Meteo's free air-quality API (None if unavailable)."""
        scale = "european_aqi" if self.cfg.get("aqi_scale") == "european" else "us_aqi"
        try:
            r = requests.get(AIR_URL, params={"latitude": lat, "longitude": lon, "current": scale, "timezone": "auto"},
                             timeout=15)
            r.raise_for_status()
            v = (r.json().get("current") or {}).get(scale)
            return None if v is None else {"value": v, "scale": scale}
        except Exception as e:
            log.info("air quality unavailable: %s", e)
            return None

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
        sun_up, sun_attr = True, {}
        try:
            s = requests.get(f"{base}/api/states/sun.sun", headers=h, timeout=10, verify=verify)
            if s.ok:
                sun_up = s.json().get("state") != "below_horizon"
                sun_attr = s.json().get("attributes") or {}
        except requests.RequestException:
            pass
        w = parse_ha(state, forecasts["daily"], forecasts["hourly"], sun_up)
        rise, sett = _local(sun_attr.get("next_rising")), _local(sun_attr.get("next_setting"))
        if rise and sett:
            if sun_up:            # today's sunrise already happened: next_rising is tomorrow's
                w["sunrise"], w["sunset"], w["sunrise_next"] = rise - timedelta(days=1), sett, rise
            elif datetime.now().hour >= 12:   # evening: both "next" times are tomorrow's
                w["sunrise"], w["sunset"], w["sunrise_next"] = rise - timedelta(days=1), sett - timedelta(days=1), rise
            else:                             # before dawn: both are today's
                w["sunrise"], w["sunset"], w["sunrise_next"] = rise, sett, rise
        unit = state.get("attributes", {}).get("temperature_unit", "°F")
        w["unit"] = "celsius" if "C" in unit else "fahrenheit"
        return w

    # -- slides ---------------------------------------------------------------
    def slides(self):
        w = self.weather
        if not w:
            return []
        builtin = {
            "current": ("show_current", lambda: w.get("temp") is not None),
            "daily": ("show_daily", lambda: len(w.get("daily") or []) >= 2),
            "hourly": ("show_hourly", lambda: len(w.get("hourly") or []) >= 6),
        }
        out = []
        for slide in screen_order(self.cfg):
            try:
                if slide in builtin:
                    key, has = builtin[slide]
                    if self.cfg.get(key, True) and has():
                        out.append(slide)
                elif self.cfg.get(SCREENS[slide][0]) and SCREENS[slide][2](w, self.cfg):
                    out.append(slide)
            except Exception as e:          # a missing field shouldn't take the board down
                log.debug("weather %s skipped: %s", slide, e)
        return out

    def render(self, c, slide, t):
        w = self.weather
        if not w:
            return
        if slide in SCREENS:
            SCREENS[slide][3](c, w, t, self.cfg)
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

        # rain chance bars along the bottom (hours below the minimum chance are left out)
        show_rain = self.cfg.get("hourly_rain", True)
        try:
            rain_min = float(self.cfg.get("hourly_rain_min", 20) or 0)
        except (TypeError, ValueError):
            rain_min = 20.0
        rainy = [(i, h["precip"]) for i, h in enumerate(hours)
                 if show_rain and h.get("precip") and h["precip"] >= max(1, rain_min)]
        peak = max(rainy, key=lambda ip: ip[1]) if rainy else None
        for i, p in rainy:
            x = round(i * step)
            bh = max(1, round(p / 100 * 8))
            col = RAIN_PEAK if peak and i == peak[0] else RAIN_BAR
            c.rect(x, H - bh, min(W - 1, x + max(1, round(step) - 1)), H - 1, col)
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
        # legend: a raindrop and the highest chance (its bar is drawn lighter), top-right corner
        if peak and self.cfg.get("hourly_rain_legend", True):
            txt = f"{round(peak[1])}%"
            tw = c.text_width(txt, "4x6")
            lx = W - tw - 5
            c.rect(lx - 1, 0, W - 1, 6, (0, 0, 0))
            for dx, dy in ((1, 0), (1, 1), (0, 2), (1, 2), (2, 2), (0, 3), (1, 3), (2, 3), (1, 4)):
                c.pixel(lx + dx, 1 + dy, RAIN_PEAK)
            c.text(lx + 4, 0, txt, "4x6", RAIN_PEAK)

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

