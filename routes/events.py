from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth, optional_auth
from lib.pagination import paginate_args

bp = Blueprint("events", __name__, url_prefix="/api/events")


def _with_rsvp_flag(events_list, user_id, token):
    if not events_list or not user_id:
        for e in events_list or []:
            e["my_rsvp"] = None
        return events_list

    event_ids = [e["id"] for e in events_list]
    rsvps, status = rest_request(
        "GET", "event_rsvps", token=token,
        params={"event_id": f"in.({','.join(event_ids)})", "user_id": f"eq.{user_id}", "select": "event_id,status"},
    )
    by_event = {r["event_id"]: r["status"] for r in rsvps} if status == 200 and rsvps else {}
    for e in events_list:
        e["my_rsvp"] = by_event.get(e["id"])
    return events_list


@bp.get("")
@optional_auth
def list_events():
    """Upcoming events only (start_at in the future), soonest first —
    same "don't drown a new campus in a global list" scoping as posts
    and groups when signed in."""
    limit, offset = paginate_args(default_limit=30, max_limit=60)
    group_id = request.args.get("group_id")

    params = {
        "select": "*", "start_at": f"gte.{datetime.now(timezone.utc).isoformat()}",
        "order": "start_at.asc", "limit": limit, "offset": offset,
    }
    if group_id:
        params["group_id"] = f"eq.{group_id}"
    elif g.user_id:
        me, mstatus = rest_request(
            "GET", "users", token=g.token, params={"id": f"eq.{g.user_id}", "select": "university_id"},
        )
        university_id = (me or [{}])[0].get("university_id") if mstatus == 200 else None
        if university_id:
            params["university_id"] = f"eq.{university_id}"

    data, status = rest_request("GET", "events", token=g.token, params=params)
    if status != 200:
        return jsonify({"error": "could not load events"}), status
    data = _with_rsvp_flag(data or [], g.user_id, g.token)
    return jsonify(data), 200


@bp.post("")
@require_auth
def create_event():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    description = (body.get("description") or "").strip() or None
    location = (body.get("location") or "").strip() or None
    start_at = body.get("start_at")
    end_at = body.get("end_at")
    group_id = body.get("group_id")
    cover_url = body.get("cover_url")

    if not (2 <= len(title) <= 120):
        return jsonify({"error": "event title must be 2-120 characters"}), 400
    if not start_at:
        return jsonify({"error": "start_at is required (ISO 8601 datetime)"}), 400

    if group_id:
        membership, mstatus = rest_request(
            "GET", "group_members", token=g.token,
            params={"group_id": f"eq.{group_id}", "user_id": f"eq.{g.user_id}", "select": "user_id"},
        )
        if mstatus != 200 or not membership:
            return jsonify({"error": "join the group before creating an event under it"}), 403

    profile, pstatus = rest_request(
        "GET", "users", token=g.token, params={"id": f"eq.{g.user_id}", "select": "university_id"},
    )
    if pstatus != 200 or not profile:
        return jsonify({"error": "could not resolve university"}), 400

    payload = {
        "university_id": profile[0]["university_id"],
        "creator_id": g.user_id,
        "group_id": group_id,
        "title": title,
        "description": description,
        "location": location,
        "cover_url": cover_url,
        "start_at": start_at,
        "end_at": end_at,
    }
    data, status = rest_request(
        "POST", "events", token=g.token, json_body=payload, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not create event"}), status
    created = data[0] if isinstance(data, list) else data
    created["my_rsvp"] = None
    return jsonify(created), 201


@bp.get("/<event_id>")
@optional_auth
def get_event(event_id):
    data, status = rest_request("GET", "events", token=g.token, params={"id": f"eq.{event_id}", "select": "*"})
    if status != 200 or not data:
        return jsonify({"error": "event not found"}), 404
    result = _with_rsvp_flag(data, g.user_id, g.token)[0]
    return jsonify(result), 200


@bp.post("/<event_id>/rsvp")
@require_auth
def rsvp_event(event_id):
    body = request.get_json(silent=True) or {}
    status_val = body.get("status")
    if status_val not in ("interested", "going"):
        return jsonify({"error": "status must be 'interested' or 'going'"}), 400

    data, status = rest_request(
        "POST", "event_rsvps", token=g.token,
        json_body={"event_id": event_id, "user_id": g.user_id, "status": status_val},
        prefer="resolution=merge-duplicates,return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not RSVP"}), status
    return jsonify({"rsvp": status_val}), 200


@bp.delete("/<event_id>/rsvp")
@require_auth
def cancel_rsvp(event_id):
    data, status = rest_request(
        "DELETE", "event_rsvps", token=g.token,
        params={"event_id": f"eq.{event_id}", "user_id": f"eq.{g.user_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not cancel RSVP"}), status
    return jsonify({"rsvp": None}), 200


@bp.get("/<event_id>/attendees")
def event_attendees(event_id):
    """`status` query param filters to 'interested' or 'going' — the
    frontend shows these as two separate tappable lists, same
    FollowListModal-style pattern as followers/following."""
    status_filter = request.args.get("status")
    params = {"event_id": f"eq.{event_id}", "select": "user_id,status", "order": "created_at.asc"}
    if status_filter in ("interested", "going"):
        params["status"] = f"eq.{status_filter}"

    rsvps, status = rest_request("GET", "event_rsvps", params=params)
    if status != 200:
        return jsonify({"error": "could not load attendees"}), status
    if not rsvps:
        return jsonify([]), 200

    user_ids = [r["user_id"] for r in rsvps]
    users, ustatus = rest_request(
        "GET", "users",
        params={"id": f"in.({','.join(user_ids)})", "select": "id,full_name,avatar_url,verified_at"},
    )
    by_id = {u["id"]: u for u in (users or [])} if ustatus == 200 else {}
    out = []
    for r in rsvps:
        u = by_id.get(r["user_id"])
        if u:
            out.append({
                "id": u["id"], "full_name": u["full_name"], "avatar_url": u.get("avatar_url"),
                "verified": u.get("verified_at") is not None, "status": r["status"],
            })
    return jsonify(out), 200
