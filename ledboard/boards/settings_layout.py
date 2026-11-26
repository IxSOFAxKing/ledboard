"""How every board's settings are arranged in the web UI: the same sections, in the same order, with the same
names on every board. Only the arrangement and some labels live here; what each setting does is unchanged.

Under the four headings the web UI already has:

  Basics    Setup          where the data comes from (addresses, keys, accounts), then what's yours:
                           teams, gamertag, location, media players, entities, monitors
            When to show   when the board appears, and "Take over the display while ..."
  Screens   Which screens  the on/off switch of each screen that has nothing else to set
            then one section per screen that has options, named after it ("Live games", "Now playing",
            "Live radar"): its on/off first, then its layout, then its options
  Look      Pictures       art, logos, thumbnails
            Colors         text and accent colors
            Details        small extras shown on screen (records, gamerscore, who's watching, units)
            (Progress bar, and the Clock's per-line sections, where a board has them)
  Advanced  Timing         seconds per screen, how often to check
            Try it out     preview and test buttons
            Scroll mode    the same on every board

A section is left out when a board has nothing for it, and small leftovers are combined rather than given a
section of their own.
"""
from __future__ import annotations

# the same words on every board
COMMON_LABELS = {
    "duration": "Seconds per screen",
    "refresh": "Check every (seconds)",
}

SPORTS = {
    "layout": [
        ("Basics", "Setup", ["favorites", "favorites_only", "hide_favorite_score"]),
        ("Basics", "When to show", ["live_only", "takeover"]),
        ("Screens", "Upcoming games", ["show_pregame", "upcoming_hours", "upcoming_layout", "show_records",
                                       "show_network", "network_logos", "show_odds"]),
        ("Screens", "Live games", ["live_layout", "live_style", "detailed_live", "show_last_play", "score_flash",
                                   "show_win_probability"]),
        ("Screens", "Final scores", ["show_final", "final_hours", "final_style", "show_final_records"]),
        ("Screens", "Between games", ["next_game", "next_game_style", "offseason_countdown", "opener_days",
                                      "show_standings", "show_playoffs"]),
        ("Screens", "Celebrations", ["celebrations", "celebrate_field_goals", "celebrate_runs", "goal_animation",
                                     "recap"]),
        ("Look", "Pictures", ["logo_version", "use_dark_logos", "logo_detail", "logo_enhance", "logo_edges",
                              "show_abbreviations", "logo_overrides"]),
        ("Look", "Colors", ["text_score", "text_status", "text_info", "text_loser", "abbr_color_mode", "abbr_color"]),
        ("Look", "Details", ["show_rankings"]),
        ("Advanced", "Timing", ["duration", "refresh", "live_refresh", "max_games"]),
        ("Advanced", "Try it out", ["preview", "simulate"]),
    ],
    "labels": {"upcoming_layout": "Layout", "live_layout": "Layout", "next_game_style": "Countdown layout",
               "live_refresh": "Check every while a game is live (seconds)"},
}

