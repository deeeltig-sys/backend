"""
lib/decorators.py

require_auth resolves whoever the bearer token belongs to by asking
Supabase directly (auth_get_user) — never by decoding the JWT
client-side and trusting it, since this backend doesn't hold the
signing secret to verify it safely on its own.

require_staff does the same, then checks role == 'admin' on the
users row. Both attach g.token and g.user_id so every route downstream
can just read from `g` instead of re-parsing headers.
"""

from functools import wraps

from flask import request, jsonify, g
from lib.supabase_client import auth_get_user, rest_request


def _extract_bearer_token():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header.split(" ", 1)[1].strip()


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_bearer_token()
        if not token:
            return jsonify({"error": "sign in required"}), 401

        user, status = auth_get_user(token)
        if status != 200 or not user or not user.get("id"):
            return jsonify({"error": "session expired, sign in again"}), 401

        g.token = token
        g.user_id = user["id"]
        return fn(*args, **kwargs)

    return wrapper


def optional_auth(fn):
    """Like require_auth, but never blocks the request — sets
    g.user_id/g.token if a valid bearer token is present, otherwise
    leaves them as None. For endpoints that are public but show extra
    detail (e.g. is_following) to a signed-in caller."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_bearer_token()
        g.token = None
        g.user_id = None
        if token:
            user, status = auth_get_user(token)
            if status == 200 and user and user.get("id"):
                g.token = token
                g.user_id = user["id"]
        return fn(*args, **kwargs)

    return wrapper


def require_staff(fn):
    """Matches db/schema.sql's is_staff() function exactly: role in
    ('moderator', 'admin'). Before this fix, this decorator only ever
    accepted 'admin' — a moderator account would pass every RLS check
    at the database level (is_staff() already covers them) but get a
    hard 403 from every single admin route in this Flask layer. That
    made 'moderator' a dead role: grantable in the schema, unusable in
    the app. Anything gated by @require_staff (verify queue, reports,
    stats, yawa velocity) is meant to be regular day-to-day staff work
    — safe to hand to a trusted team, not just the founder."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_bearer_token()
        if not token:
            return jsonify({"error": "sign in required"}), 401

        user, status = auth_get_user(token)
        if status != 200 or not user or not user.get("id"):
            return jsonify({"error": "session expired, sign in again"}), 401

        profile, pstatus = rest_request(
            "GET", "users", token=token,
            params={"id": f"eq.{user['id']}", "select": "role"},
        )
        if pstatus != 200 or not profile or profile[0].get("role") not in ("moderator", "admin"):
            return jsonify({"error": "staff access required"}), 403

        g.token = token
        g.user_id = user["id"]
        return fn(*args, **kwargs)

    return wrapper


def require_admin(fn):
    """Strictly role == 'admin' — deliberately NOT satisfied by
    'moderator'. Reserved for the handful of actions where a
    moderator having access would be a privilege-escalation path,
    most importantly changing anyone's role at all (a moderator who
    could promote people could promote themselves to admin). Use
    @require_staff for normal day-to-day moderation work, and this
    only for admin-of-admins actions."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_bearer_token()
        if not token:
            return jsonify({"error": "sign in required"}), 401

        user, status = auth_get_user(token)
        if status != 200 or not user or not user.get("id"):
            return jsonify({"error": "session expired, sign in again"}), 401

        profile, pstatus = rest_request(
            "GET", "users", token=token,
            params={"id": f"eq.{user['id']}", "select": "role"},
        )
        if pstatus != 200 or not profile or profile[0].get("role") != "admin":
            return jsonify({"error": "admin access required"}), 403

        g.token = token
        g.user_id = user["id"]
        return fn(*args, **kwargs)

    return wrapper


def require_owner(fn):
    """Strictly is_owner = true — not role-based at all, deliberately
    separate from require_admin/require_staff. Every team admin still
    gets a normal 403 here even though they pass @require_admin
    elsewhere; only the one user row with is_owner set (see
    db/okyeame_migration.sql — set by hand in the SQL editor, never
    through any endpoint) gets through. Used for Okyeame announcements
    and writing to CampusMEET HQ."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_bearer_token()
        if not token:
            return jsonify({"error": "sign in required"}), 401

        user, status = auth_get_user(token)
        if status != 200 or not user or not user.get("id"):
            return jsonify({"error": "session expired, sign in again"}), 401

        profile, pstatus = rest_request(
            "GET", "users", token=token,
            params={"id": f"eq.{user['id']}", "select": "is_owner"},
        )
        if pstatus != 200 or not profile or not profile[0].get("is_owner"):
            return jsonify({"error": "not authorized"}), 403

        g.token = token
        g.user_id = user["id"]
        return fn(*args, **kwargs)

    return wrapper
