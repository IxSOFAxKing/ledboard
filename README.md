# LEDBoard

A scoreboard for Raspberry Pi RGB LED matrices: **live sports scores, Spotify, Plex, and Home Assistant**, all managed from a **web UI with a live preview** of the panels.

It's built for two chained 64×32 panels (128×32), and the layouts scale to other sizes.

| Board | What it shows |
|---|---|
| **Sports** | Live, upcoming and final scores from ESPN (no API key needed): NFL, NBA, MLB, NHL, college football and basketball, WNBA, MLS, EPL, La Liga, Bundesliga, Serie A, Ligue 1, Champions League. It shows team logos and colors, the game clock, football possession and down-and-distance, and baseball bases, count and outs. You can mark favorite teams, show only your favorites, and let a favorite's live game take over the display. |
| **Spotify** | Album art, scrolling title and artist, and a progress bar. It's skipped when nothing is playing and can take over the display while music plays. |
| **Plex** | Active streams (movies, TV, music) with the poster, title, S/E or artist, who's watching, and progress. You can filter by user. |
| **Home Assistant** | Any entities (sensors, locks, doors, lights, thermostats, and so on) at 1, 2 or 3 per screen, with automatic formatting and colors. |
| **Clock** | The time and date. It's also the fallback when nothing else has content. |
| **Custom message** | Your own text in the rotation. You can also flash a one-off message from the UI or API. |

The web UI also covers: board order, enable and disable, per-board settings, "show now", next and pause, power, a brightness slider, quiet hours (dim or off on a schedule), hardware settings, optional password protection, and a status page with errors.

## Install on the Pi

```bash
sudo git clone <this repo> /opt/ledboard
cd /opt/ledboard
sudo ./install.sh
```

The installer does the following:

- Creates a Python venv and installs the [hzeller/rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix) Python bindings. It downloads a pre-built copy from this repo's `rgbmatrix-wheels` release, which takes seconds. It only compiles on the Pi if no pre-built copy matches your OS. To rebuild the pre-built copies, run the **Build rgbmatrix wheels** workflow from the Actions tab.
- **Imports your panel settings from `/etc/sportsmatrix.conf`** if you were running rgb-led-matrix-sports-premium. This covers the hardware mapping, chain length, brightness and so on.
- Offers to **stop and disable the `sportsmatrix` service**, because only one program can drive the panels at a time. To go back later, run `sudo systemctl disable --now ledboard && sudo systemctl enable --now sportsmatrix`.
- Blacklists the onboard sound driver if it's loaded, which the matrix library requires. That step needs a reboot.
- Installs and starts the `ledboard` systemd service.

Then open **`http://<pi-ip>:8080`**.

Useful commands:

```bash
journalctl -u ledboard -f          # logs
sudo systemctl restart ledboard
sudo nano /etc/ledboard/config.json  # everything in the UI is stored here
```

## Setting up integrations

All of this is done in the web UI under **Boards → ⚙**.

### Sports
1. Tick the leagues you want.
2. Add favorite teams as ESPN abbreviations, for example `BUF, KC` for the NFL or `NYY` for MLB.
3. Optionally turn on:
   - **"Only show favorite teams"**: leagues with no favorites listed still show every game.
   - **"Take over the display while a favorite team is playing"**.

### Spotify
Spotify only allows `http://` redirect addresses for `127.0.0.1`, so there's one copy-and-paste step.

1. Create an app at <https://developer.spotify.com/dashboard> and select **Web API**.
2. Add the redirect URI `http://127.0.0.1:8080/spotify/callback` to the app.
3. In LEDBoard, paste the **Client ID** and **Client Secret**, then click **Save**.
4. Click **Connect Spotify** and approve.
5. Your browser lands on a `127.0.0.1` page that fails to load. That's expected. Copy that page's full URL from the address bar, paste it into the box, and click **Finish**.

LEDBoard saves the refresh token, so you only do this once.

### Plex
1. Enter your server URL, for example `http://192.168.1.10:32400`.
2. Enter your **X-Plex-Token**. To find it, open Plex Web, go to any item → **⋯** → **Get Info** → **View XML**, and copy the `X-Plex-Token=…` value from the URL.
3. Click **Test connection**.

