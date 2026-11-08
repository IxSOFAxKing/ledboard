"""Web UI + REST API."""
from __future__ import annotations

import logging
import os
import threading
import urllib.parse

import requests
from flask import Flask, Response, jsonify, redirect, request, send_from_directory

from .. import fonts
from ..boards import BOARD_CLASSES, LeagueBoard, board_fields
from ..config import MASK

log = logging.getLogger(__name__)
STATIC = os.path.join(os.path.dirname(__file__), "static")

HARDWARE_MAPPINGS = ["regular", "adafruit-hat", "adafruit-hat-pwm", "regular-pi1", "classic",
                     "classic-pi1", "compute-module"]


def create_app(engine):
    app = Flask(__name__, static_folder=None)
    app.json.sort_keys = False  # keep league / field order as declared
    config = engine.config

    @app.before_request
    def auth():
        pw = config.data["web"].get("password")
        if not pw:
            return None
        a = request.authorization
        if a and a.password == pw:
            return None
        return Response("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="LEDBoard"'})

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:p>")
    def static_files(p):
        return send_from_directory(STATIC, p)

    # -- state / preview ------------------------------------------------------
    @app.get("/api/state")
    def state():
        return jsonify(engine.state())

    @app.get("/api/frame.png")
    def frame():
        png = engine.frame_png()
        if png is None:
            return Response(status=204)
        return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})

    # -- config -------------------------------------------------------------------
    @app.get("/api/config")
    def get_config():
        schema = {name: {"title": cls.title, "description": cls.description, "fields": board_fields(cls),
                         "group": "Sports" if issubclass(cls, LeagueBoard) else "Other"}
                  for name, cls in BOARD_CLASSES.items()}
        return jsonify({"config": config.public(), "schema": schema, "fonts": fonts.available(),
                        "hardware_mappings": HARDWARE_MAPPINGS})

    @app.post("/api/config")
    def set_config():
        patch = request.get_json(force=True) or {}
        restart_needed = "display" in patch and _hw_changed(config.data["display"], patch["display"])
        config.update(patch)
        engine.apply_config()
        return jsonify({"ok": True, "restart_needed": restart_needed})

    # -- controls ---------------------------------------------------------------
    @app.post("/api/control")
    def control():
        j = request.get_json(force=True) or {}
        a = j.get("action")
        if a == "next":
            engine.next()
        elif a == "pause":
            engine.set_paused(True)
        elif a == "resume":
            engine.set_paused(False)
        elif a == "power":
            engine.set_power(bool(j.get("on", not engine.power)))
        elif a == "brightness":
            engine.set_brightness(j.get("value", 60))
        elif a == "jump":
            try:
                engine.jump(j.get("board"))
            except KeyError:
                return jsonify({"error": "unknown board"}), 400
        elif a == "simulate":
            b = engine.boards.get(j.get("board"))
            if not b or not hasattr(b, "simulate"):
                return jsonify({"error": "this board has no celebrations"}), 400
            try:
                b.simulate(j.get("kind") or "goal")
                engine.jump(j.get("board"))
            except Exception as e:
                return jsonify({"error": str(e)}), 400
        elif a == "refresh":
            for b in engine.boards.values():
                b.poke()
        else:
            return jsonify({"error": f"unknown action {a!r}"}), 400
        return jsonify({"ok": True, "state": engine.state()})

    @app.post("/api/message")
    def message():
        j = request.get_json(force=True) or {}
        if not j.get("text"):
            return jsonify({"error": "text required"}), 400
        engine.show_message(j["text"], j.get("seconds", 10), j.get("color", "#ffffff"), j.get("font", "7x13B"))
        return jsonify({"ok": True})

    @app.post("/api/restart")
    def restart():
        def later():
            engine.restart_requested = True
            engine.stop()
        threading.Timer(0.5, later).start()
        return jsonify({"ok": True})

    # -- spotify ---------------------------------------------------------------------
    @app.get("/api/spotify/auth-url")
    def spotify_auth_url():
        sp = engine.boards["spotify"]
        if not sp.cfg.get("client_id") or not sp.cfg.get("client_secret"):
            return jsonify({"error": "Save your Client ID and Client Secret first"}), 400
        return jsonify({"url": sp.auth_url(), "redirect_uri": sp.cfg.get("redirect_uri")})

    def _spotify_finish(code):
        sp = engine.boards["spotify"]
        token = sp.exchange_code(code)
        engine.save_board_value("spotify", "refresh_token", token)
        sp.poke()

    @app.get("/spotify/callback")
    def spotify_callback():
        if request.args.get("error"):
            return f"Spotify returned an error: {request.args['error']}", 400
        try:
            _spotify_finish(request.args.get("code", ""))
        except Exception as e:
            return f"Failed: {e}", 400
        return redirect("/#boards")

    @app.post("/api/spotify/exchange")
    def spotify_exchange():
        raw = ((request.get_json(force=True) or {}).get("code") or "").strip()
        code = raw
        if "code=" in raw:
            code = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query).get("code", [""])[0]
        if not code:
            return jsonify({"error": "Paste the full URL you were redirected to (it contains ?code=...)"}), 400
        try:
            _spotify_finish(code)
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True})

    @app.post("/api/spotify/disconnect")
    def spotify_disconnect():
        engine.save_board_value("spotify", "refresh_token", "")
        engine.boards["spotify"].access_token = None
        engine.boards["spotify"].now = None
        engine.boards["spotify"].players = {}
        return jsonify({"ok": True})

    # -- home assistant / plex helpers ------------------------------------------------
    def _creds(board, keys):
        """Use values from the request (unsaved form) falling back to saved config."""
        j = request.get_json(silent=True) or {}
        saved = engine.boards[board].cfg
        return {k: (j.get(k) if j.get(k) not in (None, "", MASK) else saved.get(k)) for k in keys}

    @app.post("/api/homeassistant/entities")
    def ha_entities():
        c = _creds("homeassistant", ["url", "token", "verify_ssl"])
        try:
            r = requests.get(c["url"].rstrip("/") + "/api/states", timeout=10,
                             verify=bool(c.get("verify_ssl", True)),
                             headers={"Authorization": f"Bearer {c['token']}"})
            r.raise_for_status()
            ents = sorted(({"entity_id": s["entity_id"],
                            "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
                            "state": s.get("state")} for s in r.json()), key=lambda e: e["entity_id"])
            return jsonify({"entities": ents})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/api/weather/geocode")
    def weather_geocode():
        from ..boards.weather import geocode
        q = (request.args.get("q") or "").strip()
        if len(q) < 2:
            return jsonify({"results": []})
        try:
            return jsonify({"results": geocode(q)})
        except Exception as e:
            from ..boards.base import short_error
            return jsonify({"error": f"Location search failed: {short_error(e)}"}), 400

    @app.post("/api/plex/test")
    def plex_test():
        c = _creds("plex", ["url", "token", "verify_ssl"])
        try:
            r = requests.get(c["url"].rstrip("/") + "/identity", timeout=10, verify=bool(c.get("verify_ssl")),
                             headers={"X-Plex-Token": c["token"], "Accept": "application/json"})
            r.raise_for_status()
            r2 = requests.get(c["url"].rstrip("/") + "/status/sessions", timeout=10,
                              verify=bool(c.get("verify_ssl")),
                              headers={"X-Plex-Token": c["token"], "Accept": "application/json"})
            r2.raise_for_status()
            n = (r2.json().get("MediaContainer") or {}).get("size", 0)
            return jsonify({"ok": True, "message": f"Connected. {n} active stream(s)."})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    return app


def _hw_changed(current, patch):
    return any(k not in ("brightness", "fps") and current.get(k) != v for k, v in patch.items())


def serve(engine, host, port):
    from werkzeug.serving import make_server
    app = create_app(engine)
    srv = make_server(host, port, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, name="web", daemon=True)
    t.start()
    log.info("Web UI on http://%s:%d", host, port)
    return srv
