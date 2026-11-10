"""UFC: the next event's countdown, fight card, live bouts, results and the event schedule.

Uses ESPN's public MMA scoreboard (no key needed), the same source as the other sports boards.
Fighters have red / blue corners instead of team colors; pictures are ESPN headshots, with the
fighter's country flag as a fallback.
"""
from __future__ import annotations

import copy
import logging
import math
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from PIL import Image, ImageOps

from .base import Board, organize
from ..gfx import DIM, GRAY, WHITE, fetch_image, fill_height, fit_image, scale, trim_alpha

log = logging.getLogger(__name__)

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
HEADSHOT = "https://a.espncdn.com/i/headshots/mma/players/full/{id}.png"
FLAG = "https://a.espncdn.com/i/teamlogos/countries/500/{code}.png"
RED = (220, 30, 35)
BLUE = (40, 95, 235)
GOLD = (255, 190, 40)
LIVE_RED = (255, 40, 40)
CORNERS = (RED, BLUE)

BOUTS = {"main": "Main event only", "main_card": "Main card", "all": "Full card (prelims too)"}
FIGHT_LAYOUTS = {
    "card": "Fight card (pictures at the edges, names and records stacked)",
    "faceoff": "Face-off (red and blue corners, VS in the middle)",
    "tape": "Tale of the tape (names, records and flags side by side)",
}
COUNTDOWN_LAYOUTS = {
    "event": "Event (UFC logo and event number, date and start time, who's fighting)",
    "fightweek": "Fight week (DAYS / HRS / MIN / SEC tiles)",
    "versus": "Versus (big names on red and blue corners)",
    "poster": "Fight poster (main event fighters at the edges, countdown in the middle)",
    "octagon": "Octagon (days to go inside an eight-sided cage)",
    "minimal": "Minimal (event name and a big countdown)",
}
LOGO_URLS = ["https://a.espncdn.com/i/teamlogos/leagues/500-dark/ufc.png",
             "https://a.espncdn.com/i/teamlogos/leagues/500/ufc.png"]
PICTURES = {"headshots": "Headshots (flag if there's no photo)", "flags": "Country flags", "none": "No pictures"}
WIN_SECONDS = 8.0


# ---------------------------------------------------------------------------- parsing
def parse_time(s):
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def _d(x):
    return x if isinstance(x, dict) else {}


def _l(x):
    return x if isinstance(x, list) else []


def last_name(name):
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return "TBA"
    if len(parts) >= 3 and parts[-2].lower() in ("da", "de", "dos", "van", "von", "del", "la", "le", "st.", "di"):
        return " ".join(parts[-2:])
    if parts[-1].lower().rstrip(".") in ("jr", "sr", "ii", "iii", "iv") and len(parts) > 2:
        return parts[-2]
    return parts[-1]


def short_method(text):
    t = (text or "").strip()
    lo = t.lower()
    if not t:
        return ""
    if "ko" in lo:
        return "KO/TKO"
    if "sub" in lo:
        return "SUB"
    if "dec" in lo:
        for key, short in (("unan", "U DEC"), ("split", "S DEC"), ("major", "M DEC")):
            if key in lo:
                return short
        return t.upper() if len(t) <= 6 else "DEC"
    if "no contest" in lo or lo == "nc":
        return "NC"
    if "draw" in lo:
        return "DRAW"
    if "dq" in lo or "disq" in lo:
        return "DQ"
    return t.upper()[:10]


def _fighter(c):
    c = _d(c)
    a = _d(c.get("athlete"))
    aid = str(a.get("id") or c.get("id") or "")
    name = a.get("displayName") or a.get("fullName") or c.get("displayName") or "TBA"
    flag = _d(a.get("flag"))
    rec = ""
    for r in _l(c.get("records")):
        if isinstance(r, dict) and r.get("summary"):
            rec = r["summary"]
            break
    hs = a.get("headshot")
    hs = hs.get("href") if isinstance(hs, dict) else (hs if isinstance(hs, str) else "")
    winner = c.get("winner")
    return {"id": aid, "name": name, "last": last_name(name), "record": str(rec or ""),
            "country": flag.get("alt") or "", "flag": flag.get("href") or "",
            "headshot": hs or (HEADSHOT.format(id=aid) if aid.isdigit() else ""),
            "winner": winner if isinstance(winner, bool) else None}


def _segment(comp):
    texts = []
    cs = comp.get("cardSegment")
    if isinstance(cs, dict):
        texts += [cs.get("description"), cs.get("name")]
    elif isinstance(cs, str):
        texts.append(cs)
    for n in _l(comp.get("notes")):
        if isinstance(n, dict):
            texts.append(n.get("headline") or n.get("text"))
    lo = " ".join(str(x).lower() for x in texts if x)
    if "early" in lo:
        return "early"
    if "prelim" in lo:
        return "prelims"
    if "main" in lo:
        return "main"
    return None


def _networks(obj):
    out = []
    for b in _l(obj.get("broadcasts")):
        b = _d(b)
        for n in _l(b.get("names")):
            if isinstance(n, str):
                out.append(n)
        sn = _d(b.get("media")).get("shortName")
        if isinstance(sn, str):
            out.append(sn)
    for b in _l(obj.get("geoBroadcasts")):
        sn = _d(_d(b).get("media")).get("shortName")
        if isinstance(sn, str):
            out.append(sn)
    return list(dict.fromkeys(out))


def parse_fight(comp, ev_date=None):
    comp = _d(comp)
    st = _d(comp.get("status"))
    typ = _d(st.get("type"))
    res = _d(st.get("result")) or _d(comp.get("result"))
    comps = _l(comp.get("competitors"))
    comps = sorted((c for c in comps if isinstance(c, dict)),
                   key=lambda c: c.get("order") if isinstance(c.get("order"), int) else 99)
    fighters = [_fighter(c) for c in comps[:2]]
    while len(fighters) < 2:
        fighters.append(_fighter({}))
    ctype = _d(comp.get("type"))
    pool = [ctype.get("abbreviation"), ctype.get("text"), ctype.get("name"), comp.get("name")]
    pool += [_d(n).get("headline") for n in _l(comp.get("notes"))]
    pool = [str(p) for p in pool if p and isinstance(p, (str, int))]
    weight = next((p for p in pool if "weight" in p.lower()), ctype.get("abbreviation") or "")
    weight = re.sub(r"\s*-\s*(main event|co-main.*|title fight|championship)$", "", str(weight or ""), flags=re.I)
    title = any(("title" in p.lower() or "championship" in p.lower()) and "eliminator" not in p.lower() for p in pool)
    rounds = _d(_d(comp.get("format")).get("regulation")).get("periods")
    state = typ.get("state") or "pre"
    if typ.get("completed"):
        state = "post"
    period = st.get("period") if isinstance(st.get("period"), int) else 0
    method_raw = res.get("shortDisplayName") or res.get("displayName") or res.get("name") or ""
    if state == "post" and not method_raw:
        det = typ.get("detail") or typ.get("shortDetail") or ""
        method_raw = re.sub(r"^final\s*[-:]?\s*", "", det, flags=re.I)
    return {
        "id": str(comp.get("id") or id(comp)),
        "start": parse_time(comp.get("startDate") or comp.get("date")) or ev_date,
        "weight": weight, "title": title,
        "rounds": rounds if isinstance(rounds, int) else (5 if title else 3),
        "segment": _segment(comp),
        "state": state, "round": period, "clock": st.get("displayClock") or "",
        "detail": typ.get("shortDetail") or typ.get("detail") or "",
        "method": short_method(method_raw),
        "networks": _networks(comp),
        "fighters": fighters,
        "main_event": False, "co_main": False,
    }


def _matchup_names(event_name):
    """('pantoja', 'asakura') from 'UFC 310: Pantoja vs. Asakura'."""
    tail = (event_name or "").split(":", 1)[-1]
    m = re.split(r"\s+vs\.?\s+", tail.strip(), flags=re.I)
    if len(m) != 2:
        return None
    return tuple(re.sub(r"\s+\d+$", "", s).strip().lower() for s in m)


