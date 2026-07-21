from flask import Blueprint, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth
from models.user import public_user_fields

bp = Blueprint("follows", __name__, url_prefix="/api/users/<user_id>")


@bp.post("/follow")
@require_auth
def follow_user(user_id):
    if user_id == g.user_id:
        return jsonify({"error": "cannot follow yourself"}), 400

    data, status = rest_request(
        "POST", "follows", token=g.token,
        json_body={"follower_id": g.user_id, "followed_id": user_id},
        prefer="return=representation",
    )
    # A repeat follow hits the primary key and comes back as a conflict —
    # treat that as success rather than an error, since the end state
    # (already following) is exactly what was asked for.
    if status >= 400 and status != 409:
        return jsonify({"error": "could not follow"}), status
    return jsonify({"following": True}), 200


@bp.delete("/follow")
@require_auth
def unfollow_user(user_id):
    data, status = rest_request(
        "DELETE", "follows", token=g.token,
        params={"follower_id": f"eq.{g.user_id}", "followed_id": f"eq.{user_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not unfollow"}), status
    return jsonify({"following": False}), 200


@bp.get("/followers")
def list_followers(user_id):
    data, status = rest_request(
        "GET", "follows",
        params={
            "followed_id": f"eq.{user_id}",
            "select": "created_at,follower:users(id,full_name,avatar_url,verified_at)",
            "order": "created_at.desc",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load followers"}), status
    return jsonify([public_user_fields(row.get("follower") or {}) for row in (data or [])]), 200


@bp.get("/following")
def list_following(user_id):
    data, status = rest_request(
        "GET", "follows",
        params={
            "follower_id": f"eq.{user_id}",
            "select": "created_at,followed:users(id,full_name,avatar_url,verified_at)",
            "order": "created_at.desc",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load following"}), status
    return jsonify([public_user_fields(row.get("followed") or {}) for row in (data or [])]), 200
