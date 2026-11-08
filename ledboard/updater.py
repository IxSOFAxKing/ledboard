"""Self-update from the git checkout LEDBoard runs from (git pull + pip install + restart)."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time

log = logging.getLogger(__name__)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK_EVERY = 6 * 3600


class Updater:
    def __init__(self, engine):
        self.engine = engine
        self.state = "idle"           # idle | checking | updating | restarting | error
        self.log = []
        self.info = {}                # result of the last check
        self.last_check = 0.0
        self._lock = threading.Lock()
        threading.Thread(target=self._auto_check, name="update-check", daemon=True).start()

    # -- helpers --------------------------------------------------------------------
    def _git(self, *args, timeout=60, strip=True):
        r = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip() or f"git {' '.join(args)} failed")
        return r.stdout.strip() if strip else r.stdout

    def _local_changes(self):
        # porcelain lines are "XY path"; don't strip, the first column can be a space
        out = self._git("status", "--porcelain", "--untracked-files=no", strip=False)
        return [line[3:] for line in out.splitlines() if line.strip()]

    def available(self):
        return os.path.isdir(os.path.join(REPO, ".git"))

    def version(self):
        from . import __version__
        out = {"version": __version__, "git": self.available()}
        if self.available():
            try:
                out["commit"] = self._git("rev-parse", "--short", "HEAD")
                out["date"] = self._git("log", "-1", "--format=%cd", "--date=format:%b %-d %Y %H:%M")
                out["branch"] = self._git("rev-parse", "--abbrev-ref", "HEAD")
            except Exception as e:
                out["error"] = str(e)
        return out

    def status(self):
        return {"state": self.state, "log": self.log[-30:], "last_check": self.last_check, **self.info,
                "current": self.version()}

    # -- check / apply -----------------------------------------------------------------
    def check(self):
        if not self.available():
            self.info = {"error": "LEDBoard isn't running from a git checkout, so it can't update itself."}
            return self.info
        with self._lock:
            prev = self.state
            self.state = "checking"
            try:
                branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
                self._git("fetch", "--quiet", "origin", branch, timeout=90)
                behind = int(self._git("rev-list", "--count", f"HEAD..origin/{branch}") or 0)
                changes = self._git("log", "--format=%s", "-n", "15", f"HEAD..origin/{branch}").splitlines() if behind else []
                dirty = self._local_changes()
                self.info = {"behind": behind, "changes": changes, "local_changes": dirty, "error": None,
                             "latest": self._git("rev-parse", "--short", f"origin/{branch}")}
            except Exception as e:
                self.info = {"error": f"Couldn't check for updates: {e}"}
            finally:
                self.last_check = time.time()
                self.state = prev if prev != "checking" else "idle"
        return self.info

    def apply(self):
        """Pull, install requirements, restart. Runs in a background thread."""
        if self.state in ("updating", "restarting"):
            return False
        self.state = "updating"
        self.log = []
        threading.Thread(target=self._apply, name="update", daemon=True).start()
        return True

    def _step(self, msg):
        log.info("update: %s", msg)
        self.log.append(msg)

    def _apply(self):
        try:
            branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
            dirty = self._local_changes()
            if dirty:
                raise RuntimeError("these files were edited on the Pi; not overwriting them: " + ", ".join(dirty))
            self._step(f"Downloading the latest version ({branch})…")
            before = self._git("rev-parse", "--short", "HEAD")
            self._step(self._git("pull", "--ff-only", "origin", branch, timeout=180) or "pulled")
            after = self._git("rev-parse", "--short", "HEAD")
            self._step(f"Updated {before} → {after}")
            req = os.path.join(REPO, "requirements.txt")
            if os.path.exists(req):
                self._step("Installing Python packages (can take a minute on a Pi Zero)…")
                r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                                    "-r", req], capture_output=True, text=True, timeout=1200)
                if r.returncode != 0:
                    raise RuntimeError("pip install failed:\n" + (r.stderr or r.stdout)[-800:])
                self._step("Packages up to date")
            self._step("Restarting LEDBoard…")
            self.state = "restarting"
            self.info = {"behind": 0, "changes": [], "local_changes": [], "error": None}
            time.sleep(1.0)
            self.engine.restart_requested = True
            self.engine.stop()
        except Exception as e:
            self.state = "error"
            self._step(f"Update failed: {e}")

    def _auto_check(self):
        time.sleep(60)
        while True:
            try:
                if self.available() and time.time() - self.last_check > CHECK_EVERY:
                    self.check()
            except Exception:
                pass
            time.sleep(600)
