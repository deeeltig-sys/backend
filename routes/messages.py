from flask import Blueprint, request, jsonify, g
from datetime import datetime, timezone
from lib.supabase_client import rest_request, rpc
from lib.decorators import require_auth

bp = Blueprint("messages", __name__, url_prefix="/api/conversations")


def _other_participant(conv: dict, me: str) -> dict:
    """conversations rows carry both participants embedded — pick out
    whichever one isn't the caller, since the frontend only cares
    'who am I talking to', not the raw user_a/user_b ordering."""
    if conv.get("user_a") == me:
        return conv.get("user_b_info") or {}
    return conv.get("user_a_info") or {}


def _my_states(conv_ids, token):
    """One query for every conversation_user_state row belonging to the
    caller, across all their conversations — used to attach hidden/
    deleted/wallpaper flags without a round-trip per conversation."""
    if not conv_ids:
        return {}
    data, status = rest_request(
        "GET", "conversation_user_state", token=token,
        params={
            "conversation_id": f"in.({','.join(conv_ids)})",
            "user_id": f"eq.{g.user_id}",
            "select": "*",
        },
    )
    if status != 200:
        return {}
    return {row["conversation_id"]: row for row in (data or [])}


def _blocked_user_ids(token):
    """Everyone the caller has blocked OR who has blocked the caller —
    either direction hides the conversation from the normal list."""
    data, status = rest_request(
        "GET", "blocks", token=token,
        params={"or": f"(blocker_id.eq.{g.user_id},blocked_id.eq.{g.user_id})", "select": "blocker_id,blocked_id"},
    )
    if status != 200:
        return set()
    ids = set()
    for row in data or []:
        ids.add(row["blocker_id"])
        ids.add(row["blocked_id"])
    ids.discard(g.user_id)
    return ids


