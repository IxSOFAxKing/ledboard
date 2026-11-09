"""Home Assistant entity states via the REST API (long-lived access token)."""
from __future__ import annotations

import requests

from .base import Board, organize
from ..gfx import GRAY, RED, WHITE, YELLOW, hex_to_rgb

FRIENDLY = {
    ("door", "on"): "OPEN", ("door", "off"): "CLOSED",
    ("window", "on"): "OPEN", ("window", "off"): "CLOSED",
    ("garage_door", "on"): "OPEN", ("garage_door", "off"): "CLOSED",
    ("opening", "on"): "OPEN", ("opening", "off"): "CLOSED",
    ("motion", "on"): "MOTION", ("motion", "off"): "CLEAR",
    ("occupancy", "on"): "OCCUPIED", ("occupancy", "off"): "CLEAR",
    ("presence", "on"): "HOME", ("presence", "off"): "AWAY",
    ("moisture", "on"): "WET", ("moisture", "off"): "DRY",
}
STATE_COLORS = {
    "on": YELLOW, "open": YELLOW, "unlocked": YELLOW, "home": (30, 220, 60), "playing": (30, 220, 60),
    "off": GRAY, "closed": GRAY, "locked": (30, 220, 60), "not_home": GRAY, "idle": GRAY,
    "unavailable": RED, "unknown": RED,
    "heat": (255, 90, 0), "cool": (0, 150, 255), "heating": (255, 90, 0), "cooling": (0, 150, 255),
}


def format_value(st, ent):
    """Turn a HA state object + entity config into (text, color)."""
    attrs = st.get("attributes", {})
    if ent.get("attribute"):
        raw = attrs.get(ent["attribute"], "")
        unit = ent.get("unit", "")
    else:
        raw = st.get("state", "")
        unit = ent.get("unit", attrs.get("unit_of_measurement", ""))
    text = str(raw)
    color = STATE_COLORS.get(text.lower(), WHITE)
    try:
        num = float(raw)
        dec = ent.get("decimals")
        if dec in (None, ""):
            dec = 0 if num == int(num) or abs(num) >= 100 else 1
        text = f"{num:.{int(dec)}f}"
        color = WHITE
    except (TypeError, ValueError):
        friendly = FRIENDLY.get((attrs.get("device_class"), text.lower()))
        text = friendly or text.replace("_", " ").upper()
    if unit and text not in ("UNAVAILABLE", "UNKNOWN"):
        text += unit if unit in ("%", "°F", "°C", "°") else " " + unit
    if ent.get("color"):
        color = hex_to_rgb(ent["color"])
    return text, color


class HomeAssistantBoard(Board):
    name = "homeassistant"
    title = "Home Assistant"
    description = "Shows the state of any Home Assistant entities (sensors, lights, locks, thermostats...)."
    refresh = 15
    DEFAULTS = {
        "enabled": False, "duration": 8, "refresh": 15,
        "url": "http://homeassistant.local:8123", "token": "", "verify_ssl": True,
        "per_page": 3, "label_color": "#8a8a8a",
        "entities": [],   # [{entity_id, label, color, attribute, unit, decimals}]
    }
    FIELDS = [
        {"key": "url", "label": "Home Assistant URL", "type": "text"},
        {"key": "token", "label": "Long-lived access token", "type": "password",
         "help": "Create one in Home Assistant: your profile > Security > Long-lived access tokens."},
        {"key": "entities", "label": "Entities", "type": "entities"},
        {"key": "per_page", "label": "Entities per screen", "type": "select", "options": [1, 2, 3]},
        {"key": "label_color", "label": "Label color", "type": "color"},
        {"key": "duration", "label": "Seconds per screen", "type": "number"},
        {"key": "verify_ssl", "label": "Verify HTTPS certificate", "type": "bool"},
        {"key": "refresh", "label": "Refresh interval (s)", "type": "number"},
    ]
    FIELDS = organize(FIELDS, [('Connection', ['url', 'token', 'verify_ssl']), ('Entities', ['entities', 'per_page', 'label_color']), ('Timing', ['duration', 'refresh'])], {})

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.states: dict[str, dict] = {}

    def _req(self, path, method="GET", **kw):
        url = (self.cfg.get("url") or "").rstrip("/") + path
        return requests.request(method, url, timeout=10, verify=bool(self.cfg.get("verify_ssl", True)),
                                headers={"Authorization": f"Bearer {self.cfg.get('token', '')}",
                                         "Content-Type": "application/json"}, **kw)

    def update(self):
        if not (self.cfg.get("url") and self.cfg.get("token") and self.cfg.get("entities")):
            return
        r = self._req("/api/states")
        if r.status_code == 401:
            raise RuntimeError("Home Assistant rejected the token (401)")
        r.raise_for_status()
        wanted = {e["entity_id"] for e in self.cfg.get("entities", []) if e.get("entity_id")}
        self.states = {s["entity_id"]: s for s in r.json() if s["entity_id"] in wanted}

    def all_entities(self):
        """List every entity (used by the web UI's entity picker)."""
        r = self._req("/api/states")
        r.raise_for_status()
        return sorted(({"entity_id": s["entity_id"],
                        "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
                        "state": s.get("state")} for s in r.json()), key=lambda e: e["entity_id"])

    def _entities(self):
        return [e for e in self.cfg.get("entities", []) if e.get("entity_id") in self.states]

    def slides(self):
        ents = self._entities()
        n = max(1, int(self.cfg.get("per_page", 3)))
        return [i for i in range(0, len(ents), n)]

    def render(self, c, slide, t):
        n = max(1, int(self.cfg.get("per_page", 3)))
        ents = self._entities()[slide: slide + n]
        W = c.width
        label_color = hex_to_rgb(self.cfg.get("label_color"), GRAY)
        if n == 1 and ents:
            e = ents[0]
            st = self.states[e["entity_id"]]
            label = e.get("label") or st.get("attributes", {}).get("friendly_name", e["entity_id"])
            value, color = format_value(st, e)
            c.scroll_text(0, W, 1, label, "6x10", label_color, t, align="center")
            c.scroll_text(0, W, 14, value, "7x13B", color, t, align="center")
            return
        rows = {2: (3, 18), 3: (1, 12, 23)}.get(n, (1, 12, 23))
        font = "6x10" if n == 2 else "5x8"
        for e, y in zip(ents, rows):
            st = self.states[e["entity_id"]]
            label = e.get("label") or st.get("attributes", {}).get("friendly_name", e["entity_id"])
            value, color = format_value(st, e)
            vw = c.text_width(value, font)
            c.text(W - vw, y, value, font, color)
            c.scroll_text(0, W - vw - 3, y, label, font, label_color, t)

    def status(self):
        s = super().status()
        s["entities"] = {k: v.get("state") for k, v in self.states.items()}
        return s
