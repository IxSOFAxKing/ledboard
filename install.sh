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

if ! "$DIR/venv/bin/python" -c "import rgbmatrix" 2>/dev/null; then
  echo "==> Building hzeller/rpi-rgb-led-matrix Python bindings (takes a few minutes)"
  SRC="$DIR/.build/rpi-rgb-led-matrix"
  if [ ! -d "$SRC" ]; then
    git clone --depth 1 https://github.com/hzeller/rpi-rgb-led-matrix.git "$SRC"
  fi
  "$DIR/venv/bin/pip" install "$SRC"
fi
"$DIR/venv/bin/python" -c "import rgbmatrix; print('rgbmatrix OK')"

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
