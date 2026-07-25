from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request, rpc
from lib.decorators import require_auth
from models.user import public_user_fields

bp = Blueprint("friends", __name__, url_prefix="/api/friends")


def _friend_ids(user_id, token):
    """Every user_id this person is friends with — friendships is
    normalized (user_a < user_b), so both sides need checking."""
    data, status = rest_request(
        "GET", "friendships", token=token,
        params={"or": f"(user_a.eq.{user_id},user_b.eq.{user_id})", "select": "user_a,user_b"},
    )
    if status != 200:
        return []
    ids = set()
    for row in data or []:
        ids.add(row["user_a"])
        ids.add(row["user_b"])
    ids.discard(user_id)
    return list(ids)


@bp.get("")
@require_auth
def my_friends():
    ids = _friend_ids(g.user_id, g.token)
    if not ids:
        return jsonify([]), 200
    data, status = rest_request(
        "GET", "users", token=g.token,
        params={"id": f"in.({','.join(ids)})", "select": "*", "order": "full_name.asc"},
    )
    if status != 200:
        return jsonify({"error": "could not load friends"}), status
    return jsonify([public_user_fields(row) for row in (data or [])]), 200


@bp.get("/<user_id>")
@require_auth
def someone_else_friends(user_id):
    """Browsing another person's friend list — this is the actual
    'friends of friends' discovery mechanic: tap into a profile's
    friends, then send requests from there. RLS on friendships only
    restricts writes, not reads of who's friends with whom in general,
    so this works for browsing anyone (matches how Facebook's public
    friend lists behave by default)."""
    ids = _friend_ids(user_id, g.token)
    if not ids:
        return jsonify([]), 200
    data, status = rest_request(
        "GET", "users", token=g.token,
        params={"id": f"in.({','.join(ids)})", "select": "*", "order": "full_name.asc"},
    )
    if status != 200:
        return jsonify({"error": "could not load friends"}), status
    return jsonify([public_user_fields(row) for row in (data or [])]), 200


@bp.get("/requests")
@require_auth
def list_requests():
    """direction=incoming (default) or outgoing."""
    direction = request.args.get("direction", "incoming")
    field = "receiver_id" if direction == "incoming" else "sender_id"
    other_field = "sender:users!friend_requests_sender_id_fkey" if direction == "incoming" \
        else "receiver:users!friend_requests_receiver_id_fkey"
    other_key = "sender" if direction == "incoming" else "receiver"

    data, status = rest_request(
        "GET", "friend_requests", token=g.token,
        params={
            field: f"eq.{g.user_id}", "status": "eq.pending",
            "select": f"id,created_at,{other_field}(id,full_name,avatar_url,verified_at)",
            "order": "created_at.desc",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load requests"}), status

    result = []
    for row in data or []:
        other = row.pop(other_key, None) or {}
        result.append({
            "id": row["id"],
            "created_at": row["created_at"],
            "user": {
                "id": other.get("id"),
                "full_name": other.get("full_name"),
                "avatar_url": other.get("avatar_url"),
                "verified": other.get("verified_at") is not None,
            },
        })
    return jsonify(result), 200


@bp.post("/requests/<user_id>")
@require_auth
def send_request(user_id):
    data, status = rpc("send_friend_request", token=g.token, payload={"p_receiver_id": user_id})
    if status >= 400:
        msg = (data or {}).get("message", "") if isinstance(data, dict) else ""
        if "already friends" in msg:
            return jsonify({"error": "already friends"}), 409
        return jsonify({"error": "could not send friend request"}), status
    return jsonify({"sent": True}), 201


@bp.post("/requests/<request_id>/accept")
@require_auth
def accept_request(request_id):
    data, status = rpc("respond_to_friend_request", token=g.token, payload={"p_request_id": request_id, "p_accept": True})
    if status >= 400:
        return jsonify({"error": "could not accept request"}), status
    return jsonify({"accepted": True}), 200


@bp.post("/requests/<request_id>/decline")
@require_auth
def decline_request(request_id):
    data, status = rpc("respond_to_friend_request", token=g.token, payload={"p_request_id": request_id, "p_accept": False})
    if status >= 400:
        return jsonify({"error": "could not decline request"}), status
    return jsonify({"declined": True}), 200


@bp.delete("/requests/<request_id>")
@require_auth
def cancel_request(request_id):
    """Sender canceling their own still-pending outgoing request —
    RLS (friend_requests_delete_sender) already restricts this to the
    sender and to still-pending rows."""
    data, status = rest_request("DELETE", "friend_requests", token=g.token, params={"id": f"eq.{request_id}"})
    if status >= 400:
        return jsonify({"error": "could not cancel request"}), status
    return jsonify({"cancelled": True}), 200


@bp.delete("/<user_id>")
@require_auth
def unfriend(user_id):
    data, status = rpc("remove_friendship", token=g.token, payload={"p_other_user_id": user_id})
    if status >= 400:
        return jsonify({"error": "could not unfriend"}), status
    return jsonify({"unfriended": True}), 200


@bp.get("/suggestions")
@require_auth
def friend_suggestions():
    """Friends-of-friends — the actual discovery mechanic beyond just
    browsing one profile at a time. People who share at least one
    friend with the caller, aren't already a friend, and aren't the
    caller. Capped to a reasonable pool of mutual-friend lookups so
    this doesn't blow up for someone with hundreds of friends."""
    limit = min(int(request.args.get("limit", 15)), 30)
    my_ids = set(_friend_ids(g.user_id, g.token))
    if not my_ids:
        return jsonify([]), 200

    candidates = {}
    for friend_id in list(my_ids)[:20]:
        their_friends = _friend_ids(friend_id, g.token)
        for candidate_id in their_friends:
            if candidate_id == g.user_id or candidate_id in my_ids:
                continue
            candidates[candidate_id] = candidates.get(candidate_id, 0) + 1

    if not candidates:
        return jsonify([]), 200

    ranked_ids = sorted(candidates, key=candidates.get, reverse=True)[:limit]
    data, status = rest_request(
        "GET", "users", token=g.token,
        params={"id": f"in.({','.join(ranked_ids)})", "select": "*"},
    )
    if status != 200:
        return jsonify({"error": "could not load suggestions"}), status

    by_id = {row["id"]: row for row in (data or [])}
    result = []
    for cid in ranked_ids:
        if cid in by_id:
            shaped = public_user_fields(by_id[cid])
            shaped["mutual_friends"] = candidates[cid]
            result.append(shaped)
    return jsonify(result), 200
