import uuid

from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request, storage_upload
from lib.decorators import require_auth, optional_auth
from lib.image_processing import normalize_image, UnsupportedImageError
from routes.posts import (
    _filter_blocked,
    _filter_by_audience,
    _attach_user_reactions,
    _attach_original_posts,
    _attach_polls,
    _attach_mentions,
    _attach_images,
)

bp = Blueprint("groups", __name__, url_prefix="/api/groups")

MAX_GROUP_AVATAR_BYTES = 4 * 1024 * 1024


def _require_admin(group_id, user_id, token):
    """Returns True if user_id is an admin of group_id. Every settings-
    type action below (edit, delete, avatar, member management) shares
    this exact same check, so it's centralized rather than repeated."""
    membership, status = rest_request(
        "GET", "group_members", token=token,
        params={"group_id": f"eq.{group_id}", "user_id": f"eq.{user_id}", "select": "role"},
    )
    return status == 200 and membership and membership[0]["role"] == "admin"


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
    # Same protection as remove_member/update_member_role: a group
    # with members left in it but zero admins is a dead end no one
    # can recover from through the app. Leaving is fine if you're the
    # only person left entirely — there's no one left to manage.
    members, mstatus = rest_request(
        "GET", "group_members", token=g.token,
        params={"group_id": f"eq.{group_id}", "select": "user_id,role"},
    )
    if mstatus == 200 and members:
        admins = [m for m in members if m["role"] == "admin"]
        is_sole_admin = len(admins) == 1 and admins[0]["user_id"] == g.user_id
        if is_sole_admin and len(members) > 1:
            return jsonify({"error": "promote another member to admin before you leave"}), 400

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

    data = _filter_blocked(data, g.token)
    data = _filter_by_audience(data, g.user_id, g.token)
    _attach_user_reactions(data, g.token, g.user_id)
    _attach_original_posts(data, g.token)
    _attach_polls(data, g.token)
    _attach_mentions(data, g.token)
    _attach_images(data, g.token)
    return jsonify(data), 200


