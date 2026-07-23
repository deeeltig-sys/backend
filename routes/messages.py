from flask import Blueprint, request, jsonify, g
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


@bp.get("")
@require_auth
def list_conversations():
    """Every conversation the caller is part of, newest activity
    first. Includes status so the frontend can show pending requests
    (from others) separately from accepted threads."""
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

    result = []
    conv_ids = []
    for conv in data or []:
        other = _other_participant(conv, g.user_id)
        conv_ids.append(conv["id"])
        result.append({
            "id": conv["id"],
            "status": conv["status"],
            "is_request": conv["status"] == "pending" and conv["initiated_by"] != g.user_id,
            "last_message_at": conv["last_message_at"],
            "last_message_preview": None,
            "other_user": {
                "id": other.get("id"),
                "full_name": other.get("full_name"),
                "avatar_url": other.get("avatar_url"),
                "verified": other.get("verified_at") is not None,
            },
        })

    # One extra query for a preview snippet per conversation — PostgREST
    # has no "latest row per group" in a single call, so this pulls
    # recent messages across all the caller's conversations and keeps
    # only the newest per conversation_id in Python.
    if conv_ids:
        msgs, msg_status = rest_request(
            "GET", "messages", token=g.token,
            params={
                "conversation_id": f"in.({','.join(conv_ids)})",
                "select": "conversation_id,content,sender_id,created_at",
                "order": "created_at.desc",
                "limit": len(conv_ids) * 5,  # generous headroom, trimmed to newest-per-conversation below
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
    participant of the conversation — no extra ownership check
    needed here, a non-participant's request just comes back empty."""
    data, status = rest_request(
        "GET", "messages", token=g.token,
        params={"conversation_id": f"eq.{conversation_id}", "select": "*", "order": "created_at.asc"},
    )
    if status != 200:
        return jsonify({"error": "could not load messages"}), status
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
