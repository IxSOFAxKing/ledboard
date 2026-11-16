"""LibreHardwareMonitor's built-in web server (Options -> Remote Web Server -> Run, port 8085 by default).

GET http://<pc>:8085/data.json returns a tree: computer -> hardware (CPU, GPU, RAM, network...) -> groups
(Temperatures, Load, Clocks, Powers, Fans, Data, Throughput...) -> sensors, each with "Text" (its name) and
"Value" (e.g. "45.0 °C"). Newer versions add "SensorId" (e.g. "/gpu-nvidia/0/temperature/0") and "Type".
"""
from __future__ import annotations

import re

_NUM = re.compile(r"-?\d[\d.,]*")


def parse_value(text):
    """(number, unit) from '45.0 °C', '1,234 RPM', '3,5 GHz' (decimal comma), or (None, '')."""
    text = str(text or "").strip()
    m = _NUM.search(text)
    if not m:
        return None, ""
    num = m.group(0)
    if "," in num and "." in num:                   # whichever comes last is the decimal point
        num = num.replace(".", "").replace(",", ".") if num.rfind(",") > num.rfind(".") else num.replace(",", "")
    elif "," in num:
        head, _, tail = num.rpartition(",")
        num = (head.replace(",", "") + "." + tail) if len(tail) != 3 else num.replace(",", "")
    try:
        v = float(num)
    except ValueError:
        return None, ""
    return v, text[m.end():].strip()


def _kind(node, sensor_id=""):
    """'cpu' / 'gpu' / 'ram' / 'nic' / 'other' for a hardware node."""
    img = str(node.get("ImageURL") or "").lower()
    sid = sensor_id.lower()
    text = str(node.get("Text") or "").lower()
    if "cpu" in img or sid.startswith(("/amdcpu", "/intelcpu", "/cpu")):
        return "cpu"
    if any(k in img for k in ("nvidia", "ati", "amd", "intel")) or sid.startswith("/gpu") or \
            any(k in text for k in ("geforce", "radeon", "rtx", "gtx", "arc a")):
        return "gpu"
    if "ram" in img or sid.startswith("/ram") or text in ("memory", "generic memory", "total memory"):
        return "ram"
    if "nic" in img or sid.startswith("/nic") or "ethernet" in text or "wi-fi" in text or "wifi" in text:
        return "nic"
    return "other"


def flatten(tree):
    """Every sensor: [{"id", "path", "hw", "kind", "group", "name", "value", "unit"}].

    A node whose children all have no children of their own is a sensor group ("Temperatures", "Load"...),
    and its parent is the hardware (CPU, GPU...). Anything else with children is walked into (the computer,
    or hardware with sub-hardware like a mainboard's sensor chip)."""
    out = []

    def kids(n):
        return [k for k in (n.get("Children") or []) if isinstance(k, dict)]

    def walk(node, trail):
        groups = [k for k in kids(node) if kids(k) and all(not kids(x) for x in kids(k))]
        if groups:
            first_sid = next((str(x.get("SensorId") or "") for g in groups for x in kids(g)), "")
            hw, kind = str(node.get("Text") or ""), _kind(node, first_sid)
            for g in groups:
                gname = str(g.get("Text") or "")
                for x in kids(g):
                    v, unit = parse_value(x.get("Value"))
                    name = str(x.get("Text") or "")
                    path = "/".join(trail + (gname, name))
                    out.append({"id": str(x.get("SensorId") or "") or path, "path": path, "hw": hw, "kind": kind,
                                "group": gname, "name": name, "value": v, "unit": unit})
        for k in kids(node):
            if k not in groups and kids(k):
                walk(k, trail + (str(k.get("Text") or ""),))

    walk(tree if isinstance(tree, dict) else {}, ())
    return out


def _pick(sensors, kind, groups, names, unit_hint=None):
    """The first sensor of that hardware kind / group whose name matches the names in order of preference."""
    cands = [s for s in sensors if s["kind"] == kind and s["group"].lower() in groups and s["value"] is not None]
    if unit_hint:
        cands = [s for s in cands if unit_hint in s["unit"]] or cands
    for n in names:
        for s in cands:
            if (n.endswith("*") and s["name"].lower().startswith(n[:-1])) or s["name"].lower() == n:
                return s
    return None


# metric -> how to find it: (hardware kind, groups, sensor names in order of preference)
AUTO = {
    "cpu_load": ("cpu", {"load"}, ["cpu total", "total"]),
    "cpu_temp": ("cpu", {"temperatures"}, ["cpu package", "core (tctl/tdie)", "tctl/tdie", "core max", "core average",
                                           "package", "cpu", "core*"]),
    "gpu_load": ("gpu", {"load"}, ["gpu core", "d3d 3d", "gpu"]),
    "gpu_temp": ("gpu", {"temperatures"}, ["gpu core", "gpu", "gpu hot spot", "gpu*"]),
    "ram": ("ram", {"load"}, ["memory", "physical memory", "memory used"]),
    "net_down": ("nic", {"throughput"}, ["download speed", "download"]),
    "net_up": ("nic", {"throughput"}, ["upload speed", "upload"]),
    # extras, for the Details layout
    "gpu_power": ("gpu", {"powers", "power"}, ["gpu package", "gpu power", "gpu board power", "gpu*"]),
    "cpu_power": ("cpu", {"powers", "power"}, ["cpu package", "package", "cpu*"]),
    "gpu_fan": ("gpu", {"fans", "fan"}, ["gpu fan", "gpu fan 1", "gpu*", "fan*"]),
    "gpu_clock": ("gpu", {"clocks", "clock"}, ["gpu core", "gpu*"]),
    "cpu_clock": ("cpu", {"clocks", "clock"}, ["core #1", "cores (average)", "cpu core #1", "core*"]),
    "vram_used": ("gpu", {"data", "smalldata", "small data"}, ["gpu memory used", "d3d dedicated memory used"]),
    "vram_total": ("gpu", {"data", "smalldata", "small data"}, ["gpu memory total"]),
}


def auto_pick(sensors):
    """{metric: sensor} for every metric found automatically. Network: the busiest adapter."""
    out = {}
    for key, (kind, groups, names) in AUTO.items():
        if key in ("net_down", "net_up"):
            continue
        s = _pick(sensors, kind, groups, names)
        if s:
            out[key] = s
    nics = {}
    for s in sensors:
        if s["kind"] == "nic" and s["group"].lower() == "throughput" and s["value"] is not None:
            nics.setdefault(s["hw"], []).append(s)
    if nics:
        busiest = max(nics.values(), key=lambda ss: sum(x["value"] * _rate_factor(x["unit"]) for x in ss))
        for key in ("net_down", "net_up"):
            s = _pick(busiest, "nic", {"throughput"}, AUTO[key][2])
            if s:
                out[key] = s
    return out


def _rate_factor(unit):
    return {"KB/s": 1e3, "MB/s": 1e6, "GB/s": 1e9, "B/s": 1}.get(unit.strip(), 1)


def find(sensors, wanted):
    """A sensor by its id, its path, or (if unique) its name."""
    w = (wanted or "").strip()
    if not w:
        return None
    for s in sensors:
        if s["id"] == w or s["path"] == w:
            return s
    by_name = [s for s in sensors if s["name"].lower() == w.lower()]
    return by_name[0] if len(by_name) == 1 else None