@bp.patch("/<group_id>")
@require_auth
def update_group(group_id):
    """Any admin (not just the original creator) can edit settings —
    matches how FB group admin roles work: promoting someone to admin
    gives them real settings authority, not a lesser co-admin tier."""
    if not _require_admin(group_id, g.user_id, g.token):
        return jsonify({"error": "only a group admin can edit this group"}), 403

    body = request.get_json(silent=True) or {}
    updates = {}

    if "name" in body:
        name = (body["name"] or "").strip()
        if not (2 <= len(name) <= 80):
            return jsonify({"error": "group name must be 2-80 characters"}), 400
        updates["name"] = name

    if "description" in body:
        description = (body["description"] or "").strip()
        if len(description) > 500:
            return jsonify({"error": "description must be under 500 characters"}), 400
        updates["description"] = description or None

    if "privacy" in body:
        if body["privacy"] not in ("public", "private"):
            return jsonify({"error": "privacy must be 'public' or 'private'"}), 400
        updates["privacy"] = body["privacy"]

    if not updates:
        return jsonify({"error": "nothing to update"}), 400

    data, status = rest_request(
        "PATCH", "groups", token=g.token,
        params={"id": f"eq.{group_id}"}, json_body=updates, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not update group"}), status
    updated = data[0] if isinstance(data, list) else data
    updated["is_member"] = True
    updated["my_role"] = "admin"
    return jsonify(updated), 200


@bp.delete("/<group_id>")
@require_auth
def delete_group(group_id):
    """Creator only — not just any admin. A co-admin can manage
    settings and members, but disbanding the group entirely stays
    with whoever actually founded it (matches the RLS policy in
    db/group_settings_migration.sql, which is the real enforcement
    point; this check just gives a clean error instead of a bare 403
    from PostgREST)."""
    group, gstatus = rest_request(
        "GET", "groups", token=g.token, params={"id": f"eq.{group_id}", "select": "creator_id"},
    )
    if gstatus != 200 or not group:
        return jsonify({"error": "group not found"}), 404
    if group[0]["creator_id"] != g.user_id:
        return jsonify({"error": "only the group's creator can delete it"}), 403

    data, status = rest_request("DELETE", "groups", token=g.token, params={"id": f"eq.{group_id}"})
    if status >= 400:
        return jsonify({"error": "could not delete group"}), status
    return jsonify({"deleted": True}), 200


@bp.post("/<group_id>/upload-avatar")
@require_auth
def upload_group_avatar(group_id):
    if not _require_admin(group_id, g.user_id, g.token):
        return jsonify({"error": "only a group admin can change the group photo"}), 403

    if "avatar" not in request.files:
        return jsonify({"error": "attach an image file under the 'avatar' field"}), 400

    file = request.files["avatar"]
    file_bytes = file.read()
    if len(file_bytes) > MAX_GROUP_AVATAR_BYTES:
        return jsonify({"error": "image must be under 4MB"}), 400

    try:
        file_bytes, content_type, extension = normalize_image(file_bytes)
    except UnsupportedImageError as exc:
        return jsonify({"error": str(exc)}), 400

    # Fixed filename per group (not a fresh uuid each time) so a
    # re-upload overwrites the old photo — same reasoning as user
    # avatars in profile.py.
    path = f"{group_id}/avatar.{extension}"
    upload_data, status = storage_upload("group-avatars", path, file_bytes, content_type, g.token)
    if status >= 400:
        return jsonify({"error": "could not upload group photo"}), status

    avatar_url = f"{upload_data['url']}?v={uuid.uuid4().hex[:8]}"  # cache-bust like avatars do
    data, ustatus = rest_request(
        "PATCH", "groups", token=g.token,
        params={"id": f"eq.{group_id}"}, json_body={"avatar_url": avatar_url}, prefer="return=representation",
    )
    if ustatus >= 400:
        return jsonify({"error": "photo uploaded but could not be saved to the group"}), ustatus
    updated = data[0] if isinstance(data, list) else data
    return jsonify(updated), 200


@bp.patch("/<group_id>/members/<user_id>")
@require_auth
def update_member_role(group_id, user_id):
    """Promote/demote. Refuses to demote the group's only remaining
    admin — otherwise a group could end up with zero admins and no
    one left able to manage it, which is a dead end no one can
    recover from through the app."""
    if not _require_admin(group_id, g.user_id, g.token):
        return jsonify({"error": "only a group admin can change member roles"}), 403

    body = request.get_json(silent=True) or {}
    new_role = body.get("role")
    if new_role not in ("admin", "member"):
        return jsonify({"error": "role must be 'admin' or 'member'"}), 400

    if new_role == "member":
        admins, astatus = rest_request(
            "GET", "group_members", token=g.token,
            params={"group_id": f"eq.{group_id}", "role": "eq.admin", "select": "user_id"},
        )
        if astatus == 200 and admins and len(admins) == 1 and admins[0]["user_id"] == user_id:
            return jsonify({"error": "promote another admin first — a group needs at least one"}), 400

    data, status = rest_request(
        "PATCH", "group_members", token=g.token,
        params={"group_id": f"eq.{group_id}", "user_id": f"eq.{user_id}"},
        json_body={"role": new_role}, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not update member role"}), status
    if not data:
        return jsonify({"error": "member not found in this group"}), 404
    return jsonify({"user_id": user_id, "role": new_role}), 200


@bp.delete("/<group_id>/members/<user_id>")
@require_auth
def remove_member(group_id, user_id):
    """Removing an admin isn't allowed through this endpoint at all —
    demote them first via update_member_role (which itself refuses to
    demote a sole admin). That ordering means a group can never lose
    its last admin through either action alone."""
    if not _require_admin(group_id, g.user_id, g.token):
        return jsonify({"error": "only a group admin can remove members"}), 403

    target, tstatus = rest_request(
        "GET", "group_members", token=g.token,
        params={"group_id": f"eq.{group_id}", "user_id": f"eq.{user_id}", "select": "role"},
    )
    if tstatus != 200 or not target:
        return jsonify({"error": "member not found in this group"}), 404
    if target[0]["role"] == "admin":
        return jsonify({"error": "demote this admin to a regular member before removing them"}), 400

    data, status = rest_request(
        "DELETE", "group_members", token=g.token,
        params={"group_id": f"eq.{group_id}", "user_id": f"eq.{user_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not remove member"}), status
    return jsonify({"removed": True}), 200
