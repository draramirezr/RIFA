from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps


def _trim_uniform_border(img: Image.Image) -> Image.Image:
    """Trim borders that match the top-left pixel color."""
    bg = Image.new(img.mode, img.size, img.getpixel((0, 0)))
    diff = ImageChops.difference(img, bg)
    if diff.mode != "L":
        diff = diff.convert("L")
    bbox = diff.getbbox()
    return img.crop(bbox) if bbox else img


def _rounded_rect_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return m


def _make_ticket_icon(size: int = 512) -> Image.Image:
    """
    Create a clean favicon-friendly ticket icon.
    - Emerald rounded-square background
    - White ticket outline with perforation dots
    """
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(bg)

    # Brand emerald from buttons (#10B981)
    emerald = (16, 185, 129, 255)
    radius = int(size * 0.22)
    d.rounded_rectangle((0, 0, size, size), radius=radius, fill=emerald)

    # Ticket geometry
    pad = int(size * 0.18)
    x0, y0 = pad, int(size * 0.30)
    x1, y1 = size - pad, int(size * 0.70)
    w = x1 - x0
    h = y1 - y0
    r = int(min(w, h) * 0.18)

    # Ticket "notches" (semi-circles) on left and right
    notch_r = int(h * 0.18)
    notch_y = (y0 + y1) // 2

    # Draw outline path by drawing rounded rectangle then cutting notches via background color,
    # then re-drawing outline.
    outline = (255, 255, 255, 255)
    stroke = max(6, int(size * 0.045))

    # Base ticket shape (filled transparent, outline only)
    # We'll draw outline by drawing the rounded rectangle twice (stroke simulation).
    d.rounded_rectangle((x0, y0, x1, y1), radius=r, outline=outline, width=stroke)

    # Notches: paint emerald circles over outline then redraw outline to clean edges.
    d.ellipse((x0 - notch_r, notch_y - notch_r, x0 + notch_r, notch_y + notch_r), fill=emerald)
    d.ellipse((x1 - notch_r, notch_y - notch_r, x1 + notch_r, notch_y + notch_r), fill=emerald)
    d.rounded_rectangle((x0, y0, x1, y1), radius=r, outline=outline, width=stroke)

    # Perforation dots on the left side
    dot_r = max(2, int(size * 0.012))
    dots_x = x0 + int(w * 0.12)
    for i in range(5):
        dy = int((h * (i + 1)) / 6)
        cy = y0 + dy
        d.ellipse((dots_x - dot_r, cy - dot_r, dots_x + dot_r, cy + dot_r), fill=outline)

    # Inner line
    line_y = (y0 + y1) // 2
    d.line(
        (x0 + int(w * 0.28), line_y, x1 - int(w * 0.12), line_y),
        fill=outline,
        width=max(4, int(stroke * 0.55)),
    )

    # Apply rounded-square mask to ensure clean edges
    mask = _rounded_rect_mask(size, radius)
    bg.putalpha(mask)
    return bg


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "static" / "dist" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)

    # New favicon: clean ticket icon (better at 16x16)
    sq = _make_ticket_icon(512)

    # PNG sizes
    sizes = [16, 32, 180, 192, 512]
    for s in sizes:
        resized = sq.resize((s, s), Image.LANCZOS)
        if s == 180:
            name = "apple-touch-icon.png"
        elif s == 192:
            name = "android-chrome-192x192.png"
        elif s == 512:
            name = "android-chrome-512x512.png"
        else:
            name = f"favicon-{s}x{s}.png"
        resized.save(out_dir / name, format="PNG", optimize=True)

    # ICO (multi-size)
    ico_sizes = [(16, 16), (32, 32), (48, 48)]
    ico_imgs = [sq.resize(s, Image.LANCZOS).convert("RGBA") for s in ico_sizes]
    ico_imgs[0].save(out_dir / "favicon.ico", format="ICO", sizes=ico_sizes)

    # Minimal web manifest
    manifest = {
        "name": "GanaHoyRD",
        "short_name": "GanaHoyRD",
        "icons": [
            {
                "src": "/static/dist/icons/android-chrome-192x192.png",
                "sizes": "192x192",
                "type": "image/png",
            },
            {
                "src": "/static/dist/icons/android-chrome-512x512.png",
                "sizes": "512x512",
                "type": "image/png",
            },
        ],
        "theme_color": "#020617",
        "background_color": "#020617",
        "display": "standalone",
    }
    (out_dir / "site.webmanifest").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Wrote favicon assets to: {out_dir}")


if __name__ == "__main__":
    main()

