MAX_POST_LENGTH = 2000
MIN_POST_LENGTH = 1


def validate_post_content(content: str) -> bool:
    if not content:
        return False
    return MIN_POST_LENGTH <= len(content) <= MAX_POST_LENGTH


def public_post_fields(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "author_id": row.get("author_id"),
        "content": row.get("content"),
        "image_url": row.get("image_url"),
        "view_count": row.get("view_count"),
        "reaction_count": row.get("reaction_count"),
        "search_hit_count": row.get("search_hit_count"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
    }
