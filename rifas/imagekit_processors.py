from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops


@dataclass(frozen=True)
class AutoTrim:
    """
    Trim uniform borders (commonly white background margins) before resizing.

    This helps "product images" that come with large whitespace so the subject
    fills the carousel frame better.
    """

    tolerance: int = 12  # 0-255, higher trims more aggressively

    def process(self, img: Image.Image) -> Image.Image:
        if img is None:
            return img

        # Normalize orientation / mode
        work = img.convert("RGBA")

        # Composite on white to treat transparency as background.
        bg = Image.new("RGBA", work.size, (255, 255, 255, 255))
        work = Image.alpha_composite(bg, work).convert("RGB")

        # Use the top-left pixel as background reference (common for product images).
        bg_color = work.getpixel((0, 0))
        bg_img = Image.new("RGB", work.size, bg_color)
        diff = ImageChops.difference(work, bg_img)

        # Convert to mask and apply tolerance.
        mask = diff.convert("L").point(lambda p: 255 if p > self.tolerance else 0)
        bbox = mask.getbbox()
        if not bbox:
            return img

        trimmed = work.crop(bbox)
        return trimmed

