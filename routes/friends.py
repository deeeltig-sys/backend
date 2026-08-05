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
        # TEMP DEBUG — remove after diagnosing: surfaces the real
        # PostgREST/Postgres error instead of the generic message.
        return jsonify({"error": "could not send friend request", "debug": data}), status
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


def _pending_request_ids(user_id, token):
    """Everyone the caller has an open (pending) friend_request with in
    either direction — these belong in the Requests tab, not "People
    you may know" too. Without this exclusion, someone you already
    sent a request to (or who already sent you one) would keep
    reappearing in the suggestions grid right next to the Requests
    section showing the same person, which reads as broken."""
    data, status = rest_request(
        "GET", "friend_requests", token=token,
        params={
            "or": f"(sender_id.eq.{user_id},receiver_id.eq.{user_id})",
            "status": "eq.pending",
            "select": "sender_id,receiver_id",
        },
    )
    if status != 200:
        return set()
    ids = set()
    for row in data or []:
        ids.add(row["sender_id"])
        ids.add(row["receiver_id"])
    ids.discard(user_id)
    return ids


@bp.get("/suggestions")
@require_auth
def friend_suggestions():
    """"People you may know" — three tiers, same escalating-fallback
    shape as UsersAPI.suggested() in routes/users.py, so the grid is
    never empty just because someone is brand new to the platform:

    Tier 1 (mutual friends) — people who share at least one friend
    with the caller. The strongest signal, but requires the caller to
    already have friends, which most accounts on a young platform
    don't yet — that was the actual bug: a person with zero friends
    got an empty suggestions grid forever, with no way to ever get
    their first friend through this screen.

    Tier 2 (same university) — once mutual-friend candidates run out
    or the caller has no friends at all, fill from other students at
    the same campus. This is the real fix: every signed-up account
    becomes discoverable here, not just friends-of-friends.

    Tier 3 (anyone) — top up across the whole platform if a small or
    brand-new university doesn't have enough people yet.

    Throughout, excludes: the caller, existing friends, and anyone
    with a pending request already open in either direction (that
    belongs in the Requests tab, not duplicated here).
    """
    limit = min(int(request.args.get("limit", 15)), 30)
    my_ids = set(_friend_ids(g.user_id, g.token))
    pending_ids = _pending_request_ids(g.user_id, g.token)
    exclude_ids = {g.user_id} | my_ids | pending_ids

    # ---- Tier 1: mutual friends ----
    mutual_counts = {}
    for friend_id in list(my_ids)[:20]:
        their_friends = _friend_ids(friend_id, g.token)
        for candidate_id in their_friends:
            if candidate_id in exclude_ids:
                continue
            mutual_counts[candidate_id] = mutual_counts.get(candidate_id, 0) + 1

    ranked_ids = sorted(mutual_counts, key=mutual_counts.get, reverse=True)[:limit]
    exclude_ids |= set(ranked_ids)

    def fetch(params):
        data, status = rest_request("GET", "users", token=g.token, params=params)
        return data if status == 200 else []

    # ---- Tier 2: same university ----
    if len(ranked_ids) < limit:
        me, status = rest_request(
            "GET", "users", token=g.token,
            params={"select": "university_id", "id": f"eq.{g.user_id}"},
        )
        university_id = (me or [{}])[0].get("university_id") if status == 200 else None
        if university_id:
            campus_rows = fetch({
                "select": "id", "id": f"not.in.({','.join(exclude_ids)})",
                "university_id": f"eq.{university_id}",
                "order": "created_at.desc", "limit": limit - len(ranked_ids),
            })
            campus_ids = [row["id"] for row in campus_rows]
            ranked_ids += campus_ids
            exclude_ids |= set(campus_ids)

    # ---- Tier 3: anyone, top up ----
    if len(ranked_ids) < limit:
        topup_rows = fetch({
            "select": "id", "id": f"not.in.({','.join(exclude_ids)})",
            "order": "created_at.desc", "limit": limit - len(ranked_ids),
        })
        topup_ids = [row["id"] for row in topup_rows]
        ranked_ids += topup_ids

    if not ranked_ids:
        return jsonify([]), 200

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
            # Only tier 1 has a real mutual-friend count; tiers 2/3
            # deliberately omit the field rather than sending a fake 0,
            # so the frontend can choose not to render "0 mutual
            # friends" as if that were a meaningful signal.
            if cid in mutual_counts:
                shaped["mutual_friends"] = mutual_counts[cid]
            result.append(shaped)
    return jsonify(result), 200
