"""Fetch Pillow's C headers (Imaging.h etc.), which rpi-rgb-led-matrix's Python
bindings need at compile time but Pillow wheels don't ship.

Usage: python pillow_headers.py DEST_DIR [PILLOW_VERSION]
Prints the include directory. Uses the installed Pillow's version by default.
"""
import io
import json
import os
import sys
import tarfile
import urllib.request

dest = sys.argv[1]
ver = sys.argv[2] if len(sys.argv) > 2 else None
if not ver:
    try:
        import PIL
        ver = PIL.__version__
    except ImportError:
        ver = ""
url = f"https://pypi.org/pypi/pillow/{ver}/json" if ver else "https://pypi.org/pypi/pillow/json"
with urllib.request.urlopen(url, timeout=60) as r:
    info = json.load(r)
sdist = next(u for u in info["urls"] if u["packagetype"] == "sdist")
with urllib.request.urlopen(sdist["url"], timeout=300) as r:
    data = r.read()
inc = os.path.join(dest, "include")
os.makedirs(inc, exist_ok=True)
n = 0
with tarfile.open(fileobj=io.BytesIO(data)) as tf:
    for m in tf.getmembers():
        if m.isfile() and "/src/libImaging/" in m.name and m.name.endswith(".h"):
            with open(os.path.join(inc, os.path.basename(m.name)), "wb") as f:
                f.write(tf.extractfile(m).read())
            n += 1
if not os.path.exists(os.path.join(inc, "Imaging.h")):
    sys.exit("Imaging.h not found in Pillow sdist")
print(inc)
