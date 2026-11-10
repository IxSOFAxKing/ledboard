"""Severe weather alerts from the US National Weather Service (api.weather.gov), shown
through the alert system: they interrupt the display, repeat while in effect and wake
the display for extreme warnings."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from .base import Board, organize

log = logging.getLogger(__name__)

NWS_URL = "https://api.weather.gov/alerts/active"
USER_AGENT = "LEDBoard (https://github.com/IxSOFAxKing/ledboard)"
SEVERITY_ORDER = ["minor", "moderate", "severe", "extreme"]
SEVERITY_STYLE = {   # severity -> (alert priority, color)
    "extreme": ("critical", "#ff1a1a"),
    "severe": ("high", "#ff7a00"),
    "moderate": ("normal", "#ffd000"),
    "minor": ("low", "#3b9cff"),
}


def icon_for(event):
    e = (event or "").lower()
    for words, icon in ((("tornado",), "tornado"), (("thunderstorm", "lightning"), "storm"),
                        (("flood", "surge", "tsunami", "rain"), "water"), (("fire", "red flag", "smoke"), "fire"),
                        (("heat",), "thermometer"), (("freeze", "frost", "cold", "chill", "winter", "snow", "ice",
                                                      "blizzard", "sleet"), "thermometer")):
        if any(w in e for w in words):
            return icon
    return "warning"


def _local_time(iso):
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%a %-I:%M %p")
    except (TypeError, ValueError):
        return ""


class WeatherAlertsBoard(Board):
    name = "weather_alerts"
    title = "Severe Weather Alerts (US)"
    category = "Home & status"
    description = ("Warnings from the National Weather Service for your location interrupt the display, repeat while "
                   "in effect, and extreme ones (e.g. tornado warnings) wake the display until dismissed. US only.")
    refresh = 120
    DEFAULTS = {"enabled": False, "refresh": 120, "min_severity": "severe", "warnings_only": True,
                "repeat_minutes": 10, "latitude": "", "longitude": ""}
    FIELDS = [
        {"key": "min_severity", "label": "Show alerts at or above", "type": "select",
         "options": ["extreme", "severe", "moderate", "minor"],
         "option_labels": {"extreme": "Extreme (e.g. tornado warning)", "severe": "Severe (recommended)",
                           "moderate": "Moderate", "minor": "Minor (everything)"}},
        {"key": "warnings_only", "label": "Warnings only (skip watches and advisories)", "type": "bool"},
        {"key": "repeat_minutes", "label": "Repeat every (minutes) while in effect", "type": "number"},
        {"key": "latitude", "label": "Latitude", "type": "number", "placeholder": "Weather board's"},
        {"key": "longitude", "label": "Longitude", "type": "number", "placeholder": "Weather board's",
         "help": "Leave blank to use the Weather board's location."},
        {"key": "refresh", "label": "Check every (seconds)", "type": "number"},
    ]
    FIELDS = organize(FIELDS, [
        ("Basics", "Location", ["latitude", "longitude"]),
        ("Screens", "Alerts", ["min_severity", "warnings_only", "repeat_minutes"]),
        ("Advanced", "Timing", ["refresh"])], {})

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.active = {}          # NWS alert id -> our Alert
        self.feed = []            # last matching alerts (for the status page)

    def _location(self):
        lat, lon = self.cfg.get("latitude"), self.cfg.get("longitude")
        if lat in ("", None) or lon in ("", None):
            w = self.app.cfg["boards"].get("weather", {})
            lat, lon = w.get("latitude"), w.get("longitude")
        try:
            return round(float(lat), 4), round(float(lon), 4)
        except (TypeError, ValueError):
            return None

    def update(self):
        loc = self._location()
        if not loc:
            raise RuntimeError("Set a location here or on the Weather board")
        r = requests.get(NWS_URL, params={"point": f"{loc[0]},{loc[1]}", "status": "actual"}, timeout=15,
                         headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
        if r.status_code in (400, 404):
            raise RuntimeError("The National Weather Service only covers US locations")
        r.raise_for_status()
        self.process(r.json().get("features") or [])

    def process(self, features):
        """Turn NWS features into alerts (pure enough to test with fixtures)."""
        min_rank = SEVERITY_ORDER.index(self.cfg.get("min_severity", "severe"))
        now = datetime.now(timezone.utc)
        seen, feed = set(), []
        for f in features:
            p = f.get("properties") or {}
            sev = (p.get("severity") or "").lower()
            event = p.get("event") or "Weather alert"
            if sev not in SEVERITY_ORDER or SEVERITY_ORDER.index(sev) < min_rank:
                continue
            if self.cfg.get("warnings_only", True) and "warning" not in event.lower():
                continue
            if p.get("messageType") == "Cancel":
                continue
            ends = p.get("ends") or p.get("expires")
            try:
                left = (datetime.fromisoformat(ends) - now).total_seconds() if ends else 3 * 3600
            except ValueError:
                left = 3 * 3600
            if left <= 0:
                continue
            nid = p.get("id") or f.get("id")
            seen.add(nid)
            feed.append({"event": event, "severity": sev, "ends": _local_time(ends), "area": p.get("areaDesc", "")})
            if nid in self.active:
                continue            # already queued (or dismissed by you): leave it alone
            priority, color = SEVERITY_STYLE[sev]
            until = _local_time(ends)
            msg = " · ".join(x for x in ((f"until {until}" if until else ""), p.get("headline") or "",
                                         (p.get("instruction") or "").split("\n")[0][:160]) if x)
            self.active[nid] = self.app.alerts.add(
                event, msg, icon=icon_for(event), color=color, priority=priority, seconds=15,
                repeat_minutes=float(self.cfg.get("repeat_minutes", 10) or 10), expires_in=left,
                until_dismissed=priority == "critical", source="nws", replace_key=f"nws:{event}")
            log.info("NWS alert: %s (%s)", event, sev)
        # alerts that are no longer in the feed (expired / cancelled) go away
        for nid in [n for n in self.active if n not in seen]:
            self.app.alerts.dismiss(self.active.pop(nid).id)
        self.feed = feed

    def slides(self):
        return []                # shown through alerts, not as a rotation board

    def status(self):
        s = super().status()
        s["alerts"] = self.feed
        return s
