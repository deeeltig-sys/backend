from functools import wraps
from flask import request, jsonify, g
from lib.supabase_client import auth_get_user, rest_request


def _extract_token():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header.split(" ", 1)[1]


def require_auth(f):
    """Any signed-up student — open signup, so this just means
    'has a valid session,' not 'is verified.'"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return jsonify({"error": "missing or invalid Authorization header"}), 401
        user, status = auth_get_user(token)
        if status != 200 or not user or "id" not in user:
            return jsonify({"error": "invalid or expired session"}), 401
        g.token = token
        g.user_id = user["id"]
        g.user_email = user.get("email")
        return f(*args, **kwargs)
    return wrapper


def require_staff(f):
    """Moderator/admin only — backs up the RPC-level is_staff() check
    with an early, friendlier 403 instead of letting a student hit the
    RPC and get a raw Postgres exception back."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return jsonify({"error": "missing or invalid Authorization header"}), 401
        user, status = auth_get_user(token)
        if status != 200 or not user or "id" not in user:
            return jsonify({"error": "invalid or expired session"}), 401

        profile, pstatus = rest_request(
            "GET", "users", token=token,
            params={"id": f"eq.{user['id']}", "select": "role"},
        )
        if pstatus != 200 or not profile or profile[0].get("role") not in ("moderator", "admin"):
            return jsonify({"error": "staff access required"}), 403

        g.token = token
        g.user_id = user["id"]
        return f(*args, **kwargs)
    return wrapper