### Home Assistant
1. Enter your HA URL, for example `http://homeassistant.local:8123`.
2. Create a **long-lived access token** in HA under Profile → Security, and paste it in.
3. Click **Save**, then **Load entity list from Home Assistant**. This gives you autocomplete in the Entity ID boxes.
4. Add the entities you want to show. Each entity has these optional settings:
   - **Label**: overrides the friendly name.
   - **Attribute**: shows an attribute instead of the state, for example `current_temperature` on a `climate.` entity.
   - **Color**: a fixed color for the value.
   - **Decimals**: the number of decimal places.

#### Controlling the board from Home Assistant
LEDBoard has a small REST API, so Home Assistant automations can drive it:

```yaml
# configuration.yaml
rest_command:
  ledboard_message:
    url: "http://<pi-ip>:8080/api/message"
    method: POST
    content_type: "application/json"
    payload: '{"text": "{{ text }}", "seconds": {{ seconds | default(10) }}, "color": "{{ color | default("#ffffff") }}"}'
  ledboard_power:
    url: "http://<pi-ip>:8080/api/control"
    method: POST
    content_type: "application/json"
    payload: '{"action": "power", "on": {{ on }}}'
```

Then you can use it in automations, for example: `service: rest_command.ledboard_message` with `data: {text: "Doorbell!", color: "#ff0000"}`.

## API

| Method | Path | Body |
|---|---|---|
| GET | `/api/state` | Returns the current board, fps, per-board status and errors |
| GET | `/api/frame.png` | Returns the current frame |
| POST | `/api/message` | `{"text": "...", "seconds": 10, "color": "#ff0000", "font": "7x13B"}` |
| POST | `/api/control` | `{"action": "next"}`, `"pause"`, `"resume"`, `"refresh"`, `{"action": "power", "on": false}`, `{"action": "brightness", "value": 40}`, `{"action": "jump", "board": "sports"}` |
| GET/POST | `/api/config` | GET returns the config with secrets masked. POST takes a partial config and merges it in. |
| POST | `/api/restart` | Restarts the process |

If you set a web password, use HTTP basic auth with any username.

## Running without hardware (development)

```bash
pip install -r requirements.txt
python3 -m ledboard -c ./config.json --simulate
```

Open <http://localhost:8080> to see the live LED preview. If the `rgbmatrix` module isn't installed, LEDBoard falls back to the simulator automatically.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Flicker or garbage on the panels | Raise **GPIO slowdown** under Display. A Pi 4 usually needs 3–4. |
| Wrong colors | Change the **RGB sequence** setting. |
| Some panels need an init sequence | Set **Panel type** to `FM6126A`. |
| Panels only half-lit, or wrong layout | Check **rows**, **cols**, **panels chained**, and the **multiplexing** / **row address type** settings. |
| "snd_bcm2835" error in the logs | Reboot after running the installer (it blacklists the sound module). |
| Board shows in the UI preview but not on the panels | The pill at the top says "Simulator". Check `journalctl -u ledboard` for the matrix init error. Usually it's a wrong hardware mapping or the service isn't running as root. |
| Sports shows nothing | Check the Status tab for errors. Off-season leagues simply have no games. |

## Project layout

```
ledboard/
  engine.py        render loop, rotation, takeover, quiet hours
  display.py       rpi-rgb-led-matrix driver + simulator
  fonts.py         BDF bitmap font renderer (crisp pixel text)
  gfx.py           drawing helpers, marquee text, image cache
  config.py        JSON config + sportsmatrix.conf importer
  boards/          sports, spotify, plex, homeassistant, clock, message
  web/             Flask API + single-page UI (static/)
```

### Adding a board
1. Subclass `Board` in `ledboard/boards/`.
2. Fetch data in `update()`. It runs in a background thread every `refresh` seconds.
3. Return the screens to show from `slides()`.
4. Draw in `render(canvas, slide, t)`.
5. Declare `DEFAULTS` and `FIELDS`. The web UI builds the settings form from `FIELDS`.
6. Register the class in `boards/__init__.py`.

## Credits
- Matrix driver: [hzeller/rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix).
- Fonts: public-domain X11 fonts from that repo. `tom-thumb` is MIT licensed, by Robey Pointer.
- Scores: ESPN's public scoreboard endpoints. These are unofficial and could change.
