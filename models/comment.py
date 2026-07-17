# Mirrors the check constraint on comments.content in db/schema.sql
# (char_length between 1 and 1000) — validate client-side and
# server-side too, same reasoning as models/post.py.
MAX_COMMENT_LENGTH = 1000
MIN_COMMENT_LENGTH = 1


def validate_comment_content(content: str) -> bool:
    if not content:
        return False
    return MIN_COMMENT_LENGTH <= len(content) <= MAX_COMMENT_LENGTH