LAYOUTS = {
    "ufc": {
        "layout": [
            ("Basics", "Setup", ["favorites", "favorites_only", "bouts"]),
            ("Basics", "When to show", ["takeover", "single_screen", "result_minutes"]),
            ("Screens", "Countdown", ["show_countdown", "countdown_layout", "countdown_to", "countdown_days"]),
            ("Screens", "Fight card and results", ["layout", "card_days", "final_hours"]),
            ("Screens", "Live fights", ["live_layout", "celebrate"]),
            ("Screens", "Upcoming events list", ["show_schedule", "schedule_count", "schedule_when"]),
            ("Look", "Pictures and details", ["pictures", "event_logo_url", "tsdb_key", "show_network"]),
            ("Advanced", "Timing", ["duration", "refresh", "live_refresh"]),
            ("Advanced", "Try it out", ["preview"]),
        ],
        "labels": {"countdown_layout": "Layout", "layout": "Layout (also used for results)", "live_layout": "Layout",
                   "live_refresh": "Check every while fights are live (seconds)",
                   "tsdb_key": "TheSportsDB API key (optional, for event logos)"},
    },
    "weather": {
        "layout": [
            ("Basics", "Setup", ["source", "ha_entity", "location_name", "latitude", "longitude", "units"]),
            ("Screens", "Screens and order", ["screen_order"]),
            ("Screens", "24-hour graph", ["hourly_rain", "hourly_rain_min", "hourly_rain_legend"]),
            ("Screens", "Live radar", ["radar_area", "radar_map", "radar_only_rain", "radar_source"]),
            ("Screens", "Other screens", ["aqi_scale", "yesterday_threshold"]),
            ("Advanced", "Timing", ["duration", "refresh"]),
        ],
        "labels": {"radar_area": "Area shown", "radar_map": "Map", "radar_source": "Radar source",
                   "radar_only_rain": "Only show when there's rain or snow in view",
                   "hourly_rain": "Show rain chance bars", "aqi_scale": "UV + air quality: scale",
                   "yesterday_threshold": "Compared to yesterday: only when highs differ by at least (degrees)"},
    },
    "weather_alerts": {
        "layout": [
            ("Basics", "Setup", ["latitude", "longitude"]),
            ("Screens", "Alerts", ["min_severity", "warnings_only", "repeat_minutes"]),
            ("Advanced", "Timing", ["refresh"]),
        ],
    },
    "spotify": {
        "layout": [
            ("Basics", "Setup", ["source", "spotify_auth", "client_id", "client_secret", "redirect_uri",
                                 "ha_entities", "ha_live", "ha_force_refresh"]),
            ("Basics", "When to show", ["show_paused", "takeover"]),
            ("Screens", "Now playing", ["layout", "progress_style", "bar_color_mode", "bar_color"]),
            ("Look", "Colors", ["title_color", "artist_color"]),
            ("Advanced", "Timing", ["duration", "refresh"]),
        ],
        "labels": {"progress_style": "Progress bar style", "bar_color_mode": "Progress bar color",
                   "bar_color": "Your progress bar color",
                   "ha_entities": "Media players (comma-separated)"},
    },
    "steam": {
        "layout": [
            ("Basics", "Setup", ["api_key", "steam_ids", "ha_games"]),
            ("Basics", "When to show", ["show_online", "takeover"]),
            ("Screens", "Which screens", ["show_friends", "friend_filter", "show_achievements"]),
            ("Screens", "Now playing", ["layout", "art", "hero"]),
            ("Look", "Custom artwork", ["griddb_key", "art_overrides"]),
            ("Advanced", "Timing", ["duration", "refresh", "event_minutes"]),
        ],
        "labels": {"show_friends": "Friend now playing (when a friend starts a game)", "show_achievements":
                   "Achievement unlocked (with how rare it is)", "event_minutes": "Friend and achievement screens stay for (minutes)",
                   "ha_games": "Games outside Steam: Home Assistant entities that show one is running"},
    },
    "xbox": {
        "layout": [
            ("Basics", "Setup", ["gamertag", "discover"]),
            ("Basics", "When to show", ["show", "takeover"]),
            ("Screens", "Which screens", ["show_profile", "show_stats", "show_friends", "friend_filter",
                                          "show_achievements"]),
            ("Screens", "Now playing", ["layout", "art", "hero", "show_gamerscore"]),
            ("Advanced", "Timing", ["duration", "refresh", "event_minutes"]),
        ],
        "labels": {"show_profile": "Profile (when you're online but not playing)", "show_stats":
                   "Stats (gamerscore, account tier, followers...)",
                   "show_friends": "Friend now playing (when a friend starts a game)",
                   "show_achievements": "Achievement unlocked (when your gamerscore goes up)",
                   "event_minutes": "Friend and achievement screens stay for (minutes)"},
    },
    "plex": {
        "layout": [
            ("Basics", "Setup", ["url", "token", "verify_ssl", "users"]),
            ("Basics", "When to show", ["show_paused", "takeover"]),
            ("Screens", "Now playing", ["layout", "show_user", "progress_style"]),
            ("Screens", "Recently added", ["show_recent", "recent_layout", "recent_hours", "recent_max", "recent_music"]),
            ("Advanced", "Timing", ["duration", "refresh"]),
        ],
        "labels": {"layout": "Layout", "recent_layout": "Layout"},
    },
    "homeassistant": {
        "layout": [
            ("Basics", "Setup", ["url", "token", "verify_ssl", "entities"]),
            ("Look", "Entities", ["per_page", "label_color"]),
            ("Advanced", "Timing", ["duration", "refresh"]),
        ],
    },
    "uptime": {
        "layout": [
            ("Basics", "Setup", ["status_url", "monitors"]),
            ("Basics", "When to show", ["only_when_down"]),
            ("Screens", "Monitors", ["layout", "alert_on_down", "alert_monitors", "alert_hold"]),
            ("Advanced", "Timing", ["duration", "refresh"]),
        ],
        "labels": {"layout": "Layout", "alert_on_down": "Critical alert when a monitor goes down (clears when it's back up)"},
    },
    "pcstats": {
        "layout": [
            ("Basics", "Setup", ["source", "lhm_url", "discover"]),
            ("Basics", "When to show", ["show", "takeover"]),
            ("Screens", "Graph", ["layout", "graph_minutes", "temp_unit"]),
            ("Screens", "Graph lines", [f"line{n}{s}" for n in range(1, 5) for s in ("", "_color", "_graph")]),
            ("Advanced", "Timing", ["duration", "refresh", "lhm_refresh"]),
            ("Advanced", "Sensors (picked automatically; change one here)",
             ["cpu_load_entity", "cpu_temp_entity", "gpu_load_entity", "gpu_temp_entity", "ram_entity",
              "net_down_entity", "net_up_entity", "lhm_cpu_load", "lhm_cpu_temp", "lhm_gpu_load", "lhm_gpu_temp",
              "lhm_ram", "lhm_net_down", "lhm_net_up", "lhm_gpu_power", "lhm_cpu_power", "lhm_gpu_fan",
              "lhm_gpu_clock", "lhm_cpu_clock", "lhm_vram_used", "lhm_vram_total"]),
        ],
        "labels": {"graph_minutes": "Time span"},
    },
    "klipper": {
        "layout": [
            ("Basics", "Setup", ["url", "api_key", "printer_name"]),
            ("Basics", "When to show", ["show_when_idle", "show_finished_minutes", "takeover"]),
            ("Screens", "Printing", ["eta_style", "show_thumbnail", "picture_source", "camera_url"]),
            ("Advanced", "Timing", ["duration", "refresh"]),
        ],
    },
    "clock": {
        "layout": [
            ("Basics", "Setup", ["format_24h", "show_seconds", "leading_zero", "blink_colon", "show_ampm",
                                        "show_date", "extra_text"]),
            ("Look", "Time", ["time_font", "time_size", "color"]),
            ("Look", "AM / PM", ["ampm_font", "ampm_size", "ampm_color", "ampm_lowercase"]),
            ("Look", "Date", ["date_format", "date_custom", "date_uppercase", "date_font", "date_size", "date_color"]),
            ("Look", "Extra line", ["extra_font", "extra_size", "extra_color"]),
            ("Look", "Arrangement", ["align", "line_gap", "smooth"]),
            ("Advanced", "Timing", ["duration"]),
        ],
        "labels": {"time_font": "Font", "time_size": "Size (pixels)", "color": "Color", "ampm_font": "Font",
                   "ampm_size": "Size (pixels)", "ampm_color": "Color (blank = same as the time)", "date_font": "Font",
                   "date_size": "Size (pixels)", "date_color": "Color", "extra_font": "Font",
                   "extra_size": "Size (pixels)", "extra_color": "Color", "date_format": "Format",
                   "date_custom": "Custom pattern", "date_uppercase": "Uppercase"},
    },
    "ambient": {
        "layout": [
            ("Basics", "Setup", ["animation", "cycle_minutes"]),
            ("Advanced", "Timing", ["duration"]),
        ],
    },
    "message": {
        "layout": [
            ("Basics", "Setup", ["text"]),
            ("Look", "Colors", ["color", "font"]),
            ("Advanced", "Timing", ["duration", "speed"]),
        ],
        "labels": {"color": "Text color", "font": "Font", "speed": "Scroll speed (pixels per second)"},
    },
}

SCROLL = ("Advanced", "Scroll mode", ["scroll_mode", "scroll_speed", "scroll_gap"])


def arrange(name, fields, league=False):
    """The board's fields in the standard sections (fields not listed keep their place, after the rest)."""
    from .base import organize
    spec = SPORTS if league else LAYOUTS.get(name)
    if not spec:
        return fields
    labels = dict(COMMON_LABELS, **spec.get("labels", {}))
    return organize(fields, list(spec["layout"]) + [SCROLL], labels)
