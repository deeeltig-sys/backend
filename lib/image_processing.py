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

from PIL import Image, ImageOps, UnidentifiedImageError

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    # If the plugin somehow isn't installed, every other format still
    # works — HEIC/HEIF just won't decode, and that surfaces as the
    # same "could not read this image" error as any other bad file,
    # not a crash.
    pass

# A feed card never renders wider than a few hundred px on screen, but
# a straight-off-the-phone photo is routinely 3000-4000px wide. Before
# this cap, every upload was stored and served at its original
# resolution — every viewer's feed request downloaded that full-size
# original, which is what was driving the multi-megabyte, slow feed
# loads. 1600px is comfortably above what any card, lightbox, or
# retina display on this platform actually needs; nothing downstream
# renders an image anywhere near that large. This is the default for
# post/status images — avatars pass their own much smaller cap (see
# AVATAR_MAX_DIMENSION below), since a circular avatar never renders
# above 88px anywhere in this app.
MAX_DIMENSION = 1600

# The largest an avatar is ever actually displayed at is 88px (Profile
# page header) — everywhere else it's 72px or smaller. 400px gives
# generous headroom for retina/high-DPI displays (4.5x the largest
# real usage) while still being roughly 16x smaller in area than
# letting avatars through the same 1600px cap as full feed photos.
# Avatars are viewed constantly (every post, comment, message thread),
# so this was a quiet, compounding source of wasted storage and
# egress before this existed — every avatar was being stored and
# served at up to 1600px for something never shown above 88px.
AVATAR_MAX_DIMENSION = 400


def _resize_if_needed(image: Image.Image, max_dimension: int = MAX_DIMENSION) -> Image.Image:
    """Downscales in place if either dimension exceeds max_dimension,
    preserving aspect ratio. Never upscales a smaller image — a
    300x300 avatar stays 300x300, this only ever makes large images
    smaller."""
    if image.width <= max_dimension and image.height <= max_dimension:
        return image
    image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    return image


class UnsupportedImageError(ValueError):
    """Raised when Pillow genuinely cannot decode the uploaded bytes —
    a corrupt file, a non-image file with an image-like name, or a
    format not covered by Pillow + pillow-heif."""


def normalize_image(file_bytes: bytes, max_dimension: int = MAX_DIMENSION) -> tuple[bytes, str, str]:
    """Returns (normalized_bytes, content_type, extension).

    `max_dimension` lets callers that only ever display an image small
    (avatars, group avatars) opt into a much tighter cap than the
    default — pass AVATAR_MAX_DIMENSION for those. Post/status images
    keep the default 1600px since they're shown at full feed width.

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

    # Phones write rotation as an EXIF tag rather than actually
    # rotating the pixel data — ignoring it is why a portrait photo
    # can come out sideways once re-encoded. exif_transpose bakes the
    # rotation into the pixels themselves and strips the tag, since
    # nothing downstream (watermarking, thumbnails, <img> rendering)
    # reads EXIF orientation on its own.
    image = ImageOps.exif_transpose(image)

    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )

    out = io.BytesIO()
    if has_alpha:
        image = image.convert("RGBA")
        image = _resize_if_needed(image, max_dimension)
        image.save(out, format="PNG", optimize=True)
        return out.getvalue(), "image/png", "png"

    image = image.convert("RGB")
    image = _resize_if_needed(image, max_dimension)
    image.save(out, format="JPEG", quality=90, optimize=True)
    return out.getvalue(), "image/jpeg", "jpg"