def parse_event(ev):
    ev = _d(ev)
    date = parse_time(ev.get("date"))
    fights = []
    for comp in _l(ev.get("competitions")):
        try:
            fights.append(parse_fight(comp, date))
        except Exception as e:                      # one odd bout never hides the whole card
            log.debug("skipping UFC bout: %s", e)
    name = ev.get("name") or ev.get("shortName") or "UFC"
    # card order: main event first
    names = _matchup_names(name)
    hit = None
    if names:
        for i, f in enumerate(fights):
            fn = " ".join(x["name"].lower() for x in f["fighters"])
            if all(n.split()[-1] in fn for n in names if n):
                hit = i
                break
    if hit is not None:
        if hit >= len(fights) / 2:
            fights.reverse()
    elif len({f["start"] for f in fights if f["start"]}) > 1:
        fights.sort(key=lambda f: f["start"] or date, reverse=True)
    else:
        fights.reverse()                            # ESPN lists the early prelims first
    if fights:
        fights[0]["main_event"] = True
        if len(fights) > 1:
            fights[1]["co_main"] = True
    if not any(f["segment"] == "main" for f in fights):
        for i, f in enumerate(fights):
            f["segment"] = "main" if i < 5 else (f["segment"] or "prelims")
    for f in fights:
        f["segment"] = f["segment"] or "prelims"
    states = [f["state"] for f in fights]
    if "in" in states or ("post" in states and "pre" in states):
        state = "in"
    elif states and all(s == "post" for s in states):
        state = "post"
    else:
        state = _d(_d(ev.get("status")).get("type")).get("state") or "pre"
    main_starts = [f["start"] for f in fights if f["segment"] == "main" and f["start"]]
    starts = [f["start"] for f in fights if f["start"]]
    venue = {}
    for comp in _l(ev.get("competitions")):
        venue = _d(_d(comp).get("venue"))
        if venue:
            break
    venue = venue or _d((_l(ev.get("venues")) or [{}])[0])
    addr = _d(venue.get("address"))
    city = ", ".join(x for x in (addr.get("city"), addr.get("state") or addr.get("country")) if x)
    nets = list(dict.fromkeys(_networks(ev) + [n for f in fights for n in f["networks"]]))
    return {
        "id": str(ev.get("id") or name),
        "name": name, "short": ev.get("shortName") or name.split(":")[0],
        "start": date or (min(starts) if starts else None),
        "main_start": min(main_starts) if main_starts else None,
        "last_start": max(starts) if starts else date,
        "end": parse_time(ev.get("endDate")),
        "state": state, "fights": fights,
        "venue": venue.get("fullName") or "", "city": city, "networks": nets,
        "logo": next((lo.get("href") for lo in map(_d, _l(ev.get("logos"))) if isinstance(lo.get("href"), str)), ""),
    }


def league_logos(j):
    """UFC logo URLs from the scoreboard, versions made for dark backgrounds first."""
    out = []
    for lg in _l(_d(j).get("leagues")):
        for lo in _l(_d(lg).get("logos")):
            lo = _d(lo)
            if isinstance(lo.get("href"), str):
                out.append((0 if "dark" in " ".join(map(str, _l(lo.get("rel")))) else 1, lo["href"]))
    return [u for _k, u in sorted(out)]


def event_number(short):
    """('310', None) for 'UFC 310'; (None, ['FIGHT', 'NIGHT']) for a Fight Night."""
    m = re.search(r"\b(\d{2,4})\b", short or "")
    if m:
        return m.group(1), None
    rest = re.sub(r"^\s*UFC\s*", "", short or "", flags=re.I).upper().split()
    if not rest or rest[:2] in (["FIGHT", "NIGHT"], ["FN"]):
        return None, ["FIGHT", "NIGHT"]
    return None, rest[:2]


def parse_calendar(j):
    out = []
    for lg in _l(_d(j).get("leagues")):
        for c in _l(_d(lg).get("calendar")):
            if isinstance(c, dict):
                start = parse_time(c.get("startDate"))
                if start:
                    out.append({"label": c.get("label") or "", "start": start, "end": parse_time(c.get("endDate"))})
            elif isinstance(c, str):
                d = parse_time(c)
                if d:
                    out.append({"label": "", "start": d, "end": None})
    out.sort(key=lambda c: c["start"])
    return out


def split_label(label):
    """('UFC 310', 'PANTOJA VS ASAKURA') from a calendar / event name."""
    if ":" in label:
        a, b = label.split(":", 1)
        return a.strip().upper(), re.sub(r"\s+vs\.?\s+", " VS ", b.strip(), flags=re.I).upper()
    return label.strip().upper(), ""


def when_text(dt, now=None):
    if dt is None:
        return "TBA"
    local = dt.astimezone()
    now = (now or datetime.now()).astimezone()
    hm = local.strftime("%I:%M%p").lstrip("0").replace(":00", "")
    days = (local.date() - now.date()).days
    if days == 0:
        return "TONIGHT " + hm if local.hour >= 17 else "TODAY " + hm
    if days == 1:
        return "TMRW " + hm
    if 1 < days < 7:
        return local.strftime("%a").upper() + " " + hm
    return local.strftime("%b %-d").upper() + " " + hm


