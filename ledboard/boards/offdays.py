"""'Off day' content for league boards: next-game countdowns and standings tables (ESPN)."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import requests

from ..gfx import DIM, GRAY, WHITE, YELLOW

log = logging.getLogger(__name__)

SCHEDULE_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{team}/schedule"
STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/{sport}/{league}/standings"
ROWS_PER_PAGE = 4


# ---------------------------------------------------------------- data
def _schedule_events(sport, league_path, team_id):
    """Every event ESPN lists for the team: the default schedule (the current season type), plus the
    postseason and regular season explicitly (the default alone misses playoff games), plus upcoming
    fixtures for soccer (whose default schedule lists results). Merged by event id."""
    url = SCHEDULE_URL.format(sport=sport, league=league_path, team=team_id)
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    events, seen = [], set()

    def add(evs):
        for ev in evs or []:
            eid = ev.get("id") if isinstance(ev, dict) else None
            if eid not in seen:
                seen.add(eid)
                events.append(ev)

    add(r.json().get("events"))
    extra = [{"seasontype": 3}, {"seasontype": 2}]
    if sport == "soccer":
        extra.insert(0, {"fixture": "true"})
    for params in extra:
        try:
            rr = requests.get(url, params=params, timeout=15)
            if rr.ok:
                add(rr.json().get("events"))
        except Exception as e:                 # optional extras: never fail the lookup over them
            log.debug("schedule %s %s unavailable: %s", url, params, e)
    return events


def _next_season_events(sport, league_path, team_id):
    """Offseason: next season's schedule (preseason / spring training and regular season). ESPN names
    seasons by the year they start (NFL, MLB, MLS) or end (NBA, NHL), so try this year and next."""
    url = SCHEDULE_URL.format(sport=sport, league=league_path, team=team_id)
    year = datetime.now().year
    events = []
    for season in (year, year + 1):
        for st in ({}, {"seasontype": 1}, {"seasontype": 2}) + (({"fixture": "true"},) if sport == "soccer" else ()):
            try:
                rr = requests.get(url, params=dict(st, season=season), timeout=15)
                if rr.ok:
                    events += rr.json().get("events") or []
            except Exception as e:
                log.debug("schedule %s season %s %s unavailable: %s", url, season, st, e)
    return events


def _parse_all(events, league_key, parse_event):
    """Parse schedule / scoreboard events, skipping (and logging once) any that can't be read."""
    out, bad = [], 0
    for ev in events:
        try:
            g = parse_event(ev, league_key)
        except Exception as e:                 # one odd event (old game, special format) mustn't stop the rest
            bad += 1
            if bad == 1:
                log.info("skipping unreadable schedule event %s: %r", (ev or {}).get("id") if isinstance(ev, dict) else "?", e,
                         exc_info=True)
            continue
        if g:
            out.append(g)
    return out


def _upcoming(games, team_abbr=None, preseason=True):
    now = datetime.now(timezone.utc)
    out = []
    for g in games:
        if g["state"] == "post":
            continue
        if team_abbr and team_abbr not in (g["home"]["abbr"].upper(), g["away"]["abbr"].upper()):
            continue
        if g["start"] and g["start"] < now and g["state"] == "pre":
            continue           # stale "pre" from the past (postponed etc.)
        if not preseason and g.get("preseason"):
            continue
        out.append(g)
    return out


OFFSEASON_GAP_DAYS = 30


def next_game(sport, league_path, league_key, team_id, parse_event, scoreboard_scan=None, team_abbr=None,
              preseason=True):
    """The team's next not-yet-finished game: regular season, playoffs, or (in the offseason) next
    season's first game, parsed like a scoreboard game. The result has "offseason": True when it's
    next season's game (a new season year, or a long break on both sides of today).

    `scoreboard_scan` (optional) returns the league scoreboard's events for the coming weeks; it catches
    games that a team's schedule doesn't list (e.g. newly set playoff matchups).
    `preseason=False` skips preseason / spring training games (counts down to the real opener)."""
    now = datetime.now(timezone.utc)
    games = _parse_all(_schedule_events(sport, league_path, team_id), league_key, parse_event)
    cands = _upcoming(games, preseason=preseason)
    if scoreboard_scan is not None and team_abbr:
        try:
            cands += _upcoming(_parse_all(scoreboard_scan(), league_key, parse_event), team_abbr, preseason)
        except Exception as e:
            log.debug("scoreboard scan unavailable: %s", e)
    from_next = False
    if not cands:
        cands = _upcoming(_parse_all(_next_season_events(sport, league_path, team_id), league_key, parse_event),
                          preseason=preseason)
        from_next = bool(cands)
    if not cands:
        return None
    best = min(cands, key=lambda g: g["start"] or now)
    past = [g for g in games if g["state"] == "post" and g["start"]]
    last_end = max((g["start"] for g in past), default=None)
    last_year = max((g.get("season_year") or 0 for g in past), default=0)
    gap_ahead = (best["start"] - now).days if best["start"] else 0
    gap_behind = (now - last_end).days if last_end else 999
    best["offseason"] = bool(
        from_next
        or (best.get("season_year") and last_year and best["season_year"] > last_year)
        or (gap_ahead > OFFSEASON_GAP_DAYS and gap_behind > OFFSEASON_GAP_DAYS)
        or (best.get("preseason") and gap_ahead > 0))
    if best["offseason"] and not best.get("preseason") and not best.get("note"):
        best["note"] = "SEASON OPENER"
    return best


