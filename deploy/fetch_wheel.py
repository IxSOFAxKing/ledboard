"""Download a pre-built rgbmatrix wheel that matches this Python, if one exists.

Usage: python fetch_wheel.py OWNER/REPO DEST_DIR
Prints the wheel path on success; exits 1 if no compatible wheel is published.
Uses pip's own tag logic, so it picks exactly what pip would accept.
"""
import json
import os
import sys
import urllib.request

from pip._vendor.packaging.tags import sys_tags
from pip._vendor.packaging.utils import parse_wheel_filename

repo, dest = sys.argv[1], sys.argv[2]
api = f"https://api.github.com/repos/{repo}/releases/tags/rgbmatrix-wheels"
try:
    with urllib.request.urlopen(urllib.request.Request(api, headers={"User-Agent": "ledboard-installer"}), timeout=30) as r:
        assets = json.load(r).get("assets", [])
except Exception as e:
    print(f"no pre-built wheels available ({e})", file=sys.stderr)
    sys.exit(1)

supported = list(sys_tags())  # ordered best match first
best = None
for a in assets:
    if not a["name"].endswith(".whl"):
        continue
    try:
        _, _, _, tags = parse_wheel_filename(a["name"])
    except Exception:
        continue
    ranks = [supported.index(t) for t in tags if t in supported]
    if ranks and (best is None or min(ranks) < best[0]):
        best = (min(ranks), a)

if not best:
    print("no pre-built wheel matches this Python/OS", file=sys.stderr)
    sys.exit(1)

asset = best[1]
os.makedirs(dest, exist_ok=True)
path = os.path.join(dest, asset["name"])
urllib.request.urlretrieve(asset["browser_download_url"], path)
print(path)
