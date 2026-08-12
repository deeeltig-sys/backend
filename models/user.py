import re

# Matches PLATFORM_URL_TEMPLATES on the frontend (Profile.jsx) exactly —
# any key outside this set is silently dropped, not just for new/unknown
# platforms but as the actual security boundary: these are the only
# platforms that get templated into an outbound link, so nothing outside
# this set should ever be persisted.
SOCIAL_PLATFORMS = {
    "facebook", "instagram", "whatsapp", "snapchat", "tiktok",
    "x", "linkedin", "telegram", "youtube", "threads", "discord",
}
MAX_HANDLE_LENGTH = 100
MAX_BIO_LENGTH = 280  # matches the users_bio_length check constraint in the DB
# Handles are templated into a fixed URL server-side (e.g.
# https://instagram.com/{handle}) — a handle should never itself contain
# a scheme, slash, backslash, quote, or whitespace. This is a defensive
# check, not the only one: it stops someone from turning their own
# "Instagram link" into a link to somewhere else entirely.
_INVALID_HANDLE_CHARS = re.compile(r"[\s/\\\"'<>]")


def sanitize_social_links(raw) -> dict:
    """Validates a social_links payload down to just the known platforms
    with sane handle values. Silently drops unknown platform keys and
    empty/invalid handles rather than erroring the whole request — a
    stray key from an older client build shouldn't block an otherwise
    legit save."""
    if not isinstance(raw, dict):
        return {}

    cleaned = {}
    for platform, handle in raw.items():
        if platform not in SOCIAL_PLATFORMS:
            continue
        if not isinstance(handle, str):
            continue
        handle = handle.strip()
        if not handle or len(handle) > MAX_HANDLE_LENGTH:
            continue
        if handle.lower().startswith(("http://", "https://")):
            continue
        if _INVALID_HANDLE_CHARS.search(handle):
            continue
        cleaned[platform] = handle
    return cleaned


def sanitize_bio(raw) -> str | None:
    """Trims and caps a bio string. Empty string is normalized to None
    so 'clear my bio' round-trips as null rather than an empty string
    sitting in the DB forever."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    return trimmed[:MAX_BIO_LENGTH]


MAX_FULL_NAME_LENGTH = 80
MIN_FULL_NAME_LENGTH = 3  # rejects bare initials like "J." or "AB"


def sanitize_full_name(raw) -> str | None:
    """Trims and caps a display name, and rejects names that aren't
    actually names. Returns None for anything invalid — the caller
    (routes/profile.py) treats None as a rejection and returns a 400
    with a clear reason, rather than silently accepting or clearing
    the name, since a user must always have *some* real name or
    nickname on the platform.

    What's rejected and why:
      - shorter than MIN_FULL_NAME_LENGTH after trimming — blocks bare
        initials ("J.", "AB") that make a profile impossible to
        recognize in a feed or friends list
      - entirely digits, or containing no letters at all — blocks
        someone setting their name to "12345" or similar
    Nicknames are explicitly still allowed — "Makaveli", "Bless", a
    single word is fine. This only filters out things that aren't
    names at all, not legal-name-only enforcement."""
    if raw is None or not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    if len(trimmed) < MIN_FULL_NAME_LENGTH:
        return None
    if not any(ch.isalpha() for ch in trimmed):
        return None
    return trimmed[:MAX_FULL_NAME_LENGTH]


MAX_LEVEL_LENGTH = 40  # matches the users_level_of_study_length check constraint


def sanitize_level_of_study(raw) -> str | None:
    if raw is None or not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    return trimmed[:MAX_LEVEL_LENGTH] if trimmed else None


def public_user_fields(row: dict) -> dict:
    """Shape a users row for anything visible to other students —
    the (USTED) mark is derived here, not stored as its own column.

    follower_count was missing here entirely — every surface that
    routes another person's data through this shaper (people-to-follow
    carousel, search, someone else's profile, friend lists) silently
    hardcoded 0 followers for everyone but yourself, since /api/auth/me
    is the one place that returns a raw, unshaped row instead of going
    through this function. The column was always real (it's what
    ORDER BY follower_count.desc already sorts /suggested by) — it
    just never made it into the response body."""
    return {
        "id": row.get("id"),
        "full_name": row.get("full_name"),
        "avatar_url": row.get("avatar_url"),
        "standing_count": row.get("standing_count"),
        "follower_count": row.get("follower_count") or 0,
        "verified": row.get("verified_at") is not None,
        "role": row.get("role"),
        "created_at": row.get("created_at"),
        "social_links": row.get("social_links") or {},
        "bio": row.get("bio"),
        "level_of_study": row.get("level_of_study"),
    }