def _stat(entry, *names):
    for st in entry.get("stats") or []:
        if st.get("name") in names or st.get("type") in names or st.get("abbreviation") in names:
            return st
    return None


def _num(st, default=None):
    if not st:
        return default
    v = st.get("value")
    if v is None:
        try:
            v = float(str(st.get("displayValue")).lstrip("+"))
        except (TypeError, ValueError):
            return default
    return v


def record_text(sport, entry):
    """Compact record for a standings row, by sport."""
    w, l, t = (_num(_stat(entry, n), 0) for n in ("wins", "losses", "ties"))
    otl = _num(_stat(entry, "otLosses", "overtimeLosses"), 0)
    pts = _num(_stat(entry, "points"))
    overall = _stat(entry, "overall", "total")
    if sport == "hockey":
        return f"{int(w)}-{int(l)}-{int(otl)}", (f"{int(pts)}P" if pts is not None else "")
    if sport == "soccer":
        return f"{int(w)}-{int(t)}-{int(l)}", (f"{int(pts)}P" if pts is not None else "")
    rec = f"{int(w)}-{int(l)}" + (f"-{int(t)}" if t else "")
    if not (w or l) and overall and overall.get("displayValue"):
        rec = overall["displayValue"]
    gb = _stat(entry, "gamesBehind")
    extra = ""
    if sport in ("baseball", "basketball") and gb and gb.get("displayValue") not in (None, "", "-"):
        extra = gb["displayValue"]
    return rec, extra


def _groups(node, out):
    """Every node with standings entries, depth first."""
    if not isinstance(node, dict):
        return
    entries = (node.get("standings") or {}).get("entries")
    if entries:
        out.append(node)
    for ch in node.get("children") or []:
        _groups(ch, out)


