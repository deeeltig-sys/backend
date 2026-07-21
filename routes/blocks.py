from flask import Blueprint, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth

bp = Blueprint("blocks", __name__, url_prefix="/api/users/<user_id>/block")


@bp.post("")
@require_auth
def block_user(user_id):
    if user_id == g.user_id:
        return jsonify({"error": "cannot block yourself"}), 400

    data, status = rest_request(
        "POST", "blocks", token=g.token,
        json_body={"blocker_id": g.user_id, "blocked_id": user_id},
        prefer="return=representation",
    )
    if status >= 400 and status != 409:
        return jsonify({"error": "could not block"}), status

    # Blocking also clears any follow relationship in either direction —
    # staying "followed" by/of someone you just blocked doesn't make
    # sense and would keep surfacing them in follower/following lists.
    rest_request(
        "DELETE", "follows", token=g.token,
        params={"follower_id": f"eq.{g.user_id}", "followed_id": f"eq.{user_id}"},
    )
    rest_request(
        "DELETE", "follows", token=g.token,
        params={"follower_id": f"eq.{user_id}", "followed_id": f"eq.{g.user_id}"},
    )
    return jsonify({"blocked": True}), 200


@bp.delete("")
@require_auth
def unblock_user(user_id):
    data, status = rest_request(
        "DELETE", "blocks", token=g.token,
        params={"blocker_id": f"eq.{g.user_id}", "blocked_id": f"eq.{user_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not unblock"}), status
    return jsonify({"blocked": False}), 200
