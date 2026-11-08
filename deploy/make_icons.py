"""Generate the web app icons (an LED-matrix style 'scoreboard'). Run: python3 deploy/make_icons.py"""
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ledboard.gfx import Canvas  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "ledboard", "web", "static", "icons")
BG = (13, 15, 18)


def pixel_art():
    """24x24 'pixels': two team dots, a score, and a status bar, like the panel."""
    c = Canvas(24, 24)
    c.draw.ellipse((1, 3, 8, 10), fill=(255, 122, 24))       # orange "team"
    c.draw.ellipse((15, 3, 22, 10), fill=(59, 156, 255))     # blue "team"
    c.text(2, 12, "24", "4x6", (255, 255, 255))
    c.rect(11, 14, 12, 14, (120, 120, 120))
    c.text(14, 12, "17", "4x6", (255, 255, 255))
    c.rect(3, 20, 20, 20, (60, 60, 60))
    c.rect(3, 20, 13, 20, (48, 196, 108))                    # progress bar
    return c.image


def render(size, safe=1.0, rounded=True):
    """LED dots on a dark tile. `safe` < 1 shrinks the art for maskable icons."""
    art = pixel_art()
    n = art.width
    tile = Image.new("RGB", (size, size), BG)
    span = size * 0.78 * safe
    step = span / n
    ox = (size - span) / 2
    glow = Image.new("RGB", (size, size), (0, 0, 0))
    dg, dt = ImageDraw.Draw(glow), ImageDraw.Draw(tile)
    px = art.load()
    r = step * 0.42
    for y in range(n):
        for x in range(n):
            col = px[x, y]
            cx, cy = ox + (x + 0.5) * step, ox + (y + 0.5) * step
            if any(col):
                dg.ellipse((cx - r * 1.6, cy - r * 1.6, cx + r * 1.6, cy + r * 1.6), fill=tuple(v // 3 for v in col))
                dt.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)
            else:
                dt.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(28, 30, 34))
    # soft glow around lit LEDs ("screen" blend keeps the dots themselves crisp)
    tile = ImageChops.screen(tile, glow.filter(ImageFilter.GaussianBlur(step * 0.7)))
    if not rounded:
        return tile
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    render(512).save(os.path.join(OUT, "icon-512.png"))
    render(192).save(os.path.join(OUT, "icon-192.png"))
    render(512, safe=0.72, rounded=False).save(os.path.join(OUT, "icon-maskable-512.png"))  # Android adaptive
    render(180, rounded=False).save(os.path.join(OUT, "apple-touch-icon.png"))
    render(64).resize((32, 32), Image.LANCZOS).save(os.path.join(OUT, "favicon-32.png"))
    print("icons written to", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
