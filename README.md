# LEDBoard

A scoreboard for Raspberry Pi RGB LED matrices: **live sports scores, Spotify, Plex, and Home Assistant**, all managed from a **web UI with a live preview** of the panels.

It's built for two chained 64×32 panels (128×32), and the layouts scale to other sizes.

| Board | What it shows |
|---|---|
| **Sports** | One board per league, using ESPN's free API (no key needed): NFL, NBA, MLB, NHL, college football and basketball, WNBA, MLS, EPL, La Liga, Bundesliga, Serie A, Ligue 1, Champions League. See [Sports](#sports) for the settings and the live-game details each sport shows. |
| **Now Playing** | Album art, scrolling title and artist, and an animated wavy progress bar (Material 3 style; a straight bar is an option), from a **Home Assistant media player** (HA's Spotify integration, Sonos and so on) or directly from the Spotify API. It's skipped when nothing is playing and can take over the display while music plays. |
| **Steam** | What you're playing on Steam: the game icon, name, session time and total hours. It can take over the display while you play, and supports several players. Also "friend now playing" and "achievement unlocked" (with how rare it is) screens. |
| **Plex** | Active streams (movies, TV, music) with the poster, title, S/E or artist, who's watching, and a wavy (or straight) progress bar. You can filter by user. Plus a carousel of recently added movies and episodes. |
| **Server Status** | Your Uptime Kuma monitors: green/red dots and response times, and an automatic critical alert when one goes down that clears when it's back. |
| **Home Assistant** | Any entities (sensors, locks, doors, lights, thermostats, and so on) at 1, 2 or 3 per screen, with automatic formatting and colors. |
| **3D Printer (Klipper)** | Print progress from Klipper via Moonraker: the model thumbnail or a live camera snapshot, progress, time left or finish time, layer, nozzle and bed temperatures, and paused, finished and error states. |
| **Weather** | Animated icons (spinning sun, drifting clouds, falling rain and snow, lightning flashes, fog, wind), current conditions, a 4-day forecast and a 24-hour temperature and rain graph. Data comes from Open-Meteo (free, no key) or your Home Assistant weather entity. |
| **Clock** | The time and date. Every piece of text (time, AM/PM, date, an optional extra line) has its own font size, color and format. It's also the fallback when nothing else has content. |
| **Custom message** | Your own text in the rotation. You can also flash a one-off message from the UI or API. |
| **Ambient** | Relaxing animations: fireplace, Matrix rain, starfield warp, plasma, lava lamp, Game of Life. Use them in the rotation, as the "nothing to show" screen, or during quiet hours. |
| **Severe Weather Alerts (US)** | National Weather Service warnings for your location interrupt the display. Extreme ones, such as tornado warnings, repeat and wake the display until dismissed. |

Also included:
- **[Alerts](#alerts)**: animated, prioritized interruptions such as the doorbell, washer finished or garage open.
- **[Home Assistant control over MQTT](#home-assistant-control-mqtt)**.
- **[One-click updates and backups](#updates-and-backups)**.
- **[Next-game countdowns, standings and the playoff picture](#sports)**.
- **[Scoring play recaps](#sports)** after favorite-team celebrations.
- **[Transitions](#global-settings-settings-tab)** between boards: slide, push up, wipe, fade or dissolve.
- **[Sun-following brightness](#global-settings-settings-tab)**.
- **[A combined ticker](#global-settings-settings-tab)**: all boards in one continuous scroll.

A compact live preview stays pinned under the header as you scroll down. Tap it to jump back to the controls. The web UI also covers: board order, enable and disable, per-board settings, "show now", next and pause, power, a brightness slider, quiet hours (dim, off or ambient on a schedule), global scroll defaults, hardware settings, optional password protection, and a status page with errors.

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
- **Transitions**: how the display changes from one board to the next: slide, push up, wipe, fade (through black), dissolve, random, or off. Set the length (0.4–0.8 s looks smooth) and whether to use it between boards only or between every screen. Alerts and takeovers always cut in instantly.
- **Combined ticker**:
  - All enabled boards' slides scroll by as one continuous line, with fresh data on every pass.
  - You can set the speed and gap, and a pause on each slide once it's fully on screen.
  - Alerts, scoring celebrations and team takeovers still interrupt it.
- **Automatic brightness**:
  - **Follow the sun**: uses the brightness slider during the day and eases down to a night level after sunset, through twilight.
  - The sun's position is calculated for your location, or the Weather board's location if you don't set one.
- **Quiet hours**: dim the display, turn it off, or show ambient animations (dimmed) between two times. Critical alerts still wake the display.
- **General**:
  - the board shown when nothing else has content (the clock by default)
  - the web UI port
  - an optional web UI password

The **Display** tab has brightness, frame rate and panel hardware settings. Brightness is also the slider at the top of the page.

### Sports
Each league is its own board, so it has its own place in the rotation and its own settings. Turn on the leagues you want, then open one with ⚙.

Every board's settings are in collapsible sections: tap a section's name to open or close it (the number shows how many settings it holds). The first section starts open, and the web UI remembers which ones you leave open. A league board's sections, top to bottom:

| Section | What's in it |
|---|---|
| **Teams** | favorites, favorites only, take over, spoiler mode |
| **Preview** | sample live / upcoming / final games and the next-game countdown |
| **Which games** | live only, how long finals stay, how far ahead upcoming games show, most games per rotation |
| **Between games** | next-game countdown and its design, standings, playoff picture |
| **Upcoming games** | team color accents, records, TV network (and its logo), betting line |
| **Live games** | layout, team color accents (Classic layout), detailed info, last play, score flash, goal animation |
| **Final scores** | team color accents, updated records |
| **Celebrations** | celebrations, extra scoring types, scoring play recap, Simulate |
| **Logos** | abbreviations, LED color enhancement, edges, dark-background logos |
| **Text colors** | scores, clock, info, loser's score, abbreviations |
| **Timing** | seconds per screen, refresh intervals |
| **Scroll mode** | ticker-style scrolling for this board |

In more detail:

- **Teams**
  - Favorite teams, as ESPN abbreviations such as `BUF, KC`. Favorites are shown first.
  - Only show favorites.
  - **Take over** the display while a favorite is playing.
  - **Spoiler mode**: hides the scores of your favorites' games.
- **Which games** and **Between games**: live only, final scores and how long they stay, upcoming games as game cards and how far ahead, the next-game countdown, and the maximum number of games. For a favorite team it goes:
  1. **Live**: the game card.
  2. **Final**: the final score, for the set number of hours **after the game ends** (24 by default).
  3. **Next-game countdown**: once the final is gone, your team's next game (logos, countdown, kickoff time, opponent, TV) until...
  4. **Upcoming**: ...kickoff is within the "game cards" window (48 hours by default), when it shows as a regular upcoming game card.

  The countdown appears per team: with the default setting it shows whenever *your team* has no game on the board, even if other teams' games are showing.

  **Countdown design** (under **Between games**):
  - **Classic**: logos at the sides, the countdown, and a rotating line (time, opponent, TV).
  - **Flip clock**: split-flap digit tiles for days / hours / minutes (hours / minutes / seconds in the last day).
  - **Ticket**: a game ticket with the matchup, date, time and TV, and a tear-off stub with the countdown.
  - **Matchup**: each half of the panel in its team's colors, with the countdown on a center plate.
  - **Road to kickoff**: a ball travels along a one-week road toward the opponent's logo, with the time to kickoff (first pitch, tip-off, puck drop).
  - **Game day hype**: on the day of the game, a flashing "GAME DAY" in your team's colors with the time to kickoff; before that, the flip clock.

  To see your chosen design, use **Preview → Next-game countdown** on the league's board. It shows two sample countdowns for 3 minutes: one a few days out, and one later today (game day).
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
- **Scoring play recap** (on by default): right after the celebration, a team-colored "TOUCHDOWN PACKERS" banner with the clock, the play scrolling by ("J. Love 24 yd pass to C. Watson"), the new score and, for football, the drive. The play comes from ESPN's game summary; if that isn't available yet, the scoreboard's last play is used.

**Playoff picture.** Turn on **Show the playoff picture** (under **Between games**, for NFL, NBA, WNBA, NHL, MLB and MLS) to add your favorite team's conference seeds, with clinch status (clinched top seed / division / playoffs, play-in, eliminated) and up to three teams "in the hunt". Your team is highlighted. For the NFL, once the playoffs start, a bracket shows wild-card results, the divisional games with their times, and the conference championship.

**Classic view.** Every sport's standard view puts each team's abbreviation (and AP rank for college) under its logo. Two settings change this:
- **Show team abbreviations** (under **Logos**): turn it off to make the logos fill the whole 32×32 space. College ranks then sit in a small corner badge.
- **Show each team's updated record** (under **Final scores**): shows each team's updated record under the score once a game ends.

**Logos on the panel.**
- **Same size:** in every matchup, both logos are scaled to the same visual size, so a round logo doesn't dwarf a wide one.
- **Enhance logo colors for LEDs** (on by default, under **Logos**): finds each logo's real colors from a larger copy, then:
  - snaps the shrunken logo to them for crisp edges, with no muddy blended shades
  - brightens dark team colors (such as Packers green or navy) so they show up
  - keeps black truly black (only colors that are dark but not black get lifted)
- **Logo edges** (under **Logos**):
  - **Automatic**: crisp hard pixel edges for big logos, smooth anti-aliased edges for small ones, where the soft edge pixels carry the detail.
  - **Smooth**: soft edges at every size, like the clock's "Smooth edges".
  - **Crisp**: hard edges in the logo's exact colors at every size.
- **Team color accents** for finals (per league, under **Final scores**):
  - **Plain**: no accents (the original look).
  - **Winner color**: the winning score, an underline and FINAL in the winner's color, with the loser dimmed.
  - **Team split bar**: a bar under the score split by points in both teams' colors, and records in team colors.
  - **Tinted halves**: each side of the panel washed in its team color, brighter for the winner.
  - **Winner banner**: a team-colored "GB WINS" banner on the bottom line and an arrow at the winning score.
  - **Team trim lines**: team-color lines above and below the score, records in team colors and FINAL in the winner's color.

  With "hide favorite's score" on, the accents never give away who won. Use **Preview → Final** to try each one.
- **Team color accents** for upcoming games (per league, under **Upcoming games**):
  - **Plain**: no accents (the original look).
  - **Matchup badge**: the "@" sits in a badge colored half and half in both teams' colors, and records are in team colors.
  - **Team split bar**: a half-and-half team-color bar under the start time, and records in team colors.
  - **Tinted halves**: each side of the panel washed in its team color.
  - **Info banner**: the bottom line (TV network, odds, date) sits on a two-team color banner.
  - **Team trim lines**: team-color lines along the top and bottom, and records in team colors.

  Use **Preview → Upcoming** to try each one.
- **Team color accents** for live games (per league, under **Live games**). These apply to the Classic layout; the graphical live layouts (Scorebug, Drive, Diamond...) are unchanged:
  - **Plain**: no accents (the original look).
  - **Leader color**: the leading team's score in its color, with team-color underlines.
  - **Team split bar**: a bar under the score split by points.
  - **Tinted halves**: each side washed in its team color, the leader brighter.
  - **Info banner**: the clock / info line on a two-team color banner.
  - **Team trim lines**: team-color lines along the top and bottom.
- **TV network logos** (under **Upcoming games**, on by default): shows the network's logo instead of its name, on upcoming games and the next-game countdown. Built-in pixel logos cover ESPN (ESPN2, ESPNU, ESPN+), ABC, CBS, NBC, FOX, FS1, NFL Network, NFL+, Prime Video, Peacock, Apple TV, Netflix, TNT, TBS, truTV, USA, Max, MLB/NHL/NBA networks, SEC/ACC/Big Ten networks and Paramount+. Any other network still shows as text.
- **Text colors** (per league): pick the colors for scores and start times, the live clock, records / TV network / FINAL, and the losing team's score on finals. Team abbreviations can use team colors (default) or one color for all. These apply to the standard game view; the special live layouts keep their own colors.

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
4. Under **Screens**, switch screens on or off and use the ▲ ▼ arrows to set the order they're shown in. The new screens are all off by default:
   - **Sun arc**: where the sun is in its path, sunrise/sunset and daylight left (sunrise countdown at night).
   - **Rain soon**: rain (or snow) for the next 2 hours in 15-minute bars, e.g. "RAIN IN 25 MIN". Only shows when rain is coming.
   - **Wind**: compass arrow, speed, gusts and a plain-English description.
   - **Week at a glance**: 7 days of high/low bars on one scale, colored by temperature.
   - **Living scene**: a city skyline under the real sky (day, sunset, night with lit windows) with the current weather animated: clouds, rain, snow, fog, lightning.
   - **UV + air quality**: color scales with markers, today's UV peak and sunscreen hours. Air quality uses Open-Meteo's free air-quality API (US or European scale).
   - **Moon phase**: the moon drawn in its real phase, % lit, and the next full/new moon.
   - **Today in 4 parts**: morning / afternoon / evening / night, starting from now.
   - **Compared to yesterday**: shows only when today's high differs from yesterday's by at least the set number of degrees.

   With the Home Assistant source, Rain soon and Compared to yesterday aren't available, and air quality isn't either; UV shows if your weather entity reports it.

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
4. **Picture**: the model thumbnail, or a **live camera snapshot** refreshed on every poll, with a blinking red dot while printing. The camera is found automatically from the webcam set up in Mainsail or Fluidd; enter a snapshot URL only if that doesn't work. If the camera can't be reached, the thumbnail is shown instead.
   **Thumbnails**: turn on G-code thumbnails in your slicer to see the model on the panel. PrusaSlicer, OrcaSlicer and SuperSlicer all support this; include a 32×32 size if you can. Without one, a printer icon is shown instead.
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

### Server status (Uptime Kuma)
1. In Uptime Kuma, go to **Status Pages → New Status Page**, give it a slug (for example `home`) and add the monitors you want on the panel. Save.
2. Turn on **Server Status** and paste the page's address, for example `http://192.168.1.10:3001/status/home`. No login or API key is needed.
3. **Options:**
   - only show certain monitors
   - only show the board when something is down
   - **Critical alert when a monitor goes down** (on by default): the alert stays on screen until the monitor is back up (then it clears itself) or you dismiss it. If you set up the Home Assistant Plex automation earlier, you can turn that off, or you'll get two alerts.

The board shows 6 monitors per page, down ones first, with the average 24-hour uptime. Monitors in maintenance show in blue.

### Steam
1. Get a free **Steam Web API key** at <https://steamcommunity.com/dev/apikey>. Any domain name works, for example `ledboard`.
2. On the **Steam** board, paste the key and your **profile URL** (`https://steamcommunity.com/id/yourname`), custom URL name or 17-digit Steam ID. Add more, comma-separated, for others in the house.
3. In Steam, open **Profile → Edit Profile → Privacy Settings** and set **Game details** to **Public**. Without that, Steam doesn't report the game you're playing.

**Options:**
- game icon or cover art
- take over the display while playing
- also show your avatar and status when you're online but not in a game

The session timer counts from when LEDBoard first sees you in a game.

**Friends & achievements** (both on by default):
- **Friend now playing**: when a friend starts a game. It uses the first Steam ID, and needs your friends list (and their game details) to be public. You can limit it to certain friends.
- **Achievement unlocked**: a trophy, the achievement, the game and how rare it is (from Steam's global stats). Achievements you already had are never announced.
- Each shows for 10 minutes by default (adjustable).

### Plex
1. Enter your server URL, for example `http://192.168.1.10:32400`.
2. Enter your **X-Plex-Token**. To find it, open Plex Web, go to any item → **⋯** → **Get Info** → **View XML**, and copy the `X-Plex-Token=…` value from the URL.
3. Click **Test connection**.

**Recently added** (on by default): new movies, episodes, seasons (and optionally music albums) from the last 24 hours, with the poster, title, season/episode or year/runtime/rating, and how long ago they were added. Set how far back and how many to show.

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
The easiest way is [MQTT](#home-assistant-control-mqtt), which needs no YAML. LEDBoard also has a small REST API that Home Assistant automations can call:

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

## Alerts
Alerts interrupt the display with an animated icon, a title, an optional message and a border in the alert's color.

| Priority | Behavior |
|---|---|
| `low` | Waits until the current board finishes |
| `normal` | Interrupts now |
| `high` | Interrupts now, with a flashing border |
| `critical` | Interrupts now, repeats every minute, and wakes the display (even during quiet hours or when it's off) until dismissed |

How they behave:
- Alerts queue up instead of overwriting each other.
- If a more urgent alert interrupts one, the interrupted alert plays again in full afterwards.
- Repeating alerts show in a banner under the preview, with **Dismiss** buttons.
- **Settings → Send an alert** lets you try them out.

**Icons:** `bell`, `doorbell`, `washer`, `dryer`, `garage`, `door`, `warning`, `info`, `check`, `printer`, `mail`, `package`, `car`, `lock`, `unlock`, `storm`, `tornado`, `fire`, `water`, `phone`, `trash`, `calendar`, `timer`, `thermometer`.

Send one from anywhere:

```bash
curl -X POST http://<pi-ip>:8080/api/alert -H 'Content-Type: application/json' \
  -d '{"title": "Garage open", "message": "Open for 30 minutes", "icon": "garage", "priority": "critical", "color": "#ff8c00"}'
```

**Keep on screen:** add `"hold": true` to keep an alert on the display until it's dismissed (from the web UI, Home Assistant, or by key), instead of returning to the normal rotation. If several held alerts are active they take turns; a more urgent alert still interrupts.

**Alert keys (auto-clearing alerts):** give an alert a `"key"`. Sending again with the same key replaces it instead of stacking a duplicate, and `/api/alert/dismiss` with `{"key": "..."}` clears it. Use this for "something is down" alerts that should go away on their own when it recovers. Critical alerts with a key repeat until dismissed and never expire on their own.

```bash
curl -X POST http://<pi-ip>:8080/api/alert -H 'Content-Type: application/json' \
  -d '{"key": "plex_down", "title": "PLEX DOWN", "message": "Plex server is not responding", "icon": "warning", "priority": "critical", "color": "#ff2020", "hold": true}'
curl -X POST http://<pi-ip>:8080/api/alert/dismiss -H 'Content-Type: application/json' -d '{"key": "plex_down"}'
```

Over MQTT, add `"key"` to the alert JSON; send `{"dismiss": "plex_down"}` to `<base>/alert/set` to clear it.

## Home Assistant control (MQTT)
1. In Home Assistant you need an MQTT broker, for example the **Mosquitto broker** add-on, and the MQTT integration.
2. In LEDBoard, go to **Settings → Home Assistant (MQTT)**. Turn it on and enter the broker host (your Home Assistant's IP), port 1883, and an MQTT username and password.
3. LEDBoard appears automatically under **Settings → Devices & services → MQTT** as **LEDBoard**, with these entities:

| Entity | What it does |
|---|---|
| `light.ledboard_display` | On/off and brightness |
| `select.ledboard_board` | Shows the current board; choose one to jump to it |
| `switch.ledboard_pause` | Pause the rotation |
| `button.ledboard_next`, `button.ledboard_dismiss` | Next board, dismiss alerts |
| `text.ledboard_message` | Flash a message |
| `notify.ledboard_alert` | Send an alert |
| `sensor.ledboard_alerts` | Number of active alerts |

Example automation actions:

```yaml
- action: notify.send_message
  target: { entity_id: notify.ledboard_alert }
  data:
    message: '{"title": "Doorbell", "message": "Front door", "icon": "doorbell", "priority": "high"}'
- action: light.turn_on
  target: { entity_id: light.ledboard_display }
  data: { brightness_pct: 20 }
- action: select.select_option
  target: { entity_id: select.ledboard_board }
  data: { option: "NFL" }
```

A plain-text message, such as `"Washer done"`, becomes a normal-priority alert with a bell icon. The entities show as unavailable while LEDBoard is off.

## Updates and backups
**Settings → Software** shows your version, checks GitHub for updates (also automatically every 6 hours, with an **Update available** badge in the header), and lists what changed. **Update now** does the following:
1. Runs `git pull`.
2. Installs any new Python packages.
3. Restarts LEDBoard.

It refuses to run if files in `/opt/ledboard` were edited by hand, so it never overwrites your changes.

**Settings → Backup & restore** downloads all your settings as a JSON file and restores them from one. The file includes your passwords and tokens, so keep it private.

## API

| Method | Path | Body |
|---|---|---|
| GET | `/api/state` | Returns the current board, fps, per-board status and errors |
| GET | `/api/frame.png` | Returns the current frame |
| POST | `/api/message` | `{"text": "...", "seconds": 10, "color": "#ff0000", "font": "7x13B"}` |
| POST | `/api/control` | `{"action": "next"}`, `"pause"`, `"resume"`, `"refresh"`, `{"action": "power", "on": false}`, `{"action": "brightness", "value": 40}`, `{"action": "jump", "board": "nfl"}` |
| GET/POST | `/api/config` | GET returns the config with secrets masked. POST takes a partial config and merges it in. |
| POST | `/api/restart` | Restarts the process |
| POST | `/api/alert` | `{"title", "message", "icon", "color", "priority", "seconds", "repeat_minutes", "until_dismissed", "key", "hold", "expires_in"}` |
| GET | `/api/alerts` | Returns the active alerts and the available icons |
| POST | `/api/alert/dismiss` | `{"id": 3}`, `{"key": "plex_down"}` or `{"id": "all"}` |
| GET/POST | `/api/update`, `/api/update/check`, `/api/update/apply` | Software update status, check, apply |
| GET/POST | `/api/config/export`, `/api/config/import` | Backup download and restore |

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
  boards/          sports (one board per league) + layouts, playoffs, recap; weather (+ extra screens), spotify,
                   plex, steam, uptime, klipper, homeassistant, clock, ambient, message
  transitions.py   slide / push / wipe / fade / dissolve between boards
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
