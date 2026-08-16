from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request, rest_count, rpc
from lib.decorators import require_staff, require_admin, is_owner_user
from lib.audit import log_admin_action

bp = Blueprint("admin", __name__, url_prefix="/api/admin")

VALID_ROLES = ("student", "moderator", "admin")


@bp.get("/staff")
@require_staff
def list_staff():
    """Everyone currently holding moderator or admin — the roster
    view for the Team tab, so it's obvious at a glance who already has
    access before promoting anyone new."""
    data, status = rest_request(
        "GET", "users", token=g.token,
        params={"role": "in.(moderator,admin)", "select": "id,full_name,avatar_url,role,student_email", "order": "role.desc"},
    )
    if status != 200:
        return jsonify({"error": "could not load staff list"}), status
    return jsonify(data), 200


@bp.post("/users/<user_id>/role")
@require_admin
def set_role(user_id):
    """Promotes or demotes someone to/from moderator or admin — the
    actual replacement for hand-editing the database every time.
    Deliberately @require_admin, not @require_staff: if a moderator
    could call this, they could promote themselves straight to admin.

    The admin tier specifically is further restricted to the owner
    only — promoting to admin or demoting FROM admin requires
    is_owner, checked inline below. Moderator changes stay at the
    @require_admin level. This used to let any admin grant or revoke
    admin access to anyone, which stopped making sense once is_owner
    existed as a tier above it — an admin quietly adding another admin
    behind the owner's back was a real gap, not a hypothetical one.

    Guards against a founder locking themselves out by accident: you
    can't demote your own account through this route."""
    body = request.get_json(silent=True) or {}
    new_role = body.get("role")
    if new_role not in VALID_ROLES:
        return jsonify({"error": f"role must be one of {', '.join(VALID_ROLES)}"}), 400

    if user_id == g.user_id and new_role != "admin":
        return jsonify({"error": "you can't demote your own account"}), 400

    current, cstatus = rest_request(
        "GET", "users", token=g.token, params={"id": f"eq.{user_id}", "select": "role"},
    )
    old_role = (current or [{}])[0].get("role") if cstatus == 200 else None

    if (new_role == "admin" or old_role == "admin") and not is_owner_user(g.token, g.user_id):
        return jsonify({"error": "only the owner can grant or revoke admin access"}), 403

    data, status = rest_request(
        "PATCH", "users", token=g.token,
        params={"id": f"eq.{user_id}"}, json_body={"role": new_role}, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not update role"}), status
    if not data:
        return jsonify({"error": "user not found"}), 404

    log_admin_action(
        g.user_id, g.token, "role_change",
        target_type="user", target_id=user_id,
        detail={"from_role": old_role, "to_role": new_role},
    )
    return jsonify(data[0]), 200


@bp.get("/stats")
@require_staff
def stats():
    """The growth dashboard — every number a founder/admin actually
    checks in on, in one call. Each figure uses rest_count (exact
    count via PostgREST, no rows downloaded) rather than fetching full
    tables, so this stays a fast single screen load even once
    CampusMEET has thousands of users across many campuses instead of
    30 at one.

    Deliberately read-only and side-effect free — this endpoint never
    writes anything, it only ever answers "where do things stand right
    now."
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    def count(table, params=None):
        n, status = rest_count(table, token=g.token, params=params or {})
        return n if status == 200 else None

    total_users = count("users")
    verified_users = count("users", {"verified_at": "not.is.null"})
    pending_users = count("users", {"verified_at": "is.null"})
    new_users_7d = count("users", {"created_at": f"gte.{week_ago}"})

    total_posts = count("posts", {"status": "eq.active"})
    posts_today = count("posts", {"status": "eq.active", "created_at": f"gte.{today_start}"})

    total_universities = count("universities")

    pending_reports = count("reports", {"status": "eq.pending"})

    # Active universities = universities with at least one signed-up
    # user. Not a straight rest_count (PostgREST can't COUNT DISTINCT
    # a foreign column through this simple params interface), so this
    # one still reads the distinct university_id column — a single
    # narrow column across all users, not a full-table fetch, which is
    # the meaningful cost difference the other counts above avoid.
    uni_rows, uni_status = rest_request(
        "GET", "users", token=g.token, params={"select": "university_id"},
    )
    active_universities = len({r["university_id"] for r in uni_rows}) if uni_status == 200 and uni_rows else None

    return jsonify({
        "total_users": total_users,
        "verified_users": verified_users,
        "pending_users": pending_users,
        "new_users_7d": new_users_7d,
        "total_posts": total_posts,
        "posts_today": posts_today,
        "total_universities": total_universities,
        "active_universities": active_universities,
        "pending_reports": pending_reports,
    }), 200


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
    log_admin_action(g.user_id, g.token, "student_verified", target_type="user", target_id=user_id)
    return jsonify({"ok": True}), 200


@bp.post("/users/<user_id>/unverify")
@require_staff
def unverify_user(user_id):
    data, status = rpc("unverify_student", token=g.token, payload={"p_user_id": user_id})
    if status >= 400:
        return jsonify({"error": "unverify failed"}), status
    log_admin_action(g.user_id, g.token, "student_unverified", target_type="user", target_id=user_id)
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
    log_admin_action(
        g.user_id, g.token, "report_resolved",
        target_type="report", target_id=report_id, detail={"status": status_val},
    )
    return jsonify(data[0] if data else {}), 200


@bp.get("/activity")
@require_staff
def list_activity():
    """The audit log — every role change, report resolution, and
    student verification, newest-first. RLS
    (admin_actions_select_staff) already restricts this to staff, this
    decorator is a second, redundant layer at the route level."""
    data, status = rest_request(
        "GET", "admin_actions", token=g.token,
        params={
            "select": "id,action_type,target_type,target_id,detail,created_at,"
                      "actor:users!admin_actions_actor_id_fkey(id,full_name,avatar_url)",
            "order": "created_at.desc",
            "limit": "100",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load activity"}), status
    return jsonify(data), 200


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
