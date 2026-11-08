"""Solar elevation (NOAA approximation) for sun-following brightness. Accurate to well under a degree."""
from __future__ import annotations

import math
from datetime import datetime, timezone


def solar_elevation(lat, lon, when=None):
    """Sun's elevation in degrees above the horizon at (lat, lon) for a UTC datetime."""
    when = when or datetime.now(timezone.utc)
    when = when.astimezone(timezone.utc)
    doy = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60 + when.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = hour * 60 + eqtime + 4 * lon                      # true solar time, minutes
    ha = math.radians(tst / 4 - 180)
    la = math.radians(lat)
    cos_zen = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zen))))


def sun_brightness(elevation, day, night):
    """Full `day` brightness with the sun 6 degrees up, `night` below civil twilight (-6), blended between."""
    if elevation >= 6:
        return day
    if elevation <= -6:
        return night
    k = (elevation + 6) / 12
    k = k * k * (3 - 2 * k)
    return round(night + (day - night) * k)