def standings(sport, league_path, favorites):
    """[(group name, [rows])] for the smallest standings group containing each favorite
    (or the first group when no favorites). Rows: dict(abbr, logo, record, extra, fav, rank)."""
    data = None
    for params in ({"level": 3}, {}):        # divisions first when the API offers them
        r = requests.get(STANDINGS_URL.format(sport=sport, league=league_path), params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        groups = []
        _groups(data, groups)
        if groups:
            break
    else:
        return []
    favs = {f.upper() for f in favorites}

    def abbr(e):
        return ((e.get("team") or {}).get("abbreviation") or "").upper()

    chosen = []
    for fav in favs or [None]:
        cands = [g for g in groups if fav is None or any(abbr(e) == fav for e in g["standings"]["entries"])]
        if not cands:
            continue
        g = min(cands, key=lambda g: len(g["standings"]["entries"]))
        if g not in chosen:
            chosen.append(g)
    if not chosen and groups:
        chosen = [groups[0]]
    out = []
    for g in chosen[:2]:
        entries = list(g["standings"]["entries"])
        if any(_stat(e, "playoffSeed") for e in entries):
            entries.sort(key=lambda e: _num(_stat(e, "playoffSeed"), 99) or 99)
        elif sport in ("hockey", "soccer"):
            entries.sort(key=lambda e: -(_num(_stat(e, "points"), 0) or 0))
        else:
            entries.sort(key=lambda e: -(_num(_stat(e, "winPercent"), 0) or 0))
        rows = []
        for i, e in enumerate(entries):
            t = e.get("team") or {}
            logos = t.get("logos") or []
            rec, extra = record_text(sport, e)
            rows.append({"rank": i + 1, "abbr": abbr(e), "record": rec, "extra": extra, "fav": abbr(e) in favs,
                         "logo_url": t.get("logo") or (logos[0].get("href") if logos else None),
                         "color": t.get("color")})
        name = g.get("abbreviation") or g.get("shortName") or g.get("name") or "STANDINGS"
        out.append((name.upper(), rows))
    return out


# ---------------------------------------------------------------- drawing
def countdown_text(start):
    """'3 DAYS', 'TOMORROW', '5:42:10' (live under 24 h), 'STARTING'."""
    secs = (start - datetime.now(timezone.utc)).total_seconds()
    if secs <= 0:
        return "STARTING"
    local = start.astimezone()
    today = datetime.now().astimezone().date()
    days = (local.date() - today).days
    if secs < 24 * 3600:
        h, rem = divmod(int(secs), 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"
    if days == 1:
        return "TOMORROW"
    return f"{days} DAYS"


def render_next_game(board, c, g, fav, t, team_color):
    from .football_layouts import logo_fit
    W, H = c.width, c.height
    side = "home" if g["home"]["abbr"].upper() == fav else "away"
    opp_side = "away" if side == "home" else "home"
    me, opp = g[side], g[opp_side]
    L = 30
    c.paste(logo_fit(board, me, L, L, opp), 0, 1)
    c.paste(logo_fit(board, opp, L, L, me), W - L, 1)
    x0, x1 = L + 2, W - L - 2
    c.text_centered(0, "NEXT GAME", "4x6", GRAY, x0, x1)
    if g["start"]:
        cd = countdown_text(g["start"])
        font = "7x13B" if c.text_width(cd, "7x13B") <= x1 - x0 else "6x10"
        live = ":" in cd
        c.text_centered(7, cd, font, YELLOW if live else WHITE, x0, x1)
        when = g["start"].astimezone().strftime("%a %-I:%M%p").upper().replace(":00", "")
    else:
        c.text_centered(8, "TBD", "7x13B", WHITE, x0, x1)
        when = ""
    vs = ("@ " if side == "away" else "VS ") + opp["abbr"]
    info = [x for x in (when, vs, (g.get("note") or "").upper(), (g.get("series") or "").upper(),
                        (g.get("network") or "").upper()) if x]
    text = info[int(t // 3) % len(info)] if info else ""
    logo = board._network_logo(g, text) if hasattr(board, "_network_logo") else None
    if logo is not None:
        c.paste(logo, x0 + (x1 - x0 - logo.width) // 2, H - 8)
        return
    c.scroll_text(x0, x1, H - 8, text, "5x8", team_color(me) if text == vs else GRAY, t % 3, speed=30,
                  align="center", pause=0.3)


def render_standings(board, c, group, rows, page, sport, team_color):
    from .football_layouts import small_logo
    W = c.width
    pages = max(1, math.ceil(len(rows) / ROWS_PER_PAGE))
    head = group if pages == 1 else f"{group}  {page + 1}/{pages}"
    c.text(1, 0, head, "4x6", YELLOW)
    col = "PTS" if sport in ("hockey", "soccer") else ("GB" if sport in ("baseball", "basketball") else "")
    c.text_right(W - 1, 0, ("W-L" if sport not in ("hockey", "soccer") else ("W-L-OT" if sport == "hockey" else "W-D-L"))
                 + (f"  {col}" if col else ""), "4x6", DIM)
    for i, row in enumerate(rows[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]):
        y = 7 + i * 6
        fav = row["fav"]
        if fav:
            c.rect(0, y - 1, W - 1, y + 5, (25, 30, 45))
        c.text_right(9, y, str(row["rank"]), "4x6", YELLOW if fav else GRAY)
        team = {"logo_url": row["logo_url"]}
        logo = small_logo(board, team, 6) if row["logo_url"] in board.logos else None
        if logo is not None:
            c.paste(logo, 12, y - 1)
        tc = team_color({"color": _rgb(row.get("color")), "alt": WHITE}) if row.get("color") else WHITE
        c.text(20, y, row["abbr"], "4x6", tc if fav else WHITE)
        right = W - 1
        if row["extra"]:
            c.text_right(right, y, row["extra"], "4x6", GRAY)
            right -= 22
        c.text_right(right, y, row["record"], "4x6", WHITE if fav else GRAY)


def _rgb(hexs):
    from ..gfx import hex_to_rgb
    return hex_to_rgb(hexs, WHITE)

