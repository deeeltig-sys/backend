from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request, rpc
from lib.decorators import require_staff

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.get("/users")
@require_staff
def list_users():
    """Powers the admin dashboard's user list. ?verified=false shows
    the pending queue — everyone who signed up but hasn't been
    manually verified yet."""
    params = {"select": "*", "order": "created_at.desc"}
    verified_filter = request.args.get("verified")
    if verified_filter == "false":
        params["verified_at"] = "is.null"
    elif verified_filter == "true":
        params["verified_at"] = "not.is.null"

    data, status = rest_request("GET", "users", token=g.token, params=params)
    if status != 200:
        return jsonify({"error": "could not load users"}), status
    return jsonify(data), 200


@bp.post("/users/<user_id>/verify")
@require_staff
def verify_user(user_id):
    """The 'Verify USTED' button. Goes through the verify_student RPC,
    not a raw UPDATE, so verified_by is set server-side to the actual
    admin's own id and can't be spoofed."""
    data, status = rpc("verify_student", token=g.token, payload={"p_user_id": user_id})
    if status >= 400:
        return jsonify({"error": "verification failed"}), status
    return jsonify({"ok": True}), 200


@bp.post("/users/<user_id>/unverify")
@require_staff
def unverify_user(user_id):
    data, status = rpc("unverify_student", token=g.token, payload={"p_user_id": user_id})
    if status >= 400:
        return jsonify({"error": "unverify failed"}), status
    return jsonify({"ok": True}), 200


@bp.get("/reports")
@require_staff
def list_reports():
    data, status = rest_request(
        "GET", "reports", token=g.token, params={"select": "*", "order": "created_at.desc"},
    )
    if status != 200:
        return jsonify({"error": "could not load reports"}), status
    return jsonify(data), 200


@bp.patch("/reports/<report_id>")
@require_staff
def update_report(report_id):
    body = request.get_json(silent=True) or {}
    status_val = body.get("status")
    if status_val not in ("pending", "reviewed", "actioned"):
        return jsonify({"error": "status must be pending, reviewed, or actioned"}), 400

    data, status = rest_request(
        "PATCH", "reports", token=g.token,
        params={"id": f"eq.{report_id}"}, json_body={"status": status_val},
        prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "update failed"}), status
    return jsonify(data[0] if data else {}), 200


@bp.get("/reactions/velocity")
@require_staff
def yawa_velocity():
    """Read-only view of how fast posts are picking up yawa reactions in a
    recent window. This is a monitoring tool for staff, not a moderation
    mechanism — it doesn't touch feed_score(), reaction_count, or a post's
    status, and nothing here suppresses or flags a post automatically.
    Every post keeps the same weight in the feed no matter what shows up
    here; it's just visibility so a human can go look if they want to.
    """
    window_hours = request.args.get("window_hours", default=6, type=int)
    window_hours = max(1, min(window_hours, 72))
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

    data, status = rest_request(
        "GET", "reactions", token=g.token,
        params={
            "select": "post_id,created_at",
            "type": "eq.yawa",
            "created_at": f"gte.{since}",
            "order": "created_at.desc",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load reaction activity"}), status

    counts = defaultdict(int)
    for row in data:
        counts[row["post_id"]] += 1

    if not counts:
        return jsonify([]), 200

    post_ids = ",".join(counts.keys())
    posts, pstatus = rest_request(
        "GET", "posts", token=g.token,
        params={"id": f"in.({post_ids})", "select": "id,content,author_id"},
    )
    posts_by_id = {p["id"]: p for p in posts} if pstatus == 200 else {}

    results = [
        {
            "post_id": post_id,
            "yawa_count_window": count,
            "per_hour": round(count / window_hours, 2),
            "content_preview": (posts_by_id.get(post_id, {}).get("content") or "")[:140],
            "author_id": posts_by_id.get(post_id, {}).get("author_id"),
        }
        for post_id, count in counts.items()
    ]
    results.sort(key=lambda r: r["yawa_count_window"], reverse=True)
    return jsonify(results), 200
