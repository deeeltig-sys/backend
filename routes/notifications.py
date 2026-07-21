from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth

bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")


def _flatten_actor(row: dict) -> dict:
    actor = row.pop("actor", None) or {}
    row["actor_full_name"] = actor.get("full_name")
    row["actor_avatar_url"] = actor.get("avatar_url")
    return row


@bp.get("")
@require_auth
def list_notifications():
    limit = request.args.get("limit", 30)
    data, status = rest_request(
        "GET", "notifications", token=g.token,
        params={
            "user_id": f"eq.{g.user_id}",
            "select": "id,type,target_type,target_id,actor_id,read,created_at,actor:users(full_name,avatar_url)",
            "order": "created_at.desc",
            "limit": limit,
        },
    )
    if status != 200:
        return jsonify({"error": "could not load notifications"}), status
    return jsonify([_flatten_actor(row) for row in (data or [])]), 200


@bp.get("/unread-count")
@require_auth
def unread_count():
    data, status = rest_request(
        "GET", "notifications", token=g.token,
        params={"user_id": f"eq.{g.user_id}", "read": "eq.false", "select": "id"},
    )
    if status != 200:
        return jsonify({"error": "could not load count"}), status
    return jsonify({"count": len(data or [])}), 200


@bp.patch("/<notification_id>/read")
@require_auth
def mark_read(notification_id):
    data, status = rest_request(
        "PATCH", "notifications", token=g.token,
        params={"id": f"eq.{notification_id}", "user_id": f"eq.{g.user_id}"},
        json_body={"read": True},
    )
    if status >= 400:
        return jsonify({"error": "could not update"}), status
    return jsonify({"ok": True}), 200


@bp.post("/read-all")
@require_auth
def mark_all_read():
    data, status = rest_request(
        "PATCH", "notifications", token=g.token,
        params={"user_id": f"eq.{g.user_id}", "read": "eq.false"},
        json_body={"read": True},
    )
    if status >= 400:
        return jsonify({"error": "could not update"}), status
    return jsonify({"ok": True}), 200
