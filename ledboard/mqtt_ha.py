"""Home Assistant integration over MQTT (discovery).

LEDBoard shows up in Home Assistant as a device with:
  light   Display          on/off + brightness
  select  Board            current board; choosing one jumps to it
  switch  Pause rotation
  button  Next board, Dismiss alerts
  text    Message          flashes the text on the board
  notify  Alert            sends an alert (plain text, or JSON with title/message/icon/color/priority)
Commands also work on raw topics, e.g. <base>/alert/set with a JSON payload.
"""
from __future__ import annotations

import json
import logging
import threading
import time

log = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt
except ImportError:          # optional dependency
    mqtt = None


class MQTTBridge:
    def __init__(self, engine):
        self.engine = engine
        self.client = None
        self.connected = False
        self.last_error = None
        self._published = {}
        self._stop = threading.Event()
        self._thread = None
        self._cfg_used = None
        self._discovery_sent = set()

    # -- config / lifecycle ----------------------------------------------------------
    @property
    def cfg(self):
        return self.engine.cfg.get("mqtt", {})

    def _ident(self):
        return (self.cfg.get("base_topic") or "ledboard").strip("/")

    def status(self):
        if not self.cfg.get("enabled"):
            return {"enabled": False}
        if mqtt is None:
            return {"enabled": True, "connected": False,
                    "error": "paho-mqtt isn't installed. Run: sudo /opt/ledboard/venv/bin/pip install paho-mqtt"}
        return {"enabled": True, "connected": self.connected, "error": self.last_error,
                "broker": f"{self.cfg.get('host')}:{self.cfg.get('port', 1883)}"}

    def start(self):
        self._cfg_used = json.dumps(self.cfg, sort_keys=True)
        if not self.cfg.get("enabled") or not self.cfg.get("host"):
            return
        if mqtt is None:
            log.warning("MQTT enabled but paho-mqtt is not installed")
            return
        base = self._ident()
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{base}-{int(time.time())}")
        except AttributeError:  # paho-mqtt 1.x
            client = mqtt.Client(client_id=f"{base}-{int(time.time())}")
        if self.cfg.get("username"):
            client.username_pw_set(self.cfg.get("username"), self.cfg.get("password") or None)
        client.will_set(f"{base}/status", "offline", qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=2, max_delay=60)
        self.client = client
        self._published = {}
        self._discovery_sent = set()
        try:
            client.connect_async(self.cfg["host"], int(self.cfg.get("port") or 1883), keepalive=30)
            client.loop_start()
        except Exception as e:
            self.last_error = str(e)
            log.warning("MQTT connect failed: %s", e)
        self._stop.clear()
        self._thread = threading.Thread(target=self._state_loop, name="mqtt-state", daemon=True)
        self._thread.start()

    def stop(self, remove_entities=False):
        self._stop.set()
        if self.client:
            try:
                if remove_entities and self.connected:
                    for topic in list(self._discovery_sent):
                        self.client.publish(topic, "", retain=True)
                self.client.publish(f"{self._ident()}/status", "offline", retain=True)
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.connected = False

    def reload(self):
        """Called after settings are saved: reconnect only if MQTT settings changed."""
        new = json.dumps(self.cfg, sort_keys=True)
        if new == self._cfg_used:
            self._publish_discovery()           # board list may have changed
            return
        self.stop(remove_entities=not self.cfg.get("enabled"))
        self.start()

    # -- mqtt callbacks ---------------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        code = getattr(rc, "value", rc)
        if code != 0:
            self.connected = False
            self.last_error = f"broker refused connection ({rc}) - check username/password"
            log.warning("MQTT: %s", self.last_error)
            return
        self.connected = True
        self.last_error = None
        base = self._ident()
        client.publish(f"{base}/status", "online", qos=1, retain=True)
        for sub in ("light/set", "board/set", "pause/set", "next/press", "dismiss/press", "message/set", "alert/set"):
            client.subscribe(f"{base}/{sub}")
        client.subscribe(f"{self.cfg.get('discovery_prefix') or 'homeassistant'}/status")   # HA restarts
        self._published = {}
        self._discovery_sent = set()
        self._publish_discovery()
        log.info("MQTT connected to %s", self.cfg.get("host"))

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False

    def _on_message(self, client, userdata, msg):
        base = self._ident()
        topic = msg.topic
        payload = msg.payload.decode("utf-8", "replace").strip()
        e = self.engine
        try:
            if topic.endswith("/status") and not topic.startswith(base):
                if payload == "online":            # Home Assistant restarted: re-announce
                    self._discovery_sent = set()
                    self._published = {}
                    self._publish_discovery()
                return
            if topic == f"{base}/light/set":
                j = _json(payload) or {"state": payload}
                if "brightness" in j:
                    e.set_brightness(int(j["brightness"]))
                if str(j.get("state", "")).upper() in ("ON", "OFF"):
                    e.set_power(str(j["state"]).upper() == "ON")
            elif topic == f"{base}/board/set":
                name = self._board_by_title(payload)
                if name:
                    e.jump(name)
            elif topic == f"{base}/pause/set":
                e.set_paused(payload.upper() == "ON")
            elif topic == f"{base}/next/press":
                e.next()
            elif topic == f"{base}/dismiss/press":
                e.alerts.dismiss("all")
            elif topic == f"{base}/message/set":
                if payload:
                    e.show_message(payload, 10)
            elif topic == f"{base}/alert/set":
                j = _json(payload)
                if j is None:
                    j = {"title": payload}
                if j.get("title") or j.get("message"):
                    e.alerts.add(j.get("title") or j.get("message"), j.get("message", "") if j.get("title") else "",
                                 icon=j.get("icon", "bell"), color=j.get("color", "#ffaa00"),
                                 priority=j.get("priority", "normal"), seconds=j.get("seconds", 10),
                                 repeat_minutes=j.get("repeat_minutes", 0), until_dismissed=j.get("until_dismissed"),
                                 source="mqtt")
        except Exception as ex:
            log.warning("MQTT command on %s failed: %s", topic, ex)
        self._published.pop(topic.replace("/set", "/state"), None)   # force a fresh state publish

    # -- discovery + state ---------------------------------------------------------------------
    def _board_titles(self):
        e = self.engine
        return {n: e.boards[n].title for n in e.cfg["rotation"] if n in e.boards and e.boards[n].enabled}

    def _board_by_title(self, title):
        for n, b in self.engine.boards.items():
            if b.title == title or n == title:
                return n
        return None

    def _publish_discovery(self):
        if not (self.client and self.connected):
            return
        base = self._ident()
        prefix = self.cfg.get("discovery_prefix") or "homeassistant"
        node = base.replace("/", "_")
        from . import __version__
        device = {"identifiers": [node], "name": self.cfg.get("device_name") or "LEDBoard",
                  "manufacturer": "LEDBoard", "model": f"{self.engine.width}x{self.engine.height} LED matrix",
                  "sw_version": __version__}
        common = {"availability_topic": f"{base}/status", "device": device}
        boards = list(self._board_titles().values()) or ["Clock"]
        entities = {
            ("light", "display"): {"name": "Display", "schema": "json", "command_topic": f"{base}/light/set",
                                   "state_topic": f"{base}/light/state", "brightness": True, "brightness_scale": 100,
                                   "icon": "mdi:led-strip-variant"},
            ("select", "board"): {"name": "Board", "command_topic": f"{base}/board/set",
                                  "state_topic": f"{base}/board/state", "options": boards, "icon": "mdi:view-carousel"},
            ("switch", "pause"): {"name": "Pause rotation", "command_topic": f"{base}/pause/set",
                                  "state_topic": f"{base}/pause/state", "icon": "mdi:pause"},
            ("button", "next"): {"name": "Next board", "command_topic": f"{base}/next/press", "icon": "mdi:skip-next"},
            ("button", "dismiss"): {"name": "Dismiss alerts", "command_topic": f"{base}/dismiss/press",
                                    "icon": "mdi:bell-off"},
            ("text", "message"): {"name": "Message", "command_topic": f"{base}/message/set", "max": 120,
                                  "icon": "mdi:message-text"},
            ("notify", "alert"): {"name": "Alert", "command_topic": f"{base}/alert/set", "icon": "mdi:alert"},
            ("sensor", "alerts"): {"name": "Active alerts", "state_topic": f"{base}/alerts/state",
                                   "icon": "mdi:bell-ring"},
        }
        for (component, obj), conf in entities.items():
            topic = f"{prefix}/{component}/{node}/{obj}/config"
            payload = dict(common, unique_id=f"{node}_{obj}", object_id=f"{node}_{obj}", **conf)
            self.client.publish(topic, json.dumps(payload), qos=1, retain=True)
            self._discovery_sent.add(topic)
        self._published.pop(f"{base}/board/state", None)

    def _state_loop(self):
        while not self._stop.wait(1.0):
            if not (self.client and self.connected):
                continue
            try:
                self._publish_state()
            except Exception as e:
                log.debug("MQTT state publish failed: %s", e)

    def _publish_state(self):
        e = self.engine
        base = self._ident()
        cur = e.cur[0] if e.cur else None
        states = {
            f"{base}/light/state": json.dumps({"state": "ON" if e.power else "OFF",
                                               "brightness": int(e.cfg["display"]["brightness"])}),
            f"{base}/board/state": e.boards[cur].title if cur in e.boards else "",
            f"{base}/pause/state": "ON" if e.paused else "OFF",
            f"{base}/alerts/state": str(len(e.alerts.active())),
        }
        for topic, value in states.items():
            if self._published.get(topic) != value:
                self.client.publish(topic, value, qos=0, retain=True)
                self._published[topic] = value


def _json(s):
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except (ValueError, TypeError):
        return None
