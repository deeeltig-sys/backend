from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth, optional_auth
from routes.posts import (
    _bearer_token_if_present,
    _filter_blocked,
    _attach_user_reactions,
    _attach_original_posts,
    _attach_polls,
)

bp = Blueprint("groups", __name__, url_prefix="/api/groups")


def _with_membership_flag(groups_list, user_id, token):
    """Attaches `is_member`/`my_role` to each group for the calling
    user — same "annotate with the caller's own relationship to the
    row" pattern as _attach_user_reactions in posts.py."""
    if not groups_list or not user_id:
        for gr in groups_list or []:
            gr["is_member"] = False
            gr["my_role"] = None
        return groups_list

    group_ids = [gr["id"] for gr in groups_list]
    memberships, status = rest_request(
        "GET", "group_members", token=token,
        params={"group_id": f"in.({','.join(group_ids)})", "user_id": f"eq.{user_id}", "select": "group_id,role"},
    )
    by_group = {m["group_id"]: m["role"] for m in memberships} if status == 200 and memberships else {}
    for gr in groups_list:
        role = by_group.get(gr["id"])
        gr["is_member"] = role is not None
        gr["my_role"] = role
    return groups_list


@bp.get("")
@optional_auth
def list_groups():
    """Discover feed of groups — scoped to the caller's own
    university when signed in (same reasoning as the campus/national
    toggle on the main post feed: a brand-new university shouldn't
    see itself drowned out by whichever campus onboarded first)."""
    limit = request.args.get("limit", 30)
    offset = request.args.get("offset", 0)
    params = {"select": "*", "order": "member_count.desc", "limit": limit, "offset": offset}

    if g.user_id:
        me, mstatus = rest_request(
            "GET", "users", token=g.token, params={"id": f"eq.{g.user_id}", "select": "university_id"},
        )
        university_id = (me or [{}])[0].get("university_id") if mstatus == 200 else None
        if university_id:
            params["university_id"] = f"eq.{university_id}"

    data, status = rest_request("GET", "groups", token=g.token, params=params)
    if status != 200:
        return jsonify({"error": "could not load groups"}), status
    data = _with_membership_flag(data or [], g.user_id, g.token)
    return jsonify(data), 200


@bp.get("/mine")
@require_auth
def my_groups():
    """Groups the caller actually belongs to — powers a 'My Groups'
    list distinct from the discover feed above."""
    memberships, status = rest_request(
        "GET", "group_members", token=g.token,
        params={"user_id": f"eq.{g.user_id}", "select": "group_id,role"},
    )
    if status != 200:
        return jsonify({"error": "could not load your groups"}), status
    if not memberships:
        return jsonify([]), 200

    group_ids = [m["group_id"] for m in memberships]
    data, gstatus = rest_request(
        "GET", "groups", token=g.token, params={"id": f"in.({','.join(group_ids)})", "select": "*"},
    )
    if gstatus != 200:
        return jsonify({"error": "could not load your groups"}), gstatus
    role_by_id = {m["group_id"]: m["role"] for m in memberships}
    for gr in data or []:
        gr["is_member"] = True
        gr["my_role"] = role_by_id.get(gr["id"])
    return jsonify(data or []), 200


@bp.post("")
@require_auth
def create_group():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip() or None
    privacy = body.get("privacy", "public")
    avatar_url = body.get("avatar_url")

    if not (2 <= len(name) <= 80):
        return jsonify({"error": "group name must be 2-80 characters"}), 400
    if privacy not in ("public", "private"):
        return jsonify({"error": "privacy must be 'public' or 'private'"}), 400

    profile, pstatus = rest_request(
        "GET", "users", token=g.token, params={"id": f"eq.{g.user_id}", "select": "university_id"},
    )
    if pstatus != 200 or not profile:
        return jsonify({"error": "could not resolve university"}), 400

    payload = {
        "university_id": profile[0]["university_id"],
        "creator_id": g.user_id,
        "name": name,
        "description": description,
        "privacy": privacy,
        "avatar_url": avatar_url,
    }
    data, status = rest_request(
        "POST", "groups", token=g.token, json_body=payload, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not create group"}), status
    created = data[0] if isinstance(data, list) else data
    created["is_member"] = True
    created["my_role"] = "admin"
    return jsonify(created), 201


@bp.get("/<group_id>")
@optional_auth
def get_group(group_id):
    data, status = rest_request(
        "GET", "groups", token=g.token, params={"id": f"eq.{group_id}", "select": "*"},
    )
    if status != 200 or not data:
        return jsonify({"error": "group not found"}), 404
    result = _with_membership_flag(data, g.user_id, g.token)[0]
    return jsonify(result), 200


@bp.post("/<group_id>/join")
@require_auth
def join_group(group_id):
    data, status = rest_request(
        "POST", "group_members", token=g.token,
        json_body={"group_id": group_id, "user_id": g.user_id, "role": "member"},
        prefer="resolution=ignore-duplicates",
    )
    if status >= 400:
        return jsonify({"error": "could not join — this may be a private group"}), status
    return jsonify({"joined": True}), 201


@bp.delete("/<group_id>/join")
@require_auth
def leave_group(group_id):
    data, status = rest_request(
        "DELETE", "group_members", token=g.token,
        params={"group_id": f"eq.{group_id}", "user_id": f"eq.{g.user_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not leave group"}), status
    return jsonify({"joined": False}), 200


@bp.get("/<group_id>/members")
def group_members(group_id):
    memberships, status = rest_request(
        "GET", "group_members", params={"group_id": f"eq.{group_id}", "select": "user_id,role,joined_at", "order": "joined_at.asc"},
    )
    if status != 200:
        return jsonify({"error": "could not load members"}), status
    if not memberships:
        return jsonify([]), 200

    user_ids = [m["user_id"] for m in memberships]
    users, ustatus = rest_request(
        "GET", "users",
        params={"id": f"in.({','.join(user_ids)})", "select": "id,full_name,avatar_url,verified_at"},
    )
    by_id = {u["id"]: u for u in (users or [])} if ustatus == 200 else {}
    role_by_id = {m["user_id"]: m["role"] for m in memberships}
    out = []
    for uid in user_ids:
        u = by_id.get(uid)
        if u:
            out.append({
                "id": u["id"], "full_name": u["full_name"], "avatar_url": u.get("avatar_url"),
                "verified": u.get("verified_at") is not None, "role": role_by_id.get(uid),
            })
    return jsonify(out), 200


@bp.get("/<group_id>/posts")
@optional_auth
def group_posts(group_id):
    """The group's own feed — reuses the `feed` view (group_id is
    now one of its explicit columns, see db/groups_migration.sql) so
    a group post gets identical author info, reactions, reposts, and
    poll rendering to every other post in the app."""
    limit = request.args.get("limit", 30)
    offset = request.args.get("offset", 0)
    data, status = rest_request(
        "GET", "feed",
        params={
            "select": "*", "group_id": f"eq.{group_id}",
            "order": "created_at.desc", "limit": limit, "offset": offset,
        },
    )
    if status != 200:
        return jsonify({"error": "could not load group posts"}), status

    token = _bearer_token_if_present()
    data = _filter_blocked(data, token)
    _attach_user_reactions(data, token)
    _attach_original_posts(data, token)
    _attach_polls(data, token)
    return jsonify(data), 200
