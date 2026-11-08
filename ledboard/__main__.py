"""Entry point: python -m ledboard [run|import-sportsmatrix PATH] [options]"""
from __future__ import annotations

import argparse
import logging
import signal
import sys

from .boards import board_defaults, migrate
from .config import DEFAULT_PATH, Config, import_sportsmatrix


def main(argv=None):
    p = argparse.ArgumentParser(prog="ledboard", description="LED matrix scoreboard")
    p.add_argument("command", nargs="?", default="run", choices=["run", "import-sportsmatrix"])
    p.add_argument("path", nargs="?", default="/etc/sportsmatrix.conf",
                   help="sportsmatrix.conf to import hardware settings from")
    p.add_argument("-c", "--config", default=DEFAULT_PATH, help=f"config file (default {DEFAULT_PATH})")
    p.add_argument("--simulate", action="store_true", help="don't drive LED hardware (web preview only)")
    p.add_argument("--port", type=int, help="override web UI port")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    cfg = Config(a.config, board_defaults(), migrate)

    if a.command == "import-sportsmatrix":
        found = import_sportsmatrix(a.path, cfg)
        print(f"Imported {len(found)} hardware settings into {a.config}: {found}")
        return 0

    from .engine import Engine
    from .web import serve

    engine = Engine(cfg, simulate=a.simulate)
    web = cfg.data["web"]
    srv = serve(engine, web.get("host", "0.0.0.0"), a.port or int(web.get("port", 8080)))

    def handle(sig, frame):
        engine.stop()
    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    try:
        engine.run()
    finally:
        srv.shutdown()
    if engine.restart_requested:
        import os
        logging.info("Restarting...")
        os.execv(sys.executable, [sys.executable, "-m", "ledboard"] + sys.argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
