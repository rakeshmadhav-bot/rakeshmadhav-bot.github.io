"""Generate the icon, favicon and social-card images from the HomeoAI mark.

Run from the repo root:  python3 scripts/make_brand_assets.py
The mark is the dark-green rounded square holding a white teardrop with a
branching node glyph, drawn here with Pillow so the PNG/ICO files stay in sync.
"""
import math
import os
from PIL import Image, ImageDraw

OUT = "assets"
INK = (21, 59, 47)        # #153B2F
PAPER = (247, 245, 239)   # #F7F5EF
WHITE = (251, 249, 243)


def mark(size, bg=INK, drop=WHITE, glyph=INK, radius_ratio=0.18, pad=0.0):
    """Render the mark on a transparent canvas of `size` px."""
    ss = 4  # supersample for smooth edges
    w = int(round(size * (1 - 2 * pad)))
    img = Image.new("RGBA", (w * ss, w * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w * ss - 1, w * ss - 1], radius=int(w * ss * radius_ratio), fill=bg)

    cx, cy = w * ss / 2, w * ss * 0.60
    r = w * ss * 0.235
    apex_y = cy - 1.78 * r
    dist = cy - apex_y
    ang = math.acos(r / dist)
    tx = r * math.sin(ang)
    ty = r * math.cos(ang)
    # teardrop = circle + triangle from the apex to both tangent points
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=drop)
    d.polygon([(cx, apex_y), (cx - tx, cy - ty * 0 + ty), (cx + tx, cy - ty * 0 + ty)], fill=drop)

    lw = max(1, int(w * ss * 0.022))
    top = (cx, cy - r * 0.92)
    bottom = (cx, cy + r * 0.55)
    join = (cx, cy - r * 0.08)
    left = (cx - r * 0.42, cy + r * 0.30)
    right = (cx + r * 0.42, cy + r * 0.30)
    for a, b in ((top, bottom), (join, left), (join, right)):
        d.line([a, b], fill=glyph, width=lw)
    dot = w * ss * 0.030
    for p in (top, bottom, left, right):
        d.ellipse([p[0] - dot, p[1] - dot, p[0] + dot, p[1] + dot], fill=glyph)

    img = img.resize((w, w), Image.LANCZOS)
    if pad:
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        off = int(round(size * pad))
        canvas.paste(img, (off, off), img)
        return canvas
    return img


def social_card(path, width=1200, height=630):
    """A quiet paper card with the mark centred — used for link previews."""
    img = Image.new("RGB", (width, height), PAPER)
    d = ImageDraw.Draw(img)
    band = int(height * 0.012)
    d.rectangle([0, 0, width, band], fill=INK)
    d.rectangle([0, height - band, width, height], fill=INK)
    m = mark(int(height * 0.42))
    img.paste(m, ((width - m.width) // 2, (height - m.height) // 2), m)
    img.save(path, optimize=True)
    return os.path.getsize(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    for size, name in ((192, "icon-192.png"), (512, "icon-512.png"), (180, "apple-touch-icon.png")):
        mark(size).save(os.path.join(OUT, name), optimize=True)
    # Maskable icon: same mark, padded into the safe zone.
    mark(512, pad=0.12).save(os.path.join(OUT, "icon-maskable-512.png"), optimize=True)
    ico = mark(64)
    ico.save(os.path.join(OUT, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print("social card bytes:", social_card(os.path.join(OUT, "og-card.png")))
    for f in sorted(os.listdir(OUT)):
        print(f, os.path.getsize(os.path.join(OUT, f)))


if __name__ == "__main__":
    main()