@bp.get("")
@require_auth
def list_conversations():
    """Every conversation the caller is part of. `filter` selects which
    bucket: default (active, not hidden/deleted/blocked), or one of
    hidden / blocked / requests / deleted — matching the Chat options
    menu's four tabs."""
    filter_name = request.args.get("filter", "active")

    data, status = rest_request(
        "GET", "conversations", token=g.token,
        params={
            "or": f"(user_a.eq.{g.user_id},user_b.eq.{g.user_id})",
            "select": "id,user_a,user_b,status,initiated_by,last_message_at,"
                      "user_a_info:users!conversations_user_a_fkey(id,full_name,avatar_url,verified_at),"
                      "user_b_info:users!conversations_user_b_fkey(id,full_name,avatar_url,verified_at)",
            "order": "last_message_at.desc",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load conversations"}), status

    conv_ids = [c["id"] for c in (data or [])]
    states = _my_states(conv_ids, g.token)
    blocked_ids = _blocked_user_ids(g.token) if filter_name in ("active", "blocked") else set()

    # Settings' default wallpaper — used whenever a specific conversation
    # hasn't set its own override via the in-chat wallpaper picker.
    me, me_status = rest_request(
        "GET", "users", token=g.token,
        params={"id": f"eq.{g.user_id}", "select": "default_wallpaper,default_wallpaper_url"},
    )
    default_wallpaper = (me or [{}])[0].get("default_wallpaper", "system") if me_status == 200 else "system"
    default_wallpaper_url = (me or [{}])[0].get("default_wallpaper_url") if me_status == 200 else None

    result = []
    for conv in data or []:
        other = _other_participant(conv, g.user_id)
        state = states.get(conv["id"], {})
        is_hidden = state.get("hidden_at") is not None
        is_deleted = state.get("deleted_at") is not None
        is_blocked = other.get("id") in blocked_ids
        is_request = conv["status"] == "pending" and conv["initiated_by"] != g.user_id

        if filter_name == "active" and (is_hidden or is_deleted or is_blocked or is_request):
            continue
        if filter_name == "hidden" and not is_hidden:
            continue
        if filter_name == "deleted" and not is_deleted:
            continue
        if filter_name == "blocked" and not is_blocked:
            continue
        if filter_name == "requests" and not is_request:
            continue

        result.append({
            "id": conv["id"],
            "status": conv["status"],
            "is_request": is_request,
            "last_message_at": conv["last_message_at"],
            "last_message_preview": None,
            "wallpaper": state.get("wallpaper") or default_wallpaper,
            "custom_wallpaper_url": state.get("custom_wallpaper_url") or (default_wallpaper_url if not state.get("wallpaper") else None),
            "deleted_at": state.get("deleted_at"),
            "other_user": {
                "id": other.get("id"),
                "full_name": other.get("full_name"),
                "avatar_url": other.get("avatar_url"),
                "verified": other.get("verified_at") is not None,
            },
        })

    result_ids = [r["id"] for r in result]
    if result_ids:
        msgs, msg_status = rest_request(
            "GET", "messages", token=g.token,
            params={
                "conversation_id": f"in.({','.join(result_ids)})",
                "select": "conversation_id,content,sender_id,created_at,read_at",
                "order": "created_at.desc",
                "limit": len(result_ids) * 5,
            },
        )
        if msg_status == 200:
            seen = set()
            latest_by_conv = {}
            for m in msgs or []:
                cid = m["conversation_id"]
                if cid not in seen:
                    seen.add(cid)
                    latest_by_conv[cid] = m
            for row in result:
                m = latest_by_conv.get(row["id"])
                if m:
                    prefix = "You: " if m["sender_id"] == g.user_id else ""
                    snippet = m["content"][:60] + ("…" if len(m["content"]) > 60 else "")
                    row["last_message_preview"] = prefix + snippet
                    # Receipt status only makes sense for messages the
                    # caller SENT — seeing "read" on the other person's
                    # own message to you isn't a receipt, it's just noise.
                    if m["sender_id"] == g.user_id:
                        row["last_message_status"] = "read" if m.get("read_at") else "sent"
                    else:
                        row["last_message_status"] = None
    return jsonify(result), 200


@bp.get("/active-contacts")
@require_auth
def active_contacts():
    """People-you're-chatting-with strip for the top of the Chats page —
    the N most recently active accepted conversations, avatar-only.
    'Active' here means 'recently talked to', not real-time presence —
    there's no websocket/heartbeat infra to know who's online right
    now, and faking that would be misleading."""
    limit = min(int(request.args.get("limit", 12)), 25)
    data, status = rest_request(
        "GET", "conversations", token=g.token,
        params={
            "or": f"(user_a.eq.{g.user_id},user_b.eq.{g.user_id})",
            "status": "eq.accepted",
            "select": "id,user_a,user_b,last_message_at,"
                      "user_a_info:users!conversations_user_a_fkey(id,full_name,avatar_url,verified_at),"
                      "user_b_info:users!conversations_user_b_fkey(id,full_name,avatar_url,verified_at)",
            "order": "last_message_at.desc",
            "limit": limit,
        },
    )
    if status != 200:
        return jsonify({"error": "could not load contacts"}), status

    conv_ids = [c["id"] for c in (data or [])]
    states = _my_states(conv_ids, g.token)
    blocked_ids = _blocked_user_ids(g.token)

    result = []
    for conv in data or []:
        state = states.get(conv["id"], {})
        if state.get("hidden_at") or state.get("deleted_at"):
            continue
        other = _other_participant(conv, g.user_id)
        if other.get("id") in blocked_ids:
            continue
        result.append({
            "conversation_id": conv["id"],
            "id": other.get("id"),
            "full_name": other.get("full_name"),
            "avatar_url": other.get("avatar_url"),
            "verified": other.get("verified_at") is not None,
        })
    return jsonify(result), 200


@bp.post("")
@require_auth
def start_conversation():
    body = request.get_json(silent=True) or {}
    other_user_id = body.get("user_id")
    if not other_user_id:
        return jsonify({"error": "user_id is required"}), 400

    data, status = rpc("start_conversation", token=g.token, payload={"p_other_user_id": other_user_id})
    if status >= 400:
        msg = (data or {}).get("message") or "could not start conversation"
        return jsonify({"error": msg}), status
    return jsonify({"conversation_id": data}), 201


@bp.post("/<conversation_id>/accept")
@require_auth
def accept_conversation(conversation_id):
    data, status = rpc("accept_conversation", token=g.token, payload={"p_conversation_id": conversation_id})
    if status >= 400:
        return jsonify({"error": "could not accept conversation"}), status
    return jsonify({"ok": True}), 200


@bp.get("/<conversation_id>/messages")
@require_auth
def list_messages(conversation_id):
    """RLS (messages_select_own) already restricts this to a
    participant of the conversation. Also respects this user's own
    cleared_before marker (from "clear chat") and marks every message
    the caller received as read — opening the thread IS the read
    receipt trigger, same as WhatsApp/Messenger."""
    states = _my_states([conversation_id], g.token)
    cleared_before = (states.get(conversation_id) or {}).get("cleared_before")

    params = {"conversation_id": f"eq.{conversation_id}", "select": "*", "order": "created_at.asc"}
    if cleared_before:
        params["created_at"] = f"gt.{cleared_before}"

    data, status = rest_request("GET", "messages", token=g.token, params=params)
    if status != 200:
        return jsonify({"error": "could not load messages"}), status

    # Mark incoming (not-mine) unread messages as read now that this
    # user has actually opened the thread.
    rest_request(
        "PATCH", "messages", token=g.token,
        params={"conversation_id": f"eq.{conversation_id}", "sender_id": f"neq.{g.user_id}", "read_at": "is.null"},
        json_body={"read_at": datetime.now(timezone.utc).isoformat()},
    )
    return jsonify(data or []), 200


@bp.post("/<conversation_id>/messages")
@require_auth
def send_message(conversation_id):
    """RLS (messages_insert_own) is what actually enforces the
    message-request gate — a non-initiator can't insert here until
    they've called /accept first. A blocked insert comes back as a
    plain RLS-denial error from PostgREST, which reads a little
    cryptic, so it's translated into something a student would
    actually understand."""
    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content or len(content) > 2000:
        return jsonify({"error": "message must be 1-2000 characters"}), 400

    data, status = rest_request(
        "POST", "messages", token=g.token,
        json_body={"conversation_id": conversation_id, "sender_id": g.user_id, "content": content},
        prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "you need to accept this conversation before replying"}), status
    return jsonify(data[0] if isinstance(data, list) else data), 201


def _set_state(conversation_id, **kwargs):
    payload = {"p_conversation_id": conversation_id, **kwargs}
    data, status = rpc("set_conversation_state", token=g.token, payload=payload)
    return status < 400


@bp.post("/<conversation_id>/hide")
@require_auth
def hide_conversation(conversation_id):
    ok = _set_state(conversation_id, p_hidden_at=datetime.now(timezone.utc).isoformat())
    if not ok:
        return jsonify({"error": "could not hide conversation"}), 500
    return jsonify({"hidden": True}), 200


@bp.post("/<conversation_id>/unhide")
@require_auth
def unhide_conversation(conversation_id):
    ok = _set_state(conversation_id, p_clear_hidden=True)
    if not ok:
        return jsonify({"error": "could not unhide conversation"}), 500
    return jsonify({"hidden": False}), 200


@bp.post("/<conversation_id>/delete")
@require_auth
def delete_conversation(conversation_id):
    """Soft delete — moves it to 'Recent Deletes' for THIS user only,
    recoverable for 60 days (see purge_expired_deleted_conversations).
    Never touches the other participant's copy or the actual messages."""
    ok = _set_state(conversation_id, p_deleted_at=datetime.now(timezone.utc).isoformat())
    if not ok:
        return jsonify({"error": "could not delete conversation"}), 500
    return jsonify({"deleted": True}), 200


@bp.post("/<conversation_id>/restore")
@require_auth
def restore_conversation(conversation_id):
    ok = _set_state(conversation_id, p_clear_deleted=True)
    if not ok:
        return jsonify({"error": "could not restore conversation"}), 500
    return jsonify({"deleted": False}), 200


@bp.post("/<conversation_id>/clear")
@require_auth
def clear_conversation(conversation_id):
    """Erases this user's view of every message up to now — no
    recovery, per spec. The other participant's view is untouched
    since this only ever sets a per-user marker, never deletes rows."""
    ok = _set_state(conversation_id, p_cleared_before=datetime.now(timezone.utc).isoformat())
    if not ok:
        return jsonify({"error": "could not clear chat"}), 500
    return jsonify({"cleared": True}), 200


@bp.patch("/<conversation_id>/wallpaper")
@require_auth
def set_wallpaper(conversation_id):
    body = request.get_json(silent=True) or {}
    wallpaper = body.get("wallpaper")
    custom_url = body.get("custom_wallpaper_url")
    valid = {"black", "white", "system", "cream", "green", "custom"}
    if wallpaper not in valid:
        return jsonify({"error": f"wallpaper must be one of {sorted(valid)}"}), 400
    if wallpaper == "custom" and not custom_url:
        return jsonify({"error": "custom_wallpaper_url is required when wallpaper is 'custom'"}), 400

    ok = _set_state(conversation_id, p_wallpaper=wallpaper, p_custom_wallpaper_url=custom_url)
    if not ok:
        return jsonify({"error": "could not set wallpaper"}), 500
    return jsonify({"wallpaper": wallpaper, "custom_wallpaper_url": custom_url}), 200
