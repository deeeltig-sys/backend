"""
lib/pagination.py

A handful of GET routes were reading `limit`/`offset` straight off
`request.args` and handing them to PostgREST untouched — no cast, no
cap. Two real consequences of that: a caller can request an
arbitrarily large `limit` and force a heavy query through with no
ceiling, and a non-numeric value (typo, stale bookmark, bot) sails
through Flask and PostgREST throws its own raw error back at the
client instead of a clean 400 from this backend.

Other routes in this codebase already did this correctly, just each
with its own inline `min(int(...), N)` — this is that logic pulled
into one place so every route enforces it the same way instead of
however each one happened to be written.
"""

from flask import request


def paginate_args(default_limit: int = 30, max_limit: int = 60, default_offset: int = 0):
    """Reads `limit`/`offset` from the current request's query string,
    validated and capped. Returns (limit, offset) as ints. Invalid
    (non-numeric, negative) values fall back to the defaults rather
    than raising, so a bad query param degrades to "default page" for
    the caller instead of a raw 400 from downstream."""
    try:
        limit = int(request.args.get("limit", default_limit))
    except (TypeError, ValueError):
        limit = default_limit
    try:
        offset = int(request.args.get("offset", default_offset))
    except (TypeError, ValueError):
        offset = default_offset

    limit = min(max(limit, 1), max_limit)
    offset = max(offset, 0)
    return limit, offset
