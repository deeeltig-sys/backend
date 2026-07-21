from flask import Blueprint, request, jsonify, g
from lib.supabase_client import (
    auth_signup, auth_login, auth_refresh, auth_recover,
    auth_update_password, auth_delete_self, rest_request,
)
from lib.decorators import require_auth

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@bp.post("/signup")
def signup():
    """Open signup — no OTP, no bottleneck. Account is fully usable the
    moment this returns. verified_at starts null; that's a separate,
    manual admin action (see routes/admin.py).

    University is now the required identifying field instead of a
    student ID number — either an existing university_id (picked from
    the dropdown) or a university_name (the "Other" free-text path,
    which the signup trigger resolves/creates via
    get_or_create_university())."""
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    full_name = (body.get("full_name") or "").strip()
    university_id = (body.get("university_id") or "").strip() or None
    university_name = (body.get("university_name") or "").strip() or None

    if not email or "@" not in email:
        return jsonify({"error": "a valid email is required"}), 400
    if len(password) < 8:
        return jsonify({"error": "password must be at least 8 characters"}), 400
    if not full_name:
        return jsonify({"error": "full name is required"}), 400
    if not university_id and not university_name:
        return jsonify({"error": "university is required"}), 400

    data, status = auth_signup(email, password, full_name, university_id, university_name)

    if status >= 400:
        msg = (data or {}).get("msg") or (data or {}).get("error_description") or "signup failed"
        return jsonify({"error": msg}), status

    return jsonify(data), 201


@bp.post("/login")
def login():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400

    data, status = auth_login(email, password)
    if status >= 400:
        return jsonify({"error": "invalid email or password"}), 401
    return jsonify(data), 200


@bp.post("/refresh")
def refresh():
    """Exchanges a refresh token for a new session — this is what lets
    the frontend keep a student signed in indefinitely (until they
    explicitly log out) instead of the access token's ~1hr expiry
    silently kicking them back to the login screen."""
    body = request.get_json(silent=True) or {}
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        return jsonify({"error": "refresh_token is required"}), 400

    data, status = auth_refresh(refresh_token)
    if status >= 400:
        return jsonify({"error": "session could not be renewed, sign in again"}), 401
    return jsonify(data), 200


@bp.get("/me")
@require_auth
def me():
    data, status = rest_request(
        "GET", "users", token=g.token,
        params={"id": f"eq.{g.user_id}", "select": "*,university:universities(name)"},
    )
    if status != 200 or not data:
        return jsonify({"error": "profile not found"}), 404

    row = data[0]
    row["university_name"] = (row.pop("university", None) or {}).get("name")
    return jsonify(row), 200


@bp.post("/forgot-password")
def forgot_password():
    """Triggers the recovery email. Always returns success even if the
    email doesn't match an account — confirming or denying an email's
    existence here is a real (if minor) way to let someone probe
    which emails are registered."""
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "a valid email is required"}), 400

    auth_recover(email)
    return jsonify({"message": "if that email is registered, a reset link has been sent"}), 200


@bp.post("/reset-password")
def reset_password():
    """Second half of the recovery flow — takes the short-lived token
    from the emailed link (NOT a normal session token) plus a new
    password. The page that captures that token from the link and
    calls this isn't part of the mobile app itself; see the deploy
    notes for where that lives."""
    body = request.get_json(silent=True) or {}
    recovery_token = body.get("access_token")
    new_password = body.get("new_password") or ""

    if not recovery_token:
        return jsonify({"error": "missing recovery token"}), 400
    if len(new_password) < 8:
        return jsonify({"error": "password must be at least 8 characters"}), 400

    data, status = auth_update_password(recovery_token, new_password)
    if status >= 400:
        return jsonify({"error": "could not reset password — the link may have expired"}), status
    return jsonify({"message": "password updated"}), 200


@bp.delete("/me")
@require_auth
def delete_account():
    """Anonymizes the profile first, unconditionally — full_name,
    avatar, and social_links are wiped regardless of whether the
    harder deletion step below succeeds, so the practical privacy
    goal (no personal data visible) is met either way.

    Then attempts a real GoTrue self-delete. If the project doesn't
    have 'allow self-delete' enabled, that call fails and the account
    is left anonymized-but-present rather than the request erroring
    out — worth confirming that setting in the Supabase dashboard so
    this behaves as a real deletion end to end. If it succeeds,
    posts/comments/reactions/follows all cascade-delete automatically
    via the existing foreign key constraints — this is NOT a
    Reddit-style 'keep the posts, blank the author' deletion, it's a
    full removal."""
    rest_request(
        "PATCH", "users", token=g.token,
        params={"id": f"eq.{g.user_id}"},
        json_body={
            "full_name": "Deleted User",
            "avatar_url": None,
            "social_links": {},
            "status": "deactivated",
        },
    )

    data, status = auth_delete_self(g.token)
    if status >= 400:
        return jsonify({
            "message": "your profile has been cleared, but full account deletion needs a setting "
                       "enabled on the backend — contact support to finish this",
        }), 200

    return jsonify({"message": "account deleted"}), 200
