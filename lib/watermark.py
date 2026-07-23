"""Stamps a small, semi-transparent 'CampusMEET' mark into the bottom-right
corner of post images before they hit storage. This doesn't stop someone
determined from removing it (crop, screenshot-and-crop, etc.) — nothing
server-side can guarantee that — but it means a screenshot or casual
re-save that gets reposted elsewhere still carries a visible source tag.

Paired with the frontend's right-click/long-press-save blocking; this is
the half of image protection that survives even if that JS is bypassed.
"""

import io
from PIL import Image, ImageDraw, ImageFont

WATERMARK_TEXT = "CampusMEET"
# Mark scales with the image so it isn't a tiny illegible speck on a
# huge photo or an oversized blob on a small one.
MARK_FONT_RATIO = 0.032   # font size as a fraction of image width
MARK_MARGIN_RATIO = 0.02  # padding from the corner as a fraction of width
MARK_OPACITY = 130        # 0-255; low enough to stay unobtrusive


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    # DejaVuSans-Bold ships with Pillow's own bundled fonts, so this works
    # the same on Render's container as it does locally — no system font
    # dependency to worry about.
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default()


def apply_watermark(file_bytes: bytes, content_type: str) -> bytes:
    """Returns watermarked image bytes in the same format as the input.
    On any failure (corrupt image, unsupported edge case, etc.) returns
    the original bytes unchanged — a watermarking bug should never be
    the reason someone's post upload fails."""
    try:
        image = Image.open(io.BytesIO(file_bytes))
        image = image.convert("RGBA")

        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        font_size = max(12, int(image.width * MARK_FONT_RATIO))
        font = _load_font(font_size)
        margin = max(6, int(image.width * MARK_MARGIN_RATIO))

        bbox = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = image.width - text_w - margin
        y = image.height - text_h - margin

        # Soft dark backing behind the text so it stays legible on both
        # light and dark photo backgrounds, not just dark ones.
        pad = max(4, font_size // 4)
        draw.rounded_rectangle(
            [x - pad, y - pad, x + text_w + pad, y + text_h + pad],
            radius=pad, fill=(0, 0, 0, int(MARK_OPACITY * 0.55)),
        )
        draw.text((x, y), WATERMARK_TEXT, font=font, fill=(255, 255, 255, MARK_OPACITY))

        watermarked = Image.alpha_composite(image, overlay)

        out = io.BytesIO()
        if content_type == "image/png":
            watermarked.save(out, format="PNG")
        elif content_type == "image/webp":
            watermarked.convert("RGB").save(out, format="WEBP", quality=90)
        else:  # image/jpeg — no alpha channel support
            watermarked.convert("RGB").save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception:
        return file_bytes
