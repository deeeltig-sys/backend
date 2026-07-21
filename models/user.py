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


def public_user_fields(row: dict) -> dict:
    """Shape a users row for anything visible to other students —
    the (USTED) mark is derived here, not stored as its own column."""
    return {
        "id": row.get("id"),
        "full_name": row.get("full_name"),
        "avatar_url": row.get("avatar_url"),
        "standing_count": row.get("standing_count"),
        "verified": row.get("verified_at") is not None,
        "role": row.get("role"),
        "created_at": row.get("created_at"),
        "social_links": row.get("social_links") or {},
    }