def countdown_text(secs):
    secs = max(0, int(secs))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}D {h:02d}:{m:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------- samples
SAMPLE_FIGHTERS = [
    ("Marco Reyes", "22-3-0", "mex", "Mexico"), ("Dan Kowalski", "19-4-0", "pol", "Poland"),
    ("Tiago Lima", "15-2-0", "bra", "Brazil"), ("Jake Turner", "17-5-0", "usa", "USA"),
    ("Kenji Sato", "12-1-0", "jpn", "Japan"), ("Liam O'Brien", "14-6-0", "irl", "Ireland"),
    ("Ana Costa", "11-2-0", "bra", "Brazil"), ("Erin Walsh", "9-3-0", "usa", "USA"),
    ("Ivan Petrov", "24-4-0", "rus", "Russia"), ("Sam Okafor", "13-2-0", "nga", "Nigeria"),
]
SAMPLE_WEIGHTS = ["Lightweight", "Welterweight", "Bantamweight", "Women's Strawweight", "Heavyweight"]


def sample_event(kind="pre"):
    """A made-up event in ESPN's format, run through the normal parser."""
    now = datetime.now(timezone.utc)
    start = {"pre": now + timedelta(days=2, hours=5), "countdown": now + timedelta(days=3, hours=4, minutes=12)}.get(
        kind, now - timedelta(hours=1))
    comps = []
    for i in range(5):
        a, b = SAMPLE_FIGHTERS[2 * i], SAMPLE_FIGHTERS[2 * i + 1]
        if kind == "live":
            st = {0: ("in", False)}.get(i, ("post", True) if i else ("pre", False))
            st = ("in", False) if i == 0 else ("post", True)
        elif kind in ("post", "win"):
            st = ("post", True)
        else:
            st = ("pre", False)
        result = {}
        if st[0] == "post":
            result = {"shortDisplayName": ["KO/TKO", "SUB", "U Dec", "KO/TKO", "S Dec"][i]}
        comps.append({
            "id": f"s{i}", "date": (start - timedelta(minutes=30 * i)).isoformat(),
            "type": {"abbreviation": SAMPLE_WEIGHTS[i] + (" Title Fight" if i == 0 else "")},
            "format": {"regulation": {"periods": 5 if i == 0 else 3}},
            "status": {"period": 2 if st[0] == "in" else ([3, 1, 3, 2, 3][i] if st[0] == "post" else 0),
                       "displayClock": "3:12" if st[0] == "in" else (["5:00", "4:21", "5:00", "1:48", "5:00"][i]),
                       "type": {"state": st[0], "completed": st[1], "shortDetail": "Final" if st[1] else ""},
                       "result": result},
            "cardSegment": {"name": "main", "description": "Main Card"},
            "broadcasts": [{"names": ["ESPN+ PPV"]}],
            "competitors": [
                {"id": f"x{2 * i + k}", "order": k + 1, "winner": (k == 0) if st[0] == "post" else None,
                 "athlete": {"displayName": f[0], "flag": {"href": FLAG.format(code=f[2]), "alt": f[3]}},
                 "records": [{"summary": f[1]}]}
                for k, f in enumerate((a, b))],
        })
    comps.reverse()                                       # prelims-first, like ESPN
    ev = {"id": f"sample-{kind}", "name": "UFC 999: Reyes vs. Kowalski", "shortName": "UFC 999",
          "date": (start - timedelta(minutes=120)).isoformat(), "competitions": comps}
    return parse_event(ev)


# ---------------------------------------------------------------------------- board
class Win:
    """Intro slide: a favorite fighter just won."""
    intro = True

    def __init__(self, event, fight, side):
        self.event, self.fight, self.side = event, fight, side
        self.created = time.time()

    def __eq__(self, other):
        return self is other

    def __hash__(self):
        return id(self)


class UFCBoard(Board):
    name = "ufc"
    title = "UFC"
    group = "Sports"
    description = ("UFC events: countdown to the next card, the bouts with fighter pictures and records, "
                   "live round and clock, results by KO / submission / decision, and the upcoming schedule.")
    refresh = 900
    PREVIEW_SECONDS = 180
    DEFAULTS = {"enabled": False, "duration": 8, "refresh": 900, "live_refresh": 30,
                "favorites": [], "favorites_only": False, "bouts": "main_card",
                "layout": "card", "pictures": "headshots", "show_network": True,
                "show_countdown": True, "countdown_days": 7, "countdown_layout": "poster", "event_logo_url": "", "countdown_to": "main",
                "card_days": 2, "final_hours": 24,
                "show_schedule": True, "schedule_count": 6, "schedule_when": "always",
                "takeover": False, "celebrate": True}
    FIELDS = [
        {"key": "favorites", "label": "Favorite fighters (comma-separated names)", "type": "list",
         "help": "Last names are enough, e.g. Jones, Pereira. Favorites get a ★, a winner celebration and can take "
                 "over the display while they fight."},
        {"key": "favorites_only", "label": "Only show bouts with a favorite fighter", "type": "bool",
         "help": "The countdown and schedule still show."},
        {"key": "bouts", "label": "Which bouts", "type": "select", "options": list(BOUTS), "option_labels": BOUTS},
        {"key": "preview", "label": "Preview", "type": "actions", "action": "preview",
         "options": {"pre": "Upcoming", "live": "Live", "post": "Results", "countdown": "Countdown",
                     "schedule": "Schedule", "win": "Winner celebration"},
         "help": "Shows the screens for 3 minutes with your current settings, using the next real event when it's "
                 "loaded (sample fighters otherwise)."},
        {"key": "layout", "label": "Design", "type": "select", "options": list(FIGHT_LAYOUTS),
         "option_labels": FIGHT_LAYOUTS},
        {"key": "pictures", "label": "Fighter pictures", "type": "select", "options": list(PICTURES),
         "option_labels": PICTURES},
        {"key": "show_network", "label": "Show the TV / streaming network", "type": "bool"},
        {"key": "show_countdown", "label": "Countdown to the next event", "type": "bool"},
        {"key": "countdown_days", "label": "Start the countdown this many days before", "type": "number",
         "show_if": {"show_countdown": True}},
        {"key": "countdown_to", "label": "Count down to", "type": "select", "options": ["main", "first"],
         "option_labels": {"main": "The main card", "first": "The first fight (early prelims)"},
         "show_if": {"show_countdown": True}},
        {"key": "countdown_layout", "label": "Countdown design", "type": "select", "options": list(COUNTDOWN_LAYOUTS),
         "option_labels": COUNTDOWN_LAYOUTS, "show_if": {"show_countdown": True}},
        {"key": "event_logo_url", "label": "Event logo image (optional URL)", "type": "text",
         "placeholder": "UFC logo from ESPN",
         "help": "Leave blank for the UFC logo. Paste a link to an image (e.g. a PNG of the event's own logo) to use "
                 "that instead on the countdown designs that show a logo.",
         "show_if": {"show_countdown": True}},
        {"key": "card_days", "label": "Show the upcoming bouts this many days before", "type": "number",
         "help": "0 = only on fight day."},
        {"key": "final_hours", "label": "Show results for (hours after the event)", "type": "number"},
        {"key": "show_schedule", "label": "Upcoming events list", "type": "bool"},
        {"key": "schedule_count", "label": "How many events", "type": "number", "show_if": {"show_schedule": True}},
        {"key": "schedule_when", "label": "Show the list", "type": "select", "options": ["always", "no_event"],
         "option_labels": {"always": "Always", "no_event": "Only when there's no fight card or results to show"},
         "show_if": {"show_schedule": True}},
        {"key": "takeover", "label": "Take over the display while a favorite (or the main event) is fighting",
         "type": "bool"},
        {"key": "celebrate", "label": "Winner celebration when a favorite fighter wins", "type": "bool"},
        {"key": "duration", "label": "Seconds per screen", "type": "number"},
        {"key": "refresh", "label": "Refresh interval (s)", "type": "number"},
        {"key": "live_refresh", "label": "Refresh while fights are live (s)", "type": "number"},
    ]
    FIELDS = organize(FIELDS, [
        ("Fighters", ["favorites", "favorites_only", "bouts"]),
        ("Preview", ["preview"]),
        ("Fight screens", ["layout", "pictures", "show_network"]),
        ("Before the event", ["show_countdown", "countdown_days", "countdown_to", "countdown_layout", "event_logo_url",
                              "card_days"]),
        ("After the event", ["final_hours"]),
        ("Schedule", ["show_schedule", "schedule_count", "schedule_when"]),
        ("Live", ["takeover", "celebrate", "live_refresh"]),
        ("Timing", ["duration", "refresh"]),
    ])

    def __init__(self, cfg, app):
        super().__init__(cfg, app)
        self.events = {}            # id -> parsed event
        self.calendar = []          # [{label, start, end}]
        self.pics = {}              # url -> trimmed source image (None = failed)
        self.logo_urls = []
        self._cut = {}              # (url, w, h, flip, mode) -> ready image
        self._ended = {}            # event id -> when we first saw it finished
        self._seen_wins = None      # fight ids with a winner (None until the first load)
        self.pending = []
        self._lock = threading.Lock()
        self.preview_state = ([], 0, "")
        self.session = requests.Session()

    # -- data -------------------------------------------------------------------
    def _get(self, params=None):
        r = self.session.get(SCOREBOARD, params=params or {}, timeout=12)
        r.raise_for_status()
        return r.json()

    def update(self):
        j = self._get()
        events = {}
        for ev in _l(j.get("events")):
            try:
                e = parse_event(ev)
                events[e["id"]] = e
            except Exception as ex:
                log.info("skipping UFC event: %s", ex)
        cal = parse_calendar(j) or self.calendar
        self.logo_urls = league_logos(j) or self.logo_urls
        now = datetime.now(timezone.utc)
        # make sure the next event (or two) on the calendar has its fight card loaded
        loaded_days = {e["start"].date() for e in events.values() if e["start"]}
        wanted = [c for c in cal if (c["end"] or c["start"] + timedelta(hours=12)) > now][:2]
        for c in wanted:
            d = c["start"]
            if any(abs((d.date() - x).days) <= 1 for x in loaded_days):
                continue
            for params in ({"dates": f"{(d - timedelta(days=1)):%Y%m%d}-{(d + timedelta(days=1)):%Y%m%d}"},
                           {"dates": (d - timedelta(hours=6)).strftime("%Y%m%d")}):
                try:
                    jj = self._get(params)
                except requests.HTTPError:
                    continue
                for ev in _l(jj.get("events")):
                    try:
                        e = parse_event(ev)
                    except Exception:
                        continue
                    events.setdefault(e["id"], e)
                    if e["start"]:
                        loaded_days.add(e["start"].date())
                break
        # remember when each event finished (for "show results for N hours")
        for e in events.values():
            if e["state"] == "post":
                self._ended.setdefault(e["id"], e["end"] if e["end"] and e["end"] < now else
                                       max(now - timedelta(hours=1), (e["last_start"] or now)) if e["last_start"] else now)
        self._check_wins(events)
        self.events, self.calendar = events, cal
        for e in self._relevant_events():
            for f in e["fights"]:
                for fi in f["fighters"]:
                    self._load_pics(fi)
        self._load_logos(list(self.events.values()))

    def _logo_cands(self, e=None):
        custom = (self.cfg.get("event_logo_url") or "").strip()
        return [u for u in [custom, (e or {}).get("logo")] + self.logo_urls + LOGO_URLS if u]

    def _load_logos(self, events):
        retry = self.__dict__.setdefault("_pic_retry", {})
        for e in events or [None]:
            for u in self._logo_cands(e):
                if self.pics.get(u) is not None:
                    break
                if u in self.pics and time.time() < retry.get(u, 0):
                    continue
                img = fetch_image(u, 200, 200, trim=True)
                self.pics[u] = trim_alpha(img) if img is not None else None
                if img is not None:
                    break
                retry[u] = time.time() + 3600

    def _logo(self, e, w, h):
        """The event's (or UFC's) logo fitted to w x h; dark logos are lit up so they show on the panel."""
        for u in self._logo_cands(e):
            src = self.pics.get(u)
            if src is None:
                continue
            key = (u, w, h, False, "logo")
            if key not in self._cut:
                img = fit_image(src, w, h).copy()
                px = [p for p in img.getdata() if p[3] > 128]
                if px and sum(max(p[:3]) for p in px) / len(px) < 70:      # black wordmark: make it white
                    a = img.getchannel("A")
                    img = Image.new("RGBA", img.size, (235, 235, 235, 255))
                    img.putalpha(a)
                self._cut[key] = img
            return self._cut[key]
        return None

    def _draw_logo(self, c, e, x, y, w, h):
        """Logo inside the box; a pixel 'UFC' when no logo could be downloaded."""
        img = self._logo(e, w, h)
        if img is not None:
            c.paste(img, x + (w - img.width) // 2, y + (h - img.height) // 2)
            return
        font = "10x20" if w >= 32 and h >= 18 else ("7x13B" if h >= 12 else "5x7")
        tw = c.text_width("UFC", font)
        fh = {"10x20": 20, "7x13B": 13}.get(font, 7)
        tx, ty = x + (w - tw) // 2, y + (h - fh) // 2
        c.text(tx + 1, ty + 1, "UFC", font, scale(RED, 0.35))
        c.text(tx, ty, "UFC", font, RED)

    def _check_wins(self, events):
        wins = {}
        for e in events.values():
            for f in e["fights"]:
                for side, fi in enumerate(f["fighters"]):
                    if f["state"] == "post" and fi["winner"]:
                        wins[f["id"]] = (e, f, side)
        if self._seen_wins is None:
            self._seen_wins = set(wins)
            return
        for fid, (e, f, side) in wins.items():
            if fid in self._seen_wins:
                continue
            self._seen_wins.add(fid)
            if self.cfg.get("celebrate", True) and self._is_fav(f["fighters"][side]):
                with self._lock:
                    self.pending.append(Win(e, f, side))

    def interval(self):
        now = datetime.now(timezone.utc)
        for e in self.events.values():
            if e["state"] == "in" or (e["state"] == "pre" and e["start"] and
                                      timedelta(minutes=-1) < e["start"] - now < timedelta(minutes=30)):
                return max(10.0, float(self.cfg.get("live_refresh", 30) or 30))
        return super().interval()

    # -- pictures ---------------------------------------------------------------
    def _pic_mode(self):
        p = self.cfg.get("pictures", "headshots")
        return p if p in PICTURES else "headshots"

    def _load_pics(self, fi):
        mode = self._pic_mode()
        urls = ([fi["headshot"]] if mode == "headshots" and fi["headshot"] else []) + \
               ([fi["flag"]] if mode != "none" and fi["flag"] else [])
        for u in urls:
            if u in self.pics and self.pics[u] is not None:
                continue
            retry = self.__dict__.setdefault("_pic_retry", {})
            if u in self.pics and time.time() < retry.get(u, 0):
                continue
            img = fetch_image(u, 160, 160, trim=True)
            if img is not None:
                img = trim_alpha(img)                # fetch_image pads to a square
            self.pics[u] = img
            if img is None:
                retry[u] = time.time() + 3600
            if img is not None and u == fi["headshot"]:
                break                                # got the photo: no need for the flag

    def _picture(self, fi, w, h, flip=False, kind=None):
        """Ready-to-paste picture for a fighter (photo cut to w x h, or a flag), or None."""
        mode = self._pic_mode() if kind is None else kind
        if mode == "none":
            return None
        cands = []
        if mode == "headshots" and fi.get("headshot"):
            cands.append(("photo", fi["headshot"]))
        if fi.get("flag"):
            cands.append(("flag", fi["flag"]))
        for what, u in cands:
            src = self.pics.get(u)
            if src is None:
                continue
            key = (u, w, h, flip, what)
            if key not in self._cut:
                if what == "photo":
                    img = fill_height(src, h, max_w=w)
                    if flip:
                        img = ImageOps.mirror(img)
                else:
                    fw = min(w, round(h * 1.5))
                    img = fit_image(src, fw, min(h, round(fw / 1.5)))
                if len(self._cut) > 200:
                    self._cut.clear()
                self._cut[key] = img
            return self._cut[key]
        return None

    def _flag(self, fi, w, h):
        src = self.pics.get(fi.get("flag") or "")
        if src is None:
            return None
        key = (fi["flag"], w, h, False, "smallflag")
        if key not in self._cut:
            self._cut[key] = fit_image(src, w, h)
        return self._cut[key]

    # -- what to show -------------------------------------------------------------
    def _favs(self):
        return [f.strip().lower() for f in self.cfg.get("favorites") or [] if f and f.strip()]

    def _is_fav(self, fi):
        name = (fi.get("name") or "").lower()
        words = set(re.split(r"[\s.'-]+", name))
        for f in self._favs():
            if f == name or f in words or (" " in f and f in name):
                return True
        return False

    def _fight_has_fav(self, f):
        return any(self._is_fav(fi) for fi in f["fighters"])

    def _bouts(self, e):
        which = self.cfg.get("bouts", "main_card")
        fights = e["fights"]
        if which == "main":
            sel = fights[:1]
        elif which == "main_card":
            sel = [f for f in fights if f["segment"] == "main"]
        else:
            sel = list(fights)
        if self._favs():                             # a favorite on the prelims still gets shown
            sel += [f for f in fights if f not in sel and self._fight_has_fav(f)]
            sel.sort(key=fights.index)
        if self.cfg.get("favorites_only"):
            sel = [f for f in sel if self._fight_has_fav(f)]
        return sel

    def _num(self, key, default):
        try:
            return float(self.cfg.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _target(self, e):
        if self.cfg.get("countdown_to", "main") == "main" and e.get("main_start"):
            return e["main_start"]
        return e["start"]

    def _sorted_events(self):
        far = datetime.max.replace(tzinfo=timezone.utc)
        return sorted(self.events.values(), key=lambda e: e["start"] or far)

    def _relevant_events(self):
        """Events whose screens show now: live, recently finished, and the next one."""
        now = datetime.now(timezone.utc)
        out = []
        evs = self._sorted_events()
        for e in evs:
            if e["state"] == "in":
                out.append(e)
            elif e["state"] == "post":
                ended = self._ended.get(e["id"], now)
                if now - ended < timedelta(hours=self._num("final_hours", 24)):
                    out.append(e)
        nxt = next((e for e in evs if e["state"] == "pre" and (e["start"] is None or e["start"] > now - timedelta(hours=12))),
                   None)
        if nxt:
            out.append(nxt)
        return out

    def _event_slides(self, e, prefix=""):
        now = datetime.now(timezone.utc)
        slides = []
        if e["state"] == "pre":
            target = self._target(e)
            secs = (target - now).total_seconds() if target else None
            if self.cfg.get("show_countdown", True) and secs is not None and \
                    secs < self._num("countdown_days", 7) * 86400:
                slides.append(f"{prefix}countdown:{e['id']}")
            start = e["start"]
            if start is not None:
                days = (start.astimezone().date() - datetime.now().astimezone().date()).days
                if days > self._num("card_days", 2):
                    return slides
        bouts = self._bouts(e)
        if e["state"] == "in":
            bouts = [f for f in bouts if f["state"] == "in"] + [f for f in bouts if f["state"] != "in"]
        slides += [f"{prefix}fight:{e['id']}:{f['id']}" for f in bouts]
        return slides

    def _schedule(self):
        """Upcoming events: [(start, short, headline)] from the calendar (and the loaded cards)."""
        now = datetime.now(timezone.utc)
        out = []
        for c in self.calendar:
            if c["start"] > now - timedelta(hours=6):
                short, head = split_label(c["label"])
                out.append((c["start"], short or "UFC", head))
        if not out:
            for e in self._sorted_events():
                if e["state"] == "pre":
                    short, head = split_label(e["name"])
                    out.append((e["start"], short, head))
        return out[:max(1, int(self._num("schedule_count", 6)))]

    def _schedule_slides(self, prefix=""):
        n = len(self._schedule())
        return [f"{prefix}schedule:{i}" for i in range((n + 2) // 3)]

    def _preview_active(self):
        evs, until, _kind = self.preview_state
        return evs and time.time() < until

    def slides(self):
        if self._preview_active():
            evs, _until, kind = self.preview_state
            if kind == "schedule":
                return self._schedule_slides("p")
            if kind == "countdown":
                return [f"pcountdown:{evs[0]['id']}"]
            return [f"pfight:{evs[0]['id']}:{f['id']}" for f in self._bouts(evs[0]) or evs[0]["fights"][:1]]
        slides = []
        for e in self._relevant_events():
            slides += self._event_slides(e)
        if self.cfg.get("show_schedule", True) and (self.cfg.get("schedule_when") != "no_event" or not slides):
            slides += self._schedule_slides()
        return slides

    def takeover_slides(self):
        if not self.cfg.get("takeover") or self._preview_active():
            return []
        out = []
        for e in self.events.values():
            if e["state"] != "in":
                continue
            for f in e["fights"]:
                if f["state"] == "in" and (f["main_event"] or self._fight_has_fav(f)):
                    out.append(f"fight:{e['id']}:{f['id']}")
        return out

    def intro_slides(self):
        with self._lock:
            fresh = [x for x in self.pending if time.time() - x.created < 30 * 60]
            self.pending = []
        return fresh

    def slide_duration(self, slide):
        if isinstance(slide, Win):
            return WIN_SECONDS
        return self.duration

    def preview(self, kind="pre"):
        base = next((e for e in self._sorted_events() if e["state"] == "pre" and e["fights"]), None)
        if kind == "win":
            ev = self._sample_from(base, "post") if base else sample_event("post")
            f = ev["fights"][0]
            side = next((i for i, fi in enumerate(f["fighters"]) if self._is_fav(fi)), 0)
            for fi in f["fighters"]:
                fi["winner"] = fi is f["fighters"][side]
            for fi in f["fighters"]:
                self._load_pics(fi)
            with self._lock:
                self.pending.append(Win(ev, f, side))
            self.preview_state = ([ev], time.time() + 12, "post")
            return
        state = {"pre": "pre", "countdown": "countdown", "live": "live", "post": "post", "schedule": "pre"}.get(kind, "pre")
        ev = self._sample_from(base, state) if base else sample_event(state)
        for f in ev["fights"]:
            for fi in f["fighters"]:
                self._load_pics(fi)
        self._load_logos([ev])
        if kind == "schedule" and not self._schedule():
            now = datetime.now(timezone.utc)
            self.calendar = [{"label": lbl, "start": now + timedelta(days=d), "end": None} for d, lbl in (
                (5, "UFC 999: Reyes vs. Kowalski"), (12, "UFC Fight Night: Lima vs. Turner"),
                (19, "UFC Fight Night: Sato vs. O'Brien"), (26, "UFC 1000: Costa vs. Walsh"))]
        self.preview_state = ([ev], time.time() + self.PREVIEW_SECONDS, kind)

    @staticmethod
    def _sample_from(base, state):
        """The real next event, with its bouts set to look upcoming / live / finished."""
        ev = copy.deepcopy(base)
        ev["id"] = "preview-" + ev["id"]
        now = datetime.now(timezone.utc)
        methods = ["KO/TKO", "SUB", "U DEC", "KO/TKO", "S DEC", "SUB"]
        if state in ("pre", "countdown"):
            ev["state"] = "pre"
            if state == "countdown" and (not ev["start"] or ev["start"] < now):
                ev["start"] = ev["main_start"] = now + timedelta(days=3, hours=4)
            return ev
        ev["state"] = "in" if state == "live" else "post"
        for i, f in enumerate(ev["fights"]):
            if state == "live" and i == 0:
                f.update(state="in", round=2, clock="3:12")
                for fi in f["fighters"]:
                    fi["winner"] = None
                continue
            f.update(state="post", method=methods[i % len(methods)], round=[3, 1, 3, 2, 3, 2][i % 6],
                     clock=["5:00", "4:21", "5:00", "1:48", "5:00", "2:30"][i % 6])
            if f["method"].endswith("DEC"):
                f["round"], f["clock"] = f["rounds"], "5:00"
            for k, fi in enumerate(f["fighters"]):
                fi["winner"] = k == i % 2
        return ev

    # -- lookup -------------------------------------------------------------------
    def _find(self, slide):
        """(event, fight or None) for a slide id."""
        s = str(slide)
        preview = s.startswith("p")
        if preview:
            s = s[1:]
        parts = s.split(":")
        evs = {e["id"]: e for e in self.preview_state[0]} if preview else self.events
        e = evs.get(parts[1]) if len(parts) > 1 else None
        if e is None:
            return None, None
        f = None
        if parts[0] == "fight" and len(parts) > 2:
            f = next((x for x in e["fights"] if x["id"] == parts[2]), None)
        return e, f

    def status(self):
        s = super().status()
        nxt = {}
        for e in self._sorted_events():
            if e["state"] == "pre" and e["start"]:
                nxt[e["short"]] = self._target(e).isoformat()
                break
        if not nxt and self._schedule():
            st, short, _h = self._schedule()[0]
            nxt[short] = st.isoformat()
        if nxt:
            s["next_games"] = nxt
        return s

    # ======================================================================== render
    def render(self, c, slide, t):
        if isinstance(slide, Win):
            return self._render_win(c, slide, t)
        s = str(slide or "")
        kind = s.lstrip("p").split(":")[0] if s.startswith("p") else s.split(":")[0]
        if kind == "schedule":
            return self._render_schedule(c, int(s.split(":")[1]), t)
        e, f = self._find(slide)
        if e is None:
            return
        if kind == "countdown":
            lay = self.cfg.get("countdown_layout", "poster")
            {"octagon": self._cd_octagon, "minimal": self._cd_minimal, "event": self._cd_event,
             "fightweek": self._cd_fightweek, "versus": self._cd_versus}.get(lay, self._cd_poster)(c, e, t)
            return
        if f is None:
            return
        lay = self.cfg.get("layout", "card")
        {"faceoff": self._render_faceoff, "tape": self._render_tape}.get(lay, self._render_card)(c, e, f, t)

    # -- shared bits --------------------------------------------------------------
    def _name_color(self, f, fi, base=WHITE):
        if f["state"] == "post" and any(x["winner"] for x in f["fighters"]):
            return GOLD if fi["winner"] else scale(GRAY, 0.8)
        return base

    def _label(self, fi):
        return (fi["last"] or fi["name"]).upper()

    @staticmethod
    def _star(c, x, y, color=GOLD):
        """5x5 pixel star (top-left at x, y)."""
        for dx, dy in ((2, 0), (0, 1), (1, 1), (2, 1), (3, 1), (4, 1), (1, 2), (2, 2), (3, 2), (1, 3), (3, 3),
                       (0, 4), (4, 4)):
            c.pixel(x + dx, y + dy, color)

    def _name(self, c, fi, a, b, y, font, color, t, align="left"):
        """Fighter name between x=a and x=b (scrolls if too long), with a star for favorites."""
        name = self._label(fi)
        fav = self._is_fav(fi)
        room = b - a - (7 if fav else 0)
        w = c.text_width(name, font)
        if w > room:
            c.scroll_text(a, b - (7 if fav else 0), y, name, font, color, t)
            if fav:
                self._star(c, b - 5, y + 1)
            return
        x = a if align == "left" else b - w - (7 if fav else 0)
        c.text(x, y, name, font, color)
        if fav:
            self._star(c, x + w + 2, y + 1)

    def _info_items(self, e, f):
        items = []
        if f["main_event"]:
            items.append(("MAIN EVENT", GOLD))
        elif f["co_main"]:
            items.append(("CO-MAIN EVENT", (255, 220, 140)))
        if f["title"]:
            items.append((((f["weight"] + " ") if f["weight"] else "").upper() + "TITLE", GOLD))
        elif f["weight"]:
            items.append((f["weight"].upper(), GRAY))
        items.append((e["short"].upper(), scale(WHITE, 0.8)))
        nets = f["networks"] or e["networks"]
        if self.cfg.get("show_network", True) and nets and f["state"] == "pre":
            items.append((nets[0].upper(), (150, 200, 255)))
        return items

    @staticmethod
    def _cycle(c, items, t, avail, font="4x6"):
        if not items:
            return "", WHITE, 0.0
        durs = []
        for text, _ in items:
            w = c.text_width(text, font)
            durs.append(3.0 if w <= avail else 0.6 + (w - avail) / 28 + 1.2)
        t = t % sum(durs)
        for (text, color), d in zip(items, durs):
            if t < d:
                return text, color, t
            t -= d
        return items[0][0], items[0][1], 0.0

    def _status_line(self, e, f, t):
        """[(text, color)] describing the bout's state: start time, live round / clock, or the result."""
        if f["state"] == "in":
            on = int(t * 2) % 2 == 0
            return [("● " if on else "  ", LIVE_RED), (f"R{f['round'] or 1}", WHITE), ("  " + f["clock"], WHITE)]
        if f["state"] == "post":
            if not any(x["winner"] for x in f["fighters"]):
                return [(f["method"] or "FINAL", GRAY)]
            out = [(f["method"] or "FINAL", GOLD)]
            if f["method"] not in ("U DEC", "S DEC", "M DEC", "DEC") and f["round"]:
                out.append((f"  R{f['round']} {f['clock']}".rstrip(), WHITE))
            elif f["round"]:
                out.append((f"  {f['round']} RDS", GRAY))
            return out
        start = f["start"] if f["main_event"] or not e.get("main_start") else f["start"]
        return [(when_text(start), WHITE), (f"  {f['rounds']} RDS", GRAY)]

    def _draw_parts(self, c, x, y, parts, font="5x7", center=None):
        if center is not None:
            while len(parts) > 1 and sum(c.text_width(p, font) for p, _ in parts) > center[1] - center[0]:
                parts = parts[:-1]                       # drop the extras (e.g. "5 RDS") when it won't fit
            total = sum(c.text_width(p, font) for p, _ in parts)
            x = center[0] + (center[1] - center[0] - total) // 2
        for text, col in parts:
            x += c.text(x, y, text, font, col)
        return x

    def _monogram(self, c, fi, x, y, w, h, color):
        c.draw.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=3, fill=scale(color, 0.35), outline=scale(color, 0.8))
        ini = "".join(p[0] for p in fi["name"].split()[:2]).upper() or "?"
        c.text_centered(y + (h - 10) // 2, ini, "6x10", WHITE, x, x + w)

    def _paste_pic(self, c, fi, x, y, w, h, color, flip=False, dim=False):
        img = self._picture(fi, w, h, flip)
        if img is None:
            if self._pic_mode() != "none":
                self._monogram(c, fi, x, y, w, h, color)
            return
        if dim:
            img = img.copy()
            a = img.getchannel("A")
            img = Image.eval(img.convert("RGB"), lambda v: v * 4 // 10).convert("RGBA")
            img.putalpha(a)
        c.paste(img, x + (w - img.width) // 2, y + (h - img.height) // 2)

    def _loser(self, f, fi):
        return f["state"] == "post" and fi["winner"] is False and any(x["winner"] for x in f["fighters"])

    # -- design: fight card -------------------------------------------------------
    def _render_card(self, c, e, f, t):
        W, H = c.width, c.height
        pics = self._pic_mode() != "none"
        pw = 28 if pics else 0
        red, blue = f["fighters"]
        if pics:
            self._paste_pic(c, red, 0, 0, pw, H, RED, dim=self._loser(f, red))
            self._paste_pic(c, blue, W - pw, 0, pw, H, BLUE, flip=True, dim=self._loser(f, blue))
        x0, x1 = pw + 2, W - pw - 2
        text, col, tt = self._cycle(c, self._info_items(e, f), t, x1 - x0)
        c.scroll_text(x0, x1, 0, text, "4x6", col, tt, speed=28, pause=0.6)
        for row, (fi, corner) in enumerate(((red, RED), (blue, BLUE))):
            y = 7 + row * 8
            c.rect(x0, y, x0 + 1, y + 6, corner)
            rec = fi["record"]
            rw = c.text_width(rec, "4x6") if rec else 0
            room = x1 - rw - 2 - (x0 + 4) - (7 if self._is_fav(fi) else 0)
            font = "5x7" if c.text_width(self._label(fi), "5x7") <= room else "4x6"
            self._name(c, fi, x0 + 4, x1 - rw - 2, y + (1 if font == "4x6" else 0), font, self._name_color(f, fi), t)
            if rec:
                c.text_right(x1, y + 1, rec, "4x6", scale(GRAY, 0.9))
        self._draw_parts(c, x0, H - 7, self._status_line(e, f, t), "5x7", center=(x0, x1 + 1))

    # -- design: face-off ---------------------------------------------------------
    def _render_faceoff(self, c, e, f, t):
        W, H = c.width, c.height
        mid = W // 2
        red, blue = f["fighters"]
        for y in range(H):                                      # dim red / blue corners
            c.line(0, y, mid - 1, y, scale(RED, 0.22))
            c.line(mid, y, W - 1, y, scale(BLUE, 0.22))
        pics = self._pic_mode() != "none"
        pw = 26 if pics else 0
        if pics:
            self._paste_pic(c, red, 0, 0, pw, H - 8, RED, dim=self._loser(f, red))
            self._paste_pic(c, blue, W - pw, 0, pw, H - 8, BLUE, flip=True, dim=self._loser(f, blue))
        for fi, (a, b), align in ((red, (pw + 1, mid - 3), "left"), (blue, (mid + 3, W - pw - 1), "right")):
            room = b - a - (7 if self._is_fav(fi) else 0)
            font = "5x7" if c.text_width(self._label(fi), "5x7") <= room else "4x6"
            self._name(c, fi, a, b, 2, font, self._name_color(f, fi), t, align)
            if fi["record"]:
                if align == "left":
                    c.text(a + 1, 12, fi["record"], "4x6", scale(WHITE, 0.75))
                else:
                    c.text_right(b - 1, 12, fi["record"], "4x6", scale(WHITE, 0.75))
        k = 0.8 + 0.2 * math.sin(time.monotonic() * 3)
        if f["title"]:
            self._belt(c, mid, 17, k)
        else:
            c.text_centered(10, "VS", "5x7", scale(GOLD, k), mid - 8, mid + 8)
        c.rect(0, H - 8, W - 1, H - 1, (0, 0, 0))
        c.line(0, H - 8, mid - 1, H - 8, RED)
        c.line(mid, H - 8, W - 1, H - 8, BLUE)
        parts = self._status_line(e, f, t)
        if f["state"] == "pre":
            text, col, tt = self._cycle(c, [(when_text(f["start"]), WHITE)] + self._info_items(e, f), t, W - 4, "5x7")
            c.scroll_text(2, W - 2, H - 7, text, "5x7", col, tt, align="center", pause=0.5)
        else:
            self._draw_parts(c, 0, H - 7, parts, "5x7", center=(0, W))

    def _belt(self, c, cx, cy, k=1.0):
        """A tiny championship belt."""
        gold = scale(GOLD, k)
        c.rect(cx - 9, cy - 1, cx + 9, cy + 1, scale((150, 100, 30), k))
        c.draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=gold, outline=scale((255, 240, 180), k))
        c.pixel(cx, cy, scale((200, 40, 40), k))

    # -- design: tale of the tape -------------------------------------------------
    def _render_tape(self, c, e, f, t):
        W, H = c.width, c.height
        red, blue = f["fighters"]
        c.rect(0, 0, 1, H - 1, RED)
        c.rect(W - 2, 0, W - 1, H - 1, BLUE)
        text, col, tt = self._cycle(c, self._info_items(e, f), t, 80)
        c.scroll_text(24, W - 24, 0, text, "4x6", col, tt, align="center", pause=0.6)
        half = W // 2
        for fi, side in ((red, 0), (blue, 1)):
            col = self._name_color(f, fi)
            a, b = (4, half - 8) if side == 0 else (half + 8, W - 4)
            room = b - a - (7 if self._is_fav(fi) else 0)
            font = "5x7" if c.text_width(self._label(fi), "5x7") <= room else "4x6"
            self._name(c, fi, a, b, 8, font, col, t, "left" if side == 0 else "right")
            flag = self._flag(fi, 12, 8) if self._pic_mode() != "none" else None
            rec = fi["record"] or "-"
            if side == 0:
                x = 4
                if flag is not None:
                    c.paste(flag, x, 16)
                    x += 14
                c.text(x, 17, rec, "4x6", WHITE)
            else:
                x = W - 4
                if flag is not None:
                    c.paste(flag, x - 12, 16)
                    x -= 14
                c.text_right(x, 17, rec, "4x6", WHITE)
        c.text_centered(9, "VS", "4x6", DIM, half - 8, half + 8)
        c.text_centered(17, "REC", "4x6", GRAY, half - 10, half + 10)
        self._draw_parts(c, 0, H - 7, self._status_line(e, f, t), "5x7", center=(0, W))

    # -- countdowns ---------------------------------------------------------------
    def _cd_parts(self, e):
        target = self._target(e)
        secs = (target - datetime.now(timezone.utc)).total_seconds() if target else 0
        short, head = split_label(e["name"])
        if not head and e["fights"]:
            head = " VS ".join(fi["last"].upper() for fi in e["fights"][0]["fighters"])
        return target, secs, short or e["short"].upper(), head

    def _cd_info(self, e, target):
        items = [(when_text(target), WHITE)]
        if self.cfg.get("countdown_to", "main") == "main" and e.get("main_start"):
            items[0] = ("MAIN CARD " + when_text(target), WHITE)
        if self.cfg.get("show_network", True) and e["networks"]:
            items.append((e["networks"][0].upper(), (150, 200, 255)))
        if e["city"]:
            items.append((e["city"].upper(), GRAY))
        return items

    def _cd_poster(self, c, e, t):
        W, H = c.width, c.height
        target, secs, short, head = self._cd_parts(e)
        pics = self._pic_mode() != "none" and e["fights"]
        pw = 28 if pics else 0
        if pics:
            red, blue = e["fights"][0]["fighters"]
            self._paste_pic(c, red, 0, 0, pw, H, RED)
            self._paste_pic(c, blue, W - pw, 0, pw, H, BLUE, flip=True)
        x0, x1 = pw + 1, W - pw - 1
        c.text_centered(0, short, "5x7", GOLD, x0, x1)
        c.scroll_text(x0, x1, 8, head, "4x6", WHITE, t, align="center")
        c.text_centered(14, countdown_text(secs), "6x10", WHITE if secs > 0 else LIVE_RED, x0, x1)
        text, col, tt = self._cycle(c, self._cd_info(e, target), t, x1 - x0)
        c.scroll_text(x0, x1, H - 6, text, "4x6", scale(col, 0.85), tt, align="center", pause=0.5)

    def _cd_octagon(self, c, e, t):
        W, H = c.width, c.height
        target, secs, short, head = self._cd_parts(e)
        cx, cy, r = 15, 15, 15
        pts = [(cx + r * math.cos(math.radians(22.5 + 45 * i)), cy + r * math.sin(math.radians(22.5 + 45 * i)))
               for i in range(8)]
        c.draw.polygon(pts, fill=(25, 25, 30), outline=(140, 140, 150))
        inner = [(cx + (r - 3) * math.cos(math.radians(22.5 + 45 * i)), cy + (r - 3) * math.sin(math.radians(22.5 + 45 * i)))
                 for i in range(8)]
        c.draw.polygon(inner, outline=scale(RED, 0.8))
        days = int(secs // 86400)
        if days >= 1:
            big, small = str(min(days, 99)), "DAY" if days == 1 else "DAYS"
        elif secs > 3600:
            big, small = str(int(secs // 3600)), "HRS"
        elif secs > 0:
            big, small = str(max(1, int(secs // 60))), "MIN"
        else:
            big, small = "LIVE", ""
        font = "7x13B" if len(big) <= 2 else "5x7"
        c.text_centered(6 if small else 12, big, font, WHITE, 0, 31)
        if small:
            c.text_centered(20, small, "4x6", GOLD, 0, 31)
        x0 = 35
        c.text(x0, 0, short, "5x7", GOLD)
        c.scroll_text(x0, W, 9, head, "5x7", WHITE, t)
        c.text(x0, 18, countdown_text(secs), "4x6", scale(WHITE, 0.9))
        text, col, tt = self._cycle(c, self._cd_info(e, target), t, W - x0)
        c.scroll_text(x0, W, H - 6, text, "4x6", scale(col, 0.8), tt, pause=0.5)

    def _cd_minimal(self, c, e, t):
        W, H = c.width, c.height
        target, secs, short, head = self._cd_parts(e)
        c.text_centered(0, short, "5x7", GOLD)
        txt = countdown_text(secs)
        font = "7x13B" if c.text_width(txt, "7x13B") <= W - 2 else "6x10"
        c.text_centered(9, txt, font, WHITE)
        c.scroll_text(1, W - 1, H - 7, head, "5x7", scale(WHITE, 0.7), t, align="center")

    def _matchup_parts(self, e):
        """[(text, color)]: red corner name, VS, blue corner name, for the main event."""
        if not e["fights"]:
            return [(self._cd_parts(e)[3], WHITE)]
        red, blue = e["fights"][0]["fighters"]
        return [(red["last"].upper(), (255, 120, 120)), (" VS ", GOLD), (blue["last"].upper(), (130, 165, 255))]

    @staticmethod
    def _scroll_parts(c, x0, x1, y, parts, font, t, speed=18.0, pause=1.5, gap=24):
        """Like scroll_text, for text in several colors."""
        w = sum(c.text_width(p, font) for p, _ in parts)
        if w <= x1 - x0:
            x = x0
            for text, col in parts:
                x += c.text(x, y, text, font, col)
            return
        cycle = w + gap
        offset = int(max(0.0, t - pause) * speed) % cycle
        for start in (x0 - offset, x0 - offset + cycle):
            if start >= x1:
                break
            x = start
            for text, col in parts:
                c.text(x, y, text, font, col, clip=(x0, x1))
                x += c.text_width(text, font)

    def _cd_event(self, c, e, t):
        W, H = c.width, c.height
        target, secs, short, head = self._cd_parts(e)
        # left: logo + event number
        num, words = event_number(short)
        self._draw_logo(c, e, 1, 0, 36, 19 if (num or words) else 32)
        if num:
            c.text_centered(21, num, "6x10" if len(num) <= 4 else "5x7", GOLD, 0, 38)
        elif words:
            for i, wd in enumerate(words[:2]):
                c.text_centered(20 + i * 6, wd, "4x6", GOLD, 0, 38)
        c.line(39, 1, 39, H - 2, scale(RED, 0.8))
        x0 = 42
        # who's fighting
        self._scroll_parts(c, x0, W, 0, self._matchup_parts(e), "5x7", t)
        if e["fights"]:
            f = e["fights"][0]
            tag = ((f["weight"] + " ") if f["weight"] else "").upper() + ("TITLE FIGHT" if f["title"] else "")
            c.scroll_text(x0, W, 8, tag.strip() or "MAIN EVENT", "4x6", scale(GOLD, 0.85), t, speed=24)
        # date and start time
        if target is not None:
            local = target.astimezone()
            hm = local.strftime("%I:%M%p").lstrip("0").replace(":00", "")
            room = W - x0 - c.text_width(hm, "5x7") - 4
            day = next((d for d in (local.strftime("%a %b %-d").upper(), local.strftime("%a %-m/%-d").upper(),
                                    local.strftime("%-m/%-d")) if c.text_width(d, "5x7") <= room),
                       local.strftime("%-m/%-d"))
            c.text(x0, 15, day, "5x7", WHITE)
            c.text_right(W - 1, 15, hm, "5x7", GOLD)
        # countdown (and network / city)
        items = [("IN " + countdown_text(secs) if secs > 0 else "TONIGHT", scale(WHITE, 0.75))]
        if self.cfg.get("show_network", True) and e["networks"]:
            items.append((e["networks"][0].upper(), (150, 200, 255)))
        if e["city"]:
            items.append((e["city"].upper(), GRAY))
        text, col, tt = self._cycle(c, items, t, W - x0)
        c.scroll_text(x0, W, H - 6, text, "4x6", col, tt, pause=0.5)

    def _cd_fightweek(self, c, e, t):
        W, H = c.width, c.height
        target, secs, short, head = self._cd_parts(e)
        self._draw_logo(c, e, 0, 0, 20, 8)
        when = when_text(target)
        items = [(short + " · " + when, GOLD), ("".join(p for p, _ in self._matchup_parts(e)), WHITE)]
        text, col, tt = self._cycle(c, items, t, W - 23)
        c.scroll_text(23, W, 1, text, "4x6", col, tt, pause=0.5)
        secs = max(0, int(secs))
        d, rem = divmod(secs, 86400)
        hh, rem = divmod(rem, 3600)
        mm, ss = divmod(rem, 60)
        tiles = [(f"{min(d, 99):02d}", "DAYS"), (f"{hh:02d}", "HRS"), (f"{mm:02d}", "MIN"), (f"{ss:02d}", "SEC")]
        tw = (W - 2) // 4
        for i, (val, lab) in enumerate(tiles):
            x = 1 + i * tw
            c.draw.rounded_rectangle((x + 1, 9, x + tw - 2, H - 1), radius=2, fill=scale(RED, 0.3),
                                     outline=scale(RED, 0.75))
            c.line(x + 2, 15, x + tw - 3, 15, scale(RED, 0.18))          # flip-card seam
            col = WHITE if not (i == 3 and int(t * 2) % 2) else scale(WHITE, 0.85)
            c.text_centered(10, val, "7x13B" if tw >= 20 else "6x10", col, x, x + tw)
            c.text_centered(H - 7, lab, "4x6", GOLD, x, x + tw)

    def _cd_versus(self, c, e, t):
        W, H = c.width, c.height
        target, secs, short, head = self._cd_parts(e)
        mid = W // 2
        bar = H - 9
        for y in range(bar):
            cut = mid + 5 - round(y * 10 / max(1, bar - 1))
            c.line(0, y, cut - 1, y, scale(RED, 0.4))
            c.line(cut, y, W - 1, y, scale(BLUE, 0.4))
        if e["fights"]:
            red, blue = e["fights"][0]["fighters"]
            for fi, side in ((red, 0), (blue, 1)):
                name = fi["last"].upper()
                a, b = (2, mid - 6) if side == 0 else (mid + 6, W - 2)
                font = next((fn for fn in ("6x10", "5x7", "4x6") if c.text_width(name, fn) <= b - a), "4x6")
                if c.text_width(name, font) > b - a:
                    c.scroll_text(a, b, 2, name, font, WHITE, t)
                elif side == 0:
                    c.text(a, 2, name, font, WHITE)
                else:
                    c.text_right(b, 2, name, font, WHITE)
                rec = fi["record"]
                if rec and side == 0:
                    c.text(a, 13, rec, "4x6", scale(WHITE, 0.7))
                elif rec:
                    c.text_right(b, 13, rec, "4x6", scale(WHITE, 0.7))
        k = 0.8 + 0.2 * math.sin(time.monotonic() * 3)
        c.text_centered(12, "VS", "5x7", scale(GOLD, k), mid - 8, mid + 8)
        c.rect(0, bar, W - 1, H - 1, (0, 0, 0))
        c.line(0, bar, W - 1, bar, scale(GOLD, 0.6))
        self._draw_logo(c, e, 1, bar + 1, 16, 8)
        items = [(short + "  " + when_text(target), WHITE), (countdown_text(secs) + " TO GO", GOLD)]
        if self.cfg.get("show_network", True) and e["networks"]:
            items.append((e["networks"][0].upper(), (150, 200, 255)))
        text, col, tt = self._cycle(c, items, t, W - 20)
        c.scroll_text(19, W, bar + 2, text, "5x7", col, tt, align="center", pause=0.5)

    # -- schedule -----------------------------------------------------------------
    def _render_schedule(self, c, page, t):
        W = c.width
        rows = self._schedule()
        pages = max(1, (len(rows) + 2) // 3)
        c.rect(0, 0, W - 1, 6, scale(RED, 0.7))
        c.text(2, 0, "UFC SCHEDULE", "4x6", WHITE)
        if pages > 1:
            c.text_right(W - 2, 0, f"{page + 1}/{pages}", "4x6", WHITE)
        for i, (start, short, head) in enumerate(rows[page * 3:page * 3 + 3]):
            y = 8 + i * 8
            date = start.astimezone().strftime("%b %-d").upper() if start else "TBA"
            c.text(1, y, date, "4x6", GOLD)
            short = short.replace("UFC FIGHT NIGHT", "UFC FN")
            sw = c.text(28, y, short, "4x6", WHITE)
            c.scroll_text(28 + sw + 4, W, y, head, "4x6", scale(WHITE, 0.65), t, speed=24)

    # -- winner celebration -------------------------------------------------------
    def _render_win(self, c, w, t):
        W, H = c.width, c.height
        f, side = w.fight, w.side
        fi = f["fighters"][side]
        corner = CORNERS[side]
        flash = t < 2.0 and int(t * 6) % 2 == 0
        c.rect(0, 0, W - 1, H - 1, scale(corner, 0.55 if flash else 0.2))
        rnd = random.Random(int(t * 10))                         # confetti
        for _ in range(18):
            c.pixel(rnd.randrange(W), rnd.randrange(H), rnd.choice([GOLD, WHITE, corner, (255, 120, 40)]))
        pw = 0
        if self._pic_mode() != "none":
            pw = 30
            self._paste_pic(c, fi, 0, 0, pw, H, corner)
        x0 = pw + 2
        k = 0.75 + 0.25 * math.sin(t * 6)
        word = "AND NEW!" if f["title"] and t % 4 < 2 else "WINNER!"
        c.text_centered(0, word, "7x13B", scale(GOLD, k), x0, W)
        c.scroll_text(x0, W, 14, fi["name"].upper(), "5x7", WHITE, t, align="center")
        how = f["method"] or "WIN"
        if f["round"] and not how.endswith("DEC"):
            how += f" · R{f['round']} {f['clock']}"
        c.text_centered(H - 7, how, "5x7", scale(GOLD, 0.9), x0, W)
