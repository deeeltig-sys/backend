from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth
from lib.limiter import limiter

bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")

# Same reasoning as routes/messages.py's identical exemption: lib/limiter.py's
# default_limits=["200 per hour"] was sized for auth.py's abuse-prevention
# routes, not for this blueprint. BottomNav.jsx polls GET /unread-count every
# 30s (120/hr baseline) AND fires it again on every notifications-read event
# — a genuinely active session opening/clearing Alerts repeatedly can burn
# through the remaining headroom and hit the exact same 429 wall the chat
# polling did, just on the Alerts bell instead. No route here needs a tight
# cap the way auth's signup/login do — mark_read/mark_all_read are ordinary
# user-click actions, not something worth throttling.
limiter.exempt(bp)


def _flatten_actor(row: dict) -> dict:
    actor = row.pop("actor", None) or {}
    row["actor_full_name"] = actor.get("full_name")
    row["actor_avatar_url"] = actor.get("avatar_url")
    return row


# Grouping like this only makes sense for "pile-on" activity where many
# people can do the same thing to the same target — likes and comments.
# follow/message/friend events stay one-row-per-event since each one is
# its own relationship, not a repeatable reaction to a single target.
_GROUPABLE_TYPES = {"reaction", "comment", "comment_reply"}


def _group_notifications(rows: list[dict]) -> list[dict]:
    """Collapses consecutive notifications that share (type, target_id)
    into one card carrying the latest actor plus a count of the rest —
    "Kwame and 12 others liked your post" instead of 13 separate rows.
    Rows already arrive newest-first, so a single linear pass groups
    anything that piled up close together in time without re-sorting."""
    grouped = []
    index_by_key = {}
    for row in rows:
        key = (row.get("type"), row.get("target_id"))
        if row.get("type") in _GROUPABLE_TYPES and key in index_by_key:
            existing = grouped[index_by_key[key]]
            existing["extra_count"] = existing.get("extra_count", 0) + 1
            names = existing.setdefault("actor_names", [existing["actor_full_name"]])
            if row.get("actor_full_name") and row["actor_full_name"] not in names:
                names.append(row["actor_full_name"])
            if not existing.get("read") is False:
                existing["read"] = existing["read"] and row.get("read", True)
            continue
        row["extra_count"] = 0
        row["actor_names"] = [row.get("actor_full_name")] if row.get("actor_full_name") else []
        index_by_key[key] = len(grouped)
        grouped.append(row)
    return grouped


@bp.get("")
@require_auth
def list_notifications():
    # Over-fetch raw rows relative to the requested page size, since
    # grouping collapses several rows into one card — without the
    # buffer, a page that's mostly one pile-on would come back looking
    # thin even though plenty more distinct activity exists.
    limit = int(request.args.get("limit", 30))
    data, status = rest_request(
        "GET", "notifications", token=g.token,
        params={
            "user_id": f"eq.{g.user_id}",
            "select": "id,type,target_type,target_id,actor_id,read,created_at,"
                      "actor:users!notifications_actor_id_fkey(full_name,avatar_url)",
            "order": "created_at.desc",
            "limit": limit * 4,
        },
    )
    if status != 200:
        return jsonify({"error": "could not load notifications"}), status
    flattened = [_flatten_actor(row) for row in (data or [])]
    grouped = _group_notifications(flattened)
    return jsonify(grouped[:limit]), 200


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
