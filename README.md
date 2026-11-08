# LEDBoard

A scoreboard for Raspberry Pi RGB LED matrices: **live sports scores, Spotify, Plex, and Home Assistant**, all managed from a **web UI with a live preview** of the panels.

It's built for two chained 64×32 panels (128×32), and the layouts scale to other sizes.

| Board | What it shows |
|---|---|
| **Sports** | One board per league, using ESPN's free API (no key needed): NFL, NBA, MLB, NHL, college football and basketball, WNBA, MLS, EPL, La Liga, Bundesliga, Serie A, Ligue 1, Champions League. See [Sports](#sports) for the settings and the live-game details each sport shows. |
| **Now Playing** | Album art, scrolling title and artist, and a progress bar, from a **Home Assistant media player** (HA's Spotify integration, Sonos and so on) or directly from the Spotify API. It's skipped when nothing is playing and can take over the display while music plays. |
| **Plex** | Active streams (movies, TV, music) with the poster, title, S/E or artist, who's watching, and progress. You can filter by user. |
| **Home Assistant** | Any entities (sensors, locks, doors, lights, thermostats, and so on) at 1, 2 or 3 per screen, with automatic formatting and colors. |
| **3D Printer (Klipper)** | Print progress from Klipper via Moonraker: the model thumbnail, progress, time left or finish time, layer, nozzle and bed temperatures, and paused, finished and error states. |
| **Weather** | Animated icons (spinning sun, drifting clouds, falling rain and snow, lightning flashes, fog, wind), current conditions, a 4-day forecast and a 24-hour temperature and rain graph. Data comes from Open-Meteo (free, no key) or your Home Assistant weather entity. |
| **Clock** | The time and date. Every piece of text (time, AM/PM, date, an optional extra line) has its own font size, color and format. It's also the fallback when nothing else has content. |
| **Custom message** | Your own text in the rotation. You can also flash a one-off message from the UI or API. |

The web UI also covers: board order, enable and disable, per-board settings, "show now", next and pause, power, a brightness slider, quiet hours (dim or off on a schedule), global scroll defaults, hardware settings, optional password protection, and a status page with errors.

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

## Install the web UI as an app (Android / iPhone)

The web UI is an installable app (a PWA) with its own icon, a full-screen window and offline start-up. Browsers only install apps from **https://** addresses, though, and the Pi serves plain `http://`. Pick one of these options.

### Option A: Tailscale (recommended)
This gives a real HTTPS address that also works away from home.

1. In the Tailscale admin console, open **DNS** and turn on **MagicDNS** and **HTTPS Certificates**. This is a one-time step.
2. On the Pi, run:
   ```bash
   sudo tailscale serve --bg 8080
   ```
   It prints an address like `https://pisports.your-tailnet.ts.net`, and keeps serving it after reboots.
3. On your phone, with the Tailscale app connected, open that address in **Chrome**.
4. Tap **Install app**, either in LEDBoard's header or in Chrome's ⋮ menu.

On an iPhone, open the address in Safari and use Share → **Add to Home Screen**.

### Option B: Home Wi-Fi only, no Tailscale
1. In Chrome on the phone, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.
2. Enter `http://<pi-ip>:8080`, set the flag to **Enabled**, and relaunch Chrome.
3. Open LEDBoard and choose **Install app**.

### Option C: A plain shortcut
Chrome's ⋮ menu → **Add to Home screen** works on `http://` too. It opens in a normal browser tab instead of its own app window.

The app always loads the latest UI from the Pi, so updating LEDBoard updates the app as well.

## Setting up integrations

All of this is done in the web UI under **Boards → ⚙**. Enabled boards are listed first, in rotation order. Disabled boards sit below them and are hidden until you click **Show disabled boards**. Turning a board on adds it to the end of the rotation.

### Scroll mode (every board)
Every board has a **Scroll mode** switch. When it's on, the board's slides scroll across the display one after another, like a ticker, instead of showing one at a time.

The speed (20 px/s by default) and the gap between slides are set once for all boards under **Settings → Defaults for all boards**. If you fill in a board's own speed or gap, that board uses its own value.

### Global settings (Settings tab)
- **Defaults for all boards**: scroll mode speed and gap.
- **Quiet hours**: dim the display or turn it off between two times.
- **General**:
  - the board shown when nothing else has content (the clock by default)
  - the web UI port
  - an optional web UI password

The **Display** tab has brightness, frame rate and panel hardware settings. Brightness is also the slider at the top of the page.

### Sports
Each league is its own board, so it has its own place in the rotation and its own settings. Turn on the leagues you want, then open one with ⚙. Settings are grouped like this:

- **Teams**
  - Favorite teams, as ESPN abbreviations such as `BUF, KC`. Favorites are shown first.
  - Only show favorites.
  - **Take over** the display while a favorite is playing.
  - **Spoiler mode**: hides the scores of your favorites' games.
- **Which games**: live only, upcoming games (and how far ahead), final scores (and how long they stay), and the maximum number of games.
- **Upcoming games**: team records, TV network, betting line and over/under, and AP Top 25 rankings (college leagues only).
- **Live games**: detailed live info, scroll the last play, and flash the score when it changes.
- **Timing**: seconds per game, and how often scores refresh, normally and during live games.
- **Logos**: prefer logos designed for dark backgrounds.

With **detailed live info** on, the bottom line rotates through the game clock and these extras:

| Sport | Extras |
|---|---|
| Football | Possession marker (red in the red zone), timeouts left (dots), down and distance with field position (e.g. `3&4 KC35`), last play |
| Baseball | Base runners (diamond), outs (dots), ball and strike count, pitcher, batter, hits and errors, last play |
| Basketball | Each team's top scorer, last play |
| Hockey | Shots on goal, last play |
| Soccer | Red cards, latest goal scorer and minute |

**Football live layouts.** On the NFL and College Football boards, **Live game layout** chooses how live games look:

| Layout | What it shows |
|---|---|
| **Classic** | Logos at the edges, the score, and a rotating info line |
| **Field** | Classic plus a mini football field: team-colored end zones, yard lines, the ball (blue line of scrimmage), a yellow first-down line and red-zone shading. One line combines the clock with down and distance. |
| **Scorebug** | TV-style solid team-color panels with the score, timeout bars and a football icon for possession. The center shows the clock, down and distance, the spot and a mini field. |
| **Drive** | Small logos and big scores, an arrow for the drive direction, a win-probability bar in team colors (when ESPN provides it), and a full-width field along the bottom |

**Baseball live layouts.** The MLB board has the same **Live game layout** setting:

| Layout | What it shows |
|---|---|
| **Classic** | Logos, the score, a small diamond with outs, and a rotating info line |
| **Scorebug** | TV-style: stacked teams, each with its logo, team-color bar and score (a pointer marks who's batting); a big diamond with base runners; a right-hand column with the inning (▲/▼) and large B/S/O count dots; plus a pitcher / batter / last-play ticker |
| **Line Score** | Runs by inning plus R/H/E, with the current inning highlighted. A strip underneath shows the inning, base runners, count and outs. |
| **Matchup** | Small logos and big scores, then pitcher vs. batter (with their stats for the game when ESPN sends them), a diamond and the count. It alternates with the last play. |

**Hockey live layouts.** The NHL board has the same **Live game layout** setting:

| Layout | What it shows |
|---|---|
| **Classic** | Logos, the score, and a rotating info line |
| **Scorebug** | Stacked team-color bars with small logos, the score and shots on goal; the period and a big game clock; a ticker with the last play and top scorers |
| **Period Score** | Goals by period (1st / 2nd / 3rd / OT / SO) with the total and shots on goal, and the current period highlighted |
| **Rink** | A mini hockey rink drawn across the display (boards, red and blue lines, goal creases), with each team's logo and score in its own end and the period, clock and shots in the neutral zone |

**Soccer live layouts** (MLS, Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Champions League). Each league has its own **Live game layout** setting:

| Layout | What it shows |
|---|---|
| **Classic** | Logos, the score, red cards, and a rotating info line |
| **Scorebug** | TV-style: each team's logo, a color bar and a big score, with the match clock (or HT / FT) in the middle. Below that are a possession bar in team colors, yellow and red cards under each team, and a ticker of goal scorers (with penalty/own-goal markers) and match stats. |
| **Match Stats** | A compact scoreline, then head-to-head comparison bars for possession, shots, shots on target, corners, fouls and saves. It pages through them if there are more than three. |
| **Timeline** | The match as a line from 0 to 90 minutes (120 in extra time), filled in up to the current minute, with goals and cards at the minute they happened: one team above the line, the other below |

Match stats and events come from ESPN when it provides them. If it doesn't, those parts are simply left out.

**Goal celebration.** When a team scores, an NHL or soccer game shows a flashing **GOAL!** with the team's logo and colors for a few seconds. Turn it off with **Goal celebration** on the league's board.

**Previewing games.** Every league board has **Preview** buttons: Live game, Upcoming, Final, and All three. They're useful in the off-season.
- They show realistic sample games for 3 minutes, drawn with your current settings and live layout.
- They use your favorite teams, with real team colors and logos from ESPN's teams list.
- Preview games ignore your filters, and never trigger takeover or celebrations.

**Scoring celebrations.** When one of your **favorite teams** scores, the league's board plays an animation the next time it comes up in the rotation, then shows its games as usual:

| Sport | Celebrated | Animation |
|---|---|---|
| Football | Touchdowns, plus field goals if you turn that on | A football spirals across the field into the end zone, or through the uprights, then flashing "TOUCHDOWN!" with fireworks and confetti |
| Hockey | Goals | The puck slides into the net, then a spinning red goal light with "GOAL!" |
| Soccer | Goals | The ball curls into a rippling net, then confetti |
| Baseball | Home runs (and grand slams), plus every run if you turn that on | The ball sails over the outfield wall, then a fireworks show |

How it works:
- Each animation uses the scoring team's colors and logo, and ends on the new score.
- The extra point after a touchdown doesn't trigger a second celebration.
- Celebrations are skipped in spoiler mode, and when the score was already on the board as LEDBoard started.
- Up to three can queue, and they expire after 30 minutes.
- To see one now, use the **Simulate** buttons on the league's board (under Celebrations). Simulate uses a live game with your favorite if there is one, or a sample game otherwise.

**Classic view.** Every sport's standard view puts each team's abbreviation (and AP rank for college) under its logo.

> **Note:** The live details read optional fields from ESPN's unofficial API. If ESPN doesn't send a field for a game, that detail is simply left out.

Upgrading from an earlier LEDBoard with a single "Sports" board moves your settings over automatically. Each league keeps its favorites and on/off state, and the old config is saved as `config.json.bak`.

### Now Playing

**Through Home Assistant (recommended if HA already has your Spotify):**
1. Set the URL and token on the **Home Assistant** board.
2. On **Now Playing**, choose **Home Assistant media player**.
3. Enter your media player entity, for example `media_player.spotify_yourname`. The **Load from HA** button fills in suggestions. You can list several players; each one that's playing gets its own slide.

**Directly from Spotify** (choose **Spotify Web API**): Spotify only allows `http://` redirect addresses for `127.0.0.1`, so there's one copy-and-paste step.

1. Create an app at <https://developer.spotify.com/dashboard> and select **Web API**.
2. Add the redirect URI `http://127.0.0.1:8080/spotify/callback` to the app.
3. In LEDBoard, paste the **Client ID** and **Client Secret**, then click **Save**.
4. Click **Connect Spotify** and approve.
5. Your browser lands on a `127.0.0.1` page that fails to load. That's expected. Copy that page's full URL from the address bar, paste it into the box, and click **Finish**.

LEDBoard saves the refresh token, so you only do this once.

### Weather
1. Turn on **Weather**.
2. Choose a source:
   - **Open-Meteo**: type a city or ZIP code under Location, click **Search**, pick the right place, then **Save**.
   - **Home Assistant**: enter your weather entity, for example `weather.forecast_home`. It uses the Home Assistant board's URL and token, and gets forecasts through `weather.get_forecasts`. Night icons follow `sun.sun`.
3. Optionally:
   - switch units (°F/mph or °C/km/h)
   - turn the **Now**, **4-day forecast** and **Next 24 hours** screens on or off

### Clock
The time, AM/PM, date and an optional extra line each have their own font, size and color.

There are two kinds of font:
- **Scalable fonts**, which take any pixel size:
  - Bebas Neue, Anton, Oswald, Big Shoulders and Teko (tall and condensed styles)
  - Orbitron (futuristic)
  - DSEG7 and DSEG14 (7- and 14-segment "digital clock" displays)
  - Doto (dot matrix)
  - Pixelify Sans, Press Start 2P, VT323 and Silkscreen (pixel and retro)
  - Share Tech Mono and Righteous
- **Fixed-size pixel fonts**, from 4×6 up to 10×20.

Leave the time's size blank and it grows as large as fits around the other lines. **Smooth edges** turns on anti-aliasing for scalable fonts. Scalable fonts look best at about 10 px and up, so the fixed pixel fonts are better for small dates. Font licenses are in `ledboard/fonts/ttf/LICENSES.txt`. You can also set:
- the date format, from presets or a custom `strftime` pattern
- 24-hour time, seconds, a leading zero and a blinking colon
- uppercase or lowercase
- left, center or right alignment, and the spacing between lines

### 3D printer (Klipper / Moonraker)
1. Turn on **3D Printer (Klipper)**.
2. Set **Moonraker URL** to the address Mainsail or Fluidd uses, normally `http://<printer-ip>:7125`.
3. **API key**: only needed if Moonraker's `[authorization]` doesn't already trust the Pi's network. Mainsail and Fluidd show the key under their settings.
4. **Thumbnails**: turn on G-code thumbnails in your slicer to see the model on the panel. PrusaSlicer, OrcaSlicer and SuperSlicer all support this; include a 32×32 size if you can. Without one, a printer icon is shown instead.
5. **Options**:
   - take over the display while printing
   - show temperatures when idle
   - how long a finished print stays up
   - show time left, or the clock time it will finish

Time left averages the file-progress estimate and the slicer's estimate, the way Mainsail does.

**When the board shows:**
- **Printing or paused:** always.
- **Finished:** for the configured number of minutes after the print *actually* ended. The end time comes from Moonraker's job history, so restarting LEDBoard or power-cycling the printer doesn't restart the timer.
- **Switched off:** never. That covers Klipper being unable to reach the printer and the printer's computer being unreachable.
- **Failure:** if Klipper loses the printer in the middle of a print, the error shows for the same window.

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
| POST | `/api/control` | `{"action": "next"}`, `"pause"`, `"resume"`, `"refresh"`, `{"action": "power", "on": false}`, `{"action": "brightness", "value": 40}`, `{"action": "jump", "board": "nfl"}` |
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
| Flicker | See [Reducing flicker](#reducing-flicker) below. |
| Random sparkles or garbage pixels | Raise **GPIO slowdown** under Display by 1. |
| Wrong colors | Change the **RGB sequence** setting. |
| Some panels need an init sequence | Set **Panel type** to `FM6126A`. |
| Panels only half-lit, or wrong layout | Check **rows**, **cols**, **panels chained**, and the **multiplexing** / **row address type** settings. |
| "snd_bcm2835" error in the logs | Reboot after running the installer (it blacklists the sound module). |
| Board shows in the UI preview but not on the panels | The pill at the top says "Simulator". Check `journalctl -u ledboard` for the matrix init error. Usually it's a wrong hardware mapping or the service isn't running as root. |
| Sports shows nothing | Check the Status tab for errors. Off-season leagues simply have no games. |

## Reducing flicker

Work through these in order, checking the panels after each step.

1. **Check the Status tab.** The Performance row shows:
   - frame times
   - CPU temperature
   - whether the Pi has reported **under-voltage** or throttling

   Under-voltage makes the refresh uneven, so fix the power supply first if it's flagged.
2. **Set GPIO slowdown** (Display tab): 2 for a Pi Zero 2 W or Pi 3, 3–4 for a Pi 4. A value that's too high slows the refresh rate and causes flicker. A value that's too low causes sparkles. Use the lowest value that looks clean.
3. **Reserve a CPU core for the panels.** LEDBoard already keeps its own work off the last core. For the full effect, have the kernel keep everything else off it too. `install.sh` offers this, or you can do it by hand:
   ```bash
   sudo sed -i '1 s/$/ isolcpus=3/' /boot/firmware/cmdline.txt && sudo reboot
   ```
4. **Raise the refresh rate.** Turn on **Print refresh rate to log** and watch `journalctl -u ledboard -f`. Then try these, one at a time:
   - **PWM bits** 8–9: this roughly doubles the refresh rate.
   - **PWM dither bits** 1.
   - **PWM LSB nanoseconds** around 100.
5. **Make the brightness steady.** Set **Limit refresh rate** a little below the lowest rate you saw in step 4.
6. **Lower Render FPS** to 20–25. That's plenty for scrolling at 20 px/s and frees up CPU.

LEDBoard only sends the pixels that changed to the panels, and skips frames that are identical. A scrolling line of text is typically under 10% of the screen per frame.

## Project layout

```
ledboard/
  engine.py        render loop, rotation, takeover, quiet hours
  display.py       rpi-rgb-led-matrix driver + simulator
  fonts.py         BDF bitmap font renderer (crisp pixel text)
  gfx.py           drawing helpers, marquee text, image cache
  config.py        JSON config + sportsmatrix.conf importer
  boards/          sports (one board per league), spotify (now playing), plex, homeassistant, clock, message
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
