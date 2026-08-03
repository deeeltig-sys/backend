"""Normalizes any uploaded image to one of two canonical formats.

The old approach whitelisted a handful of MIME types (JPEG/PNG/WEBP)
and rejected everything else — which meant a perfectly valid photo
straight off an iPhone (HEIC) or a GIF someone wanted to post as a
static image would just fail with "unsupported format," even though
the bytes were a completely legitimate image.

This instead tries to actually *decode* the file with Pillow — the
real test of "is this a usable image" — regardless of what the
browser claimed its Content-Type was (browsers are often wrong or
vague about less common formats). Once decoded, it's always
re-encoded to JPEG (or PNG, if the source had real transparency)
before it ever reaches storage. That means every other piece of code
downstream (watermarking, the storage path's file extension, whatever
reads the URL back later) only ever has to deal with two possible
formats, no matter what a hundred different phones and screenshot
tools actually sent in.
"""

import io

from PIL import Image, UnidentifiedImageError

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    # If the plugin somehow isn't installed, every other format still
    # works — HEIC/HEIF just won't decode, and that surfaces as the
    # same "could not read this image" error as any other bad file,
    # not a crash.
    pass


class UnsupportedImageError(ValueError):
    """Raised when Pillow genuinely cannot decode the uploaded bytes —
    a corrupt file, a non-image file with an image-like name, or a
    format not covered by Pillow + pillow-heif."""


def normalize_image(file_bytes: bytes) -> tuple[bytes, str, str]:
    """Returns (normalized_bytes, content_type, extension).

    Animated formats (GIF, animated WEBP) are flattened to their first
    frame — this platform shows posts/statuses/avatars as static
    images, so an animated upload becoming a still is the same
    deliberate trade-off Facebook/Instagram make for non-Story image
    posts, not an oversight.
    """
    try:
        image = Image.open(io.BytesIO(file_bytes))
        image.load()  # forces full decode now, not lazily on first use —
        # a truncated/corrupt file needs to fail here, not somewhere
        # deep in watermarking or storage upload.
    except (UnidentifiedImageError, OSError) as exc:
        raise UnsupportedImageError("could not read this image — try a different photo") from exc

    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )

    out = io.BytesIO()
    if has_alpha:
        image = image.convert("RGBA")
        image.save(out, format="PNG")
        return out.getvalue(), "image/png", "png"

    image = image.convert("RGB")
    image.save(out, format="JPEG", quality=90)
    return out.getvalue(), "image/jpeg", "jpg"
