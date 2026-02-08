from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageOps


def _trim_uniform_border(img: Image.Image) -> Image.Image:
    """Trim borders that match the top-left pixel color."""
    bg = Image.new(img.mode, img.size, img.getpixel((0, 0)))
    diff = ImageChops.difference(img, bg)
    if diff.mode != "L":
        diff = diff.convert("L")
    bbox = diff.getbbox()
    return img.crop(bbox) if bbox else img


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    src = root / "logo" / "logoganahoy.png"
    if not src.exists():
        raise SystemExit(f"Logo not found at: {src}")

    out_dir = root / "static" / "dist" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)

    img = Image.open(src)
    img = ImageOps.exif_transpose(img)

    # Our source is a full horizontal logo. For favicon we want the left "ticket" icon.
    w, h = img.size
    icon_crop = img.crop((0, 0, int(w * 0.36), h))
    icon_crop = _trim_uniform_border(icon_crop)

    # Make square canvas (keep transparency if any).
    icon_rgba = icon_crop.convert("RGBA")
    size = max(icon_rgba.size)
    sq = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - icon_rgba.size[0]) // 2
    y = (size - icon_rgba.size[1]) // 2
    sq.paste(icon_rgba, (x, y))

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

