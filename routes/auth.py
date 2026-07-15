from flask import Blueprint, request, jsonify, g
from lib.supabase_client import auth_signup, auth_login, auth_refresh, rest_request
from lib.decorators import require_auth
from models.user import is_valid_student_id

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@bp.post("/signup")
def signup():
    """Open signup — no OTP, no bottleneck. Account is fully usable the
    moment this returns. verified_at starts null; that's a separate,
    manual admin action (see routes/admin.py)."""
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    full_name = (body.get("full_name") or "").strip()
    student_id_number = (body.get("student_id_number") or "").strip()

    if not email or "@" not in email:
        return jsonify({"error": "a valid email is required"}), 400
    if len(password) < 8:
        return jsonify({"error": "password must be at least 8 characters"}), 400
    if not full_name:
        return jsonify({"error": "full name is required"}), 400
    if not is_valid_student_id(student_id_number):
        return jsonify({"error": "student ID must be 10 digits starting with 52"}), 400

    data, status = auth_signup(email, password, full_name, student_id_number)

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
        params={"id": f"eq.{g.user_id}", "select": "*"},
    )
    if status != 200 or not data:
        return jsonify({"error": "profile not found"}), 404
    return jsonify(data[0]), 200
