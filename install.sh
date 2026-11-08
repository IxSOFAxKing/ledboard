#!/usr/bin/env bash
# LEDBoard installer for Raspberry Pi OS. Run from the repo:  sudo ./install.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
CONF=/etc/ledboard/config.json

if [ "$(id -u)" -ne 0 ]; then echo "Please run with sudo: sudo ./install.sh"; exit 1; fi

echo "==> Installing system packages"
# Finish any package install that was interrupted earlier (otherwise apt refuses to run)
dpkg --configure -a
apt-get update
apt-get install -y python3-venv python3-dev cython3 git build-essential cmake \
  libjpeg-dev zlib1g-dev libfreetype6-dev

echo "==> Creating virtualenv in $DIR/venv"
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --upgrade pip wheel
"$DIR/venv/bin/pip" install -r "$DIR/requirements.txt"

# Prefer a pre-built wheel (built by .github/workflows/rgbmatrix-wheels.yml) so the Pi
# doesn't have to compile anything. Falls back to compiling if none matches.
if ! "$DIR/venv/bin/python" -c "import rgbmatrix" 2>/dev/null; then
  REPO=$(git -C "$DIR" config --get remote.origin.url 2>/dev/null | sed -E 's#(\.git)?$##; s#.*github\.com[:/]##')
  REPO=${REPO:-IxSOFAxKing/ledboard}
  echo "==> Looking for a pre-built rgbmatrix wheel in $REPO"
  if WHEEL=$("$DIR/venv/bin/python" "$DIR/deploy/fetch_wheel.py" "$REPO" "$DIR/.build/wheels"); then
    echo "    Installing $(basename "$WHEEL")"
    "$DIR/venv/bin/pip" install "$WHEEL" || echo "    Pre-built wheel didn't install; will compile instead"
  fi
fi

if ! "$DIR/venv/bin/python" -c "import rgbmatrix" 2>/dev/null; then
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
JOBS=$(nproc)
if [ "$MEM_MB" -lt 1500 ]; then
  # Low-RAM boards (Pi Zero 2 W, Pi 3): compiling with every core at once runs the
  # Pi out of memory and makes it unresponsive. Use one compiler job and more swap.
  JOBS=1
  if [ -f /etc/dphys-swapfile ]; then
    CUR=$(awk -F= '/^CONF_SWAPSIZE/ {print $2}' /etc/dphys-swapfile)
    if [ "${CUR:-0}" -lt 1024 ]; then
      echo "==> Low memory (${MEM_MB} MB): raising swap to 1 GB for the build"
      sed -i 's/^#\?CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
      grep -q '^CONF_SWAPSIZE' /etc/dphys-swapfile || echo 'CONF_SWAPSIZE=1024' >> /etc/dphys-swapfile
      grep -q '^CONF_MAXSWAP' /etc/dphys-swapfile || echo 'CONF_MAXSWAP=2048' >> /etc/dphys-swapfile
      systemctl restart dphys-swapfile || true
    fi
  fi
fi
export CMAKE_BUILD_PARALLEL_LEVEL=$JOBS MAKEFLAGS="-j$JOBS"

if ! "$DIR/venv/bin/python" -c "import rgbmatrix" 2>/dev/null; then
  echo "==> Building hzeller/rpi-rgb-led-matrix Python bindings ($JOBS compile job(s))"
  echo "    This can take 20-40 minutes on a Pi Zero 2 W. Don't interrupt it."
  SRC="$DIR/.build/rpi-rgb-led-matrix"
  if [ ! -d "$SRC" ]; then
    git clone --depth 1 https://github.com/hzeller/rpi-rgb-led-matrix.git "$SRC"
  fi
  "$DIR/venv/bin/pip" install -v "$SRC" 2>&1 | grep --line-buffered -E "Building|\[ *[0-9]+%\]|error|Successfully" || true
fi
fi
if ! "$DIR/venv/bin/python" -c "import rgbmatrix; print('rgbmatrix OK')"; then
  echo "!! Building the rgbmatrix bindings failed. Re-run with full output to see why:"
  echo "   sudo $DIR/venv/bin/pip install -v $DIR/.build/rpi-rgb-led-matrix"
  exit 1
fi

echo "==> Config"
mkdir -p /etc/ledboard /var/cache/ledboard
if [ ! -f "$CONF" ]; then
  if [ -f /etc/sportsmatrix.conf ]; then
    echo "    Importing panel/hardware settings from /etc/sportsmatrix.conf"
    (cd "$DIR" && "$DIR/venv/bin/python" -m ledboard -c "$CONF" import-sportsmatrix /etc/sportsmatrix.conf)
  else
    (cd "$DIR" && "$DIR/venv/bin/python" -c "
from ledboard.config import Config; from ledboard.boards import board_defaults
Config('$CONF', board_defaults()).save()")
  fi
fi

# The on-board sound driver conflicts with the matrix timing hardware.
if lsmod | grep -q snd_bcm2835; then
  echo "==> Blacklisting snd_bcm2835 (onboard audio) - a reboot is required"
  echo "blacklist snd_bcm2835" > /etc/modprobe.d/blacklist-rgb-matrix.conf
  NEED_REBOOT=1
fi

# Only one program can drive the panels at a time.
for svc in sportsmatrix; do
  if systemctl list-unit-files | grep -q "^$svc.service"; then
    read -r -p "==> Found the '$svc' service. Stop and disable it so LEDBoard can use the panels? [Y/n] " ans
    if [ "${ans:-Y}" != "n" ] && [ "${ans:-Y}" != "N" ]; then
      systemctl disable --now "$svc" || true
    fi
  fi
done

echo "==> Installing systemd service"
sed "s#__DIR__#$DIR#g" "$DIR/deploy/ledboard.service" > /etc/systemd/system/ledboard.service
systemctl daemon-reload
systemctl enable ledboard
if [ "${NEED_REBOOT:-0}" = "1" ]; then
  echo; echo "Done. Reboot now (sudo reboot); LEDBoard will start automatically."
else
  systemctl restart ledboard
  echo; echo "Done. LEDBoard is running."
fi
PORT=$(python3 -c "import json;print(json.load(open('$CONF')).get('web',{}).get('port',8080))" 2>/dev/null || echo 8080)
echo "Web UI: http://$(hostname -I | awk '{print $1}'):$PORT"
echo "Logs:   journalctl -u ledboard -f"
