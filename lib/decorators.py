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
            return jsonify({"error": "staff access required"}), 403

        g.token = token
        g.user_id = user["id"]
        return fn(*args, **kwargs)

    return wrapper
