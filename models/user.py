import re

# Mirrors the check constraint in db/schema.sql — validate client-side
# too so bad input never reaches Postgres in the first place.
STUDENT_ID_PATTERN = re.compile(r"^52\d{8}$")


def is_valid_student_id(student_id: str) -> bool:
    return bool(STUDENT_ID_PATTERN.match(student_id or ""))


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
    }
