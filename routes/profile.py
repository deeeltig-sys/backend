from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth

bp = Blueprint("profile", __name__, url_prefix="/api/profile")


@bp.get("/<user_id>")
def get_profile(user_id):
    data, status = rest_request("GET", "users", params={"id": f"eq.{user_id}", "select": "*"})
    if status != 200 or not data:
        return jsonify({"error": "user not found"}), 404
    return jsonify(data[0]), 200


@bp.patch("/me")
@require_auth
def update_own_profile():
    body = request.get_json(silent=True) or {}
    allowed_fields = {"full_name", "avatar_url"}
    updates = {k: v for k, v in body.items() if k in allowed_fields}
    if not updates:
        return jsonify({"error": "nothing to update"}), 400

    data, status = rest_request(
        "PATCH", "users", token=g.token,
        params={"id": f"eq.{g.user_id}"}, json_body=updates, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "update failed"}), status
    return jsonify(data[0] if data else {}), 200
