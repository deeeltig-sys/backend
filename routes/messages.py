import uuid

from flask import Blueprint, request, jsonify, g
from datetime import datetime, timezone, timedelta
from lib.supabase_client import rest_request, rpc, storage_upload_private, storage_create_signed_url, storage_delete_object
from lib.decorators import require_auth
from lib.limiter import limiter
from models.reaction import is_valid_reaction

bp = Blueprint("messages", __name__, url_prefix="/api/conversations")

# lib/limiter.py's default_limits=["200 per hour"] is sized for
# auth.py's abuse-prevention routes (signup/login/forgot-password) —
# it was never meant to cover this blueprint, but with no exemption it
# silently also caps every read here, keyed by IP. A single open chat
# already spends that whole budget on its own polling fallback +
# typing indicator alone (4s message poll = 900/hr, 3s typing poll =
# 1200/hr) before a single message is even sent, and it's per-IP, not
# per-user — two people testing behind the same router or campus WiFi
# NAT share one 200/hour bucket. Exempting the blueprint removes that
# ceiling; the handful of endpoints below that genuinely warrant their
# own cap (creating conversations, sending messages) get an explicit,
# chat-appropriate limit instead — an exempted blueprint can still
# carry per-route @limiter.limit() decorators, only the blanket
# default stops auto-applying.
limiter.exempt(bp)

# Voice notes are recorded client-side as Opus/webm (MediaRecorder's
# native output) — no server-side transcoding. This backend has no
# ffmpeg/audio toolchain (see requirements.txt — Pillow is for images
# only), and Opus at the browser's default bitrate is already small
# enough for the data-cost-conscious audience this is built for, so
# adding one would be pure extra infra for no real gain.
MAX_VOICE_BYTES = 8 * 1024 * 1024  # 8MB — generous headroom above a realistic ~2min note
ALLOWED_VOICE_CONTENT_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg"}
VOICE_SIGNED_URL_TTL_SECONDS = 3600

# Storage lifecycle for voice notes — one of TWO layers, both aimed
# at the same 5-day cutoff:
#   1. THIS lazy path — piggybacks on opening a thread (this
#      endpoint), zero privilege needed, catches active conversations
#      instantly.
#   2. A bounded, batched pg_cron sweep living entirely in Postgres
#      (db/voice_note_lifecycle_scale_migration.sql) — the guarantee
#      layer, for conversations nobody reopens. That one DOES use a
#      privileged credential, but it's stored in Supabase Vault, never
#      here — this Flask backend still never holds a service-role key
#      (see supabase_client.py's module docstring), that invariant is
#      unchanged.
# Both are safe to run together — whichever fires first clears
# voice_path, the other one just finds nothing left to do.
# 5 days is short on purpose — the phone-side cache (see
# components/VoiceMessage.jsx) is what makes playback still feel
# instant within that window; after it, the note is genuinely gone
# from Supabase, same as it'll fall out of the phone cache too once
# the 70MB per-user cap evicts it.
VOICE_NOTE_EXPIRY_DAYS = 5


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
                      "user_a_info:users!conversations_user_a_fkey(id,full_name,avatar_url,verified_at,last_seen_at),"
                      "user_b_info:users!conversations_user_b_fkey(id,full_name,avatar_url,verified_at,last_seen_at)",
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
                "last_seen_at": other.get("last_seen_at"),
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

        # Per-conversation unread count — a dedicated query rather than
        # reusing the latest-messages batch above, since that batch is
        # Per-conversation unread count — a dedicated query rather than
        # reusing the latest-messages batch above, since that batch is
        # capped and not guaranteed to hold every unread message in a
        # conversation with a long unread backlog. `messages` has no
        # recipient_id column (that's only on `notifications`) — the
        # recipient of a DM is just "whoever didn't send it", so unread
        # is sender_id != me AND read_at is null, scoped to my own
        # conversation ids (RLS also enforces that boundary).
        unread_data, unread_status = rest_request(
            "GET", "messages", token=g.token,
            params={
                "conversation_id": f"in.({','.join(result_ids)})",
                "sender_id": f"neq.{g.user_id}",
                "read_at": "is.null",
                "select": "conversation_id",
            },
        )
        if unread_status == 200:
            unread_counts = {}
            for m in unread_data or []:
                cid = m["conversation_id"]
                unread_counts[cid] = unread_counts.get(cid, 0) + 1
            for row in result:
                row["unread_count"] = unread_counts.get(row["id"], 0)
        else:
            for row in result:
                row["unread_count"] = 0
    return jsonify(result), 200


@bp.get("/unread-count")
@require_auth
def unread_message_count():
    """Get count of unread messages received by the current user.

    Returns: {"count": N}

    A message is unread if:
    1. The current user is the recipient (not the sender)
    2. The message has no read_at timestamp

    NOTE: this used to filter on messages.recipient_id, which doesn't
    exist on this table (only notifications has that column) — every
    call was silently erroring, which is why the Chats badge in
    BottomNav.jsx never actually lit up. Fixed to derive "recipient"
    the same way the rest of this file does: whoever didn't send it,
    scoped to conversations the caller is actually part of.
    """
    conv_data, conv_status = rest_request(
        "GET", "conversations", token=g.token,
        params={"or": f"(user_a.eq.{g.user_id},user_b.eq.{g.user_id})", "select": "id"},
    )
    if conv_status != 200:
        return jsonify({"error": "could not load unread count"}), conv_status
    conv_ids = [c["id"] for c in conv_data or []]
    if not conv_ids:
        return jsonify({"count": 0}), 200

    data, status = rest_request(
        "GET", "messages", token=g.token,
        params={
            "conversation_id": f"in.({','.join(conv_ids)})",
            "sender_id": f"neq.{g.user_id}",
            "read_at": "is.null",
            "select": "id",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load unread count"}), status
    
    return jsonify({"count": len(data or [])}), 200


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
@limiter.limit("30 per hour")  # starting new conversations — generous for real use, cheap to abuse otherwise since this blueprint is exempt from the global default
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


def _expire_old_voice_notes(conversation_id: str, messages: list, token: str) -> None:
    """Best-effort — never lets a cleanup failure break the actual
    message list response, this is a nice-to-have running piggyback
    on a real request, not a critical path. Deletes the storage
    object (reclaims the billed bytes — see storage_delete_object's
    docstring on why a raw SQL delete wouldn't) and clears the voice
    columns on the message row, replacing it with a plain-text
    placeholder so the conversation history stays coherent instead of
    leaving a dead/broken voice bubble."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=VOICE_NOTE_EXPIRY_DAYS)).isoformat()
    for m in messages:
        if m.get("type") != "voice" or not m.get("voice_path"):
            continue
        if not m.get("created_at") or m["created_at"] >= cutoff:
            continue
        try:
            storage_delete_object("voice-notes", m["voice_path"], token)
            rest_request(
                "PATCH", "messages", token=token,
                params={"id": f"eq.{m['id']}"},
                json_body={
                    "voice_path": None, "voice_duration_ms": None, "voice_waveform": None,
                    "content": "🎤 Voice message (expired)",
                },
            )
            # Reflect it in the response we're about to send back, so
            # the client doesn't try to play a file that's already gone.
            m["voice_path"] = None
            m["voice_duration_ms"] = None
            m["voice_waveform"] = None
            m["content"] = "🎤 Voice message (expired)"
        except Exception:
            pass  # this message's voice note just stays around until the next thread-open retries it


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

    params = {
        "conversation_id": f"eq.{conversation_id}",
        "select": "*,message_reactions(user_id,emoji)",
        "order": "created_at.asc",
    }
    if cleared_before:
        params["created_at"] = f"gt.{cleared_before}"

    data, status = rest_request("GET", "messages", token=g.token, params=params)
    if status != 200:
        return jsonify({"error": "could not load messages"}), status

    if isinstance(data, list) and data:
        _expire_old_voice_notes(conversation_id, data, g.token)

    now_iso = datetime.now(timezone.utc).isoformat()

    # Opening the thread is itself proof the message reached this
    # device, so stamp delivered_at first — covers anyone who never
    # got the live realtime delivery ping (see POST /messages/delivered
    # below), e.g. they were offline when it was sent. Kept as its own
    # PATCH (not merged into the read_at one) so a message that WAS
    # already delivered earlier keeps its real, earlier delivered_at
    # instead of being overwritten to "just now".
    rest_request(
        "PATCH", "messages", token=g.token,
        params={"conversation_id": f"eq.{conversation_id}", "sender_id": f"neq.{g.user_id}", "delivered_at": "is.null"},
        json_body={"delivered_at": now_iso},
    )
    # Mark incoming (not-mine) unread messages as read now that this
    # user has actually opened the thread.
    rest_request(
        "PATCH", "messages", token=g.token,
        params={"conversation_id": f"eq.{conversation_id}", "sender_id": f"neq.{g.user_id}", "read_at": "is.null"},
        json_body={"read_at": now_iso},
    )
    return jsonify(data or []), 200


@bp.post("/<conversation_id>/messages")
@require_auth
@limiter.limit("120 per hour")  # ~2/minute sustained — flood/spam guard, well above real typing+sending speed
def send_message(conversation_id):
    """RLS (messages_insert_own) is what actually enforces the
    message-request gate — a non-initiator can't insert here until
    they've called /accept first. A blocked insert comes back as a
    plain RLS-denial error from PostgREST, which reads a little
    cryptic, so it's translated into something a student would
    actually understand.

    type='sticker' is handled here (no file, just a client-known
    preset id — see frontend's sticker pack). type='voice' is NOT
    handled here — voice notes carry an audio file and go through the
    dedicated multipart endpoint below."""
    body = request.get_json(silent=True) or {}
    msg_type = body.get("type") or "text"

    if msg_type == "sticker":
        sticker_id = (body.get("sticker_id") or "").strip()
        if not sticker_id or len(sticker_id) > 60:
            return jsonify({"error": "sticker_id is required"}), 400
        payload = {
            "conversation_id": conversation_id, "sender_id": g.user_id,
            "type": "sticker", "sticker_id": sticker_id,
            # content still needs to satisfy the NOT NULL/length check
            # on the column, and doubles as the Inbox list preview text
            # ("Sent a sticker") the same way WhatsApp/IG show a
            # placeholder line for non-text messages.
            "content": "Sent a sticker",
        }
    elif msg_type == "text":
        content = (body.get("content") or "").strip()
        if not content or len(content) > 2000:
            return jsonify({"error": "message must be 1-2000 characters"}), 400
        payload = {"conversation_id": conversation_id, "sender_id": g.user_id, "content": content}
    else:
        return jsonify({"error": "type must be 'text' or 'sticker' (use the voice endpoint for voice notes)"}), 400

    data, status = rest_request(
        "POST", "messages", token=g.token,
        json_body=payload,
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


@bp.post("/<conversation_id>/typing")
@require_auth
def set_typing(conversation_id):
    """Called on keystroke (debounced client-side) with {typing: true},
    and once on blur/send/close with {typing: false}. Cheap by design —
    just an upsert of a timestamp, no new tables beyond one column."""
    body = request.get_json(silent=True) or {}
    typing = bool(body.get("typing"))
    data, status = rpc(
        "set_typing", token=g.token,
        payload={"p_conversation_id": conversation_id, "p_typing": typing},
    )
    if status >= 400:
        return jsonify({"error": "could not update typing status"}), status
    return jsonify({"typing": typing}), 200


@bp.get("/<conversation_id>/typing")
@require_auth
def get_typing(conversation_id):
    """Polled every few seconds while a thread is open — true if the
    OTHER participant pinged /typing in roughly the last 6 seconds."""
    data, status = rpc(
        "get_other_typing", token=g.token,
        payload={"p_conversation_id": conversation_id},
    )
    if status >= 400:
        return jsonify({"error": "could not load typing status"}), status
    return jsonify({"typing": bool(data)}), 200


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


@bp.post("/<conversation_id>/messages/voice")
@require_auth
@limiter.limit("60 per hour")  # voice notes cost real storage on every send, tighter than text
def send_voice_message(conversation_id):
    """Uploads a recorded voice note straight to the private
    `voice-notes` bucket (path "{conversation_id}/{uuid}.{ext}") and
    creates the message row in one call. Same RLS-gated,
    no-service-role pattern as posts.py's upload_image — the caller's
    own JWT is what the storage write actually runs as, so
    voice_notes_storage_migration.sql's participant-only policy is
    what enforces this can't be uploaded into a conversation the
    caller isn't part of."""
    if "audio" not in request.files:
        return jsonify({"error": "attach a recording under the 'audio' field"}), 400

    file = request.files["audio"]
    content_type = (file.mimetype or "audio/webm").split(";")[0].strip()
    if content_type not in ALLOWED_VOICE_CONTENT_TYPES:
        return jsonify({"error": "unsupported audio format"}), 400

    file_bytes = file.read()
    if not file_bytes:
        return jsonify({"error": "recording is empty"}), 400
    if len(file_bytes) > MAX_VOICE_BYTES:
        return jsonify({"error": "voice note is too large (max 8MB)"}), 400

    try:
        duration_ms = int(request.form.get("duration_ms", 0))
    except (TypeError, ValueError):
        duration_ms = 0
    if duration_ms <= 0 or duration_ms > 10 * 60 * 1000:  # sanity cap: 10 minutes
        return jsonify({"error": "invalid duration_ms"}), 400

    # Precomputed amplitude samples from the client's own recording
    # analysis (Web Audio API) — stored once, so playback never has to
    # re-analyze the audio on a low-end device. Optional: falls back to
    # a flat waveform client-side if omitted, this just skips storing one.
    waveform_raw = request.form.get("waveform")
    waveform = None
    if waveform_raw:
        try:
            import json as _json
            parsed = _json.loads(waveform_raw)
            if isinstance(parsed, list) and len(parsed) <= 200:
                waveform = [float(v) for v in parsed]
        except (ValueError, TypeError):
            waveform = None  # malformed waveform data — drop it, don't fail the whole upload over a cosmetic field

    extension = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3"}[content_type]
    path = f"{conversation_id}/{uuid.uuid4().hex}.{extension}"

    upload_data, status = storage_upload_private("voice-notes", path, file_bytes, content_type, g.token)
    if status >= 400:
        return jsonify({"error": "voice note upload failed, try again"}), status

    data, status = rest_request(
        "POST", "messages", token=g.token,
        json_body={
            "conversation_id": conversation_id, "sender_id": g.user_id,
            "type": "voice", "content": "🎤 Voice message",
            "voice_path": upload_data["path"], "voice_duration_ms": duration_ms,
            "voice_waveform": waveform,
        },
        prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "you need to accept this conversation before replying"}), status
    return jsonify(data[0] if isinstance(data, list) else data), 201


@bp.get("/<conversation_id>/messages/<message_id>/voice-url")
@require_auth
def get_voice_url(conversation_id, message_id):
    """Signs a short-lived playback URL for a voice note. Fetches the
    message row through the caller's own token first — messages_select_own
    RLS means this 404s (as 'not found', not a 403) for anyone who
    isn't actually a participant in the conversation, same as every
    other message read in this file."""
    data, status = rest_request(
        "GET", "messages", token=g.token,
        params={"id": f"eq.{message_id}", "conversation_id": f"eq.{conversation_id}", "select": "voice_path,type"},
    )
    if status != 200 or not data:
        return jsonify({"error": "voice note not found"}), 404
    row = data[0]
    if row.get("type") != "voice" or not row.get("voice_path"):
        return jsonify({"error": "this message has no voice note"}), 400

    signed, sign_status = storage_create_signed_url("voice-notes", row["voice_path"], g.token, VOICE_SIGNED_URL_TTL_SECONDS)
    if sign_status >= 400:
        return jsonify({"error": "could not load voice note"}), sign_status
    return jsonify({"url": signed["url"], "expires_in": VOICE_SIGNED_URL_TTL_SECONDS}), 200


@bp.post("/<conversation_id>/messages/delivered")
@require_auth
def mark_delivered(conversation_id):
    """Batch delivery ack — called by the frontend the INSTANT a new
    message reaches the recipient's client (realtime INSERT event via
    useIncomingMessages, see hooks/useIncomingMessages.js), regardless
    of whether the thread is even open. This is what makes the double
    tick appear as soon as the message reaches the receiver rather
    than waiting for them to open the chat (that's what read_at is
    for). Idempotent (delivered_at is.null filter) and safe to call
    for messages that are already delivered or don't belong to this
    conversation — those rows just match zero rows and no-op."""
    body = request.get_json(silent=True) or {}
    message_ids = body.get("message_ids")
    if not isinstance(message_ids, list) or not message_ids:
        return jsonify({"error": "message_ids must be a non-empty list"}), 400
    if len(message_ids) > 100:
        message_ids = message_ids[:100]  # sane cap — this is a per-batch ping, not a bulk backfill tool

    ids_csv = ",".join(str(mid) for mid in message_ids)
    rest_request(
        "PATCH", "messages", token=g.token,
        params={
            "conversation_id": f"eq.{conversation_id}",
            "id": f"in.({ids_csv})",
            "sender_id": f"neq.{g.user_id}",
            "delivered_at": "is.null",
        },
        json_body={"delivered_at": datetime.now(timezone.utc).isoformat()},
    )
    return jsonify({"ok": True}), 200


@bp.post("/<conversation_id>/messages/<message_id>/react")
@require_auth
def react_to_message(conversation_id, message_id):
    """One live reaction per user per message — same upsert-via-
    on_conflict pattern as routes/reactions.py's post reactions, and
    reuses the exact same emoji vocabulary (like/fire/cosign/yawa)
    rather than a separate chat-only reaction set."""
    body = request.get_json(silent=True) or {}
    emoji = body.get("emoji")
    if not is_valid_reaction(emoji):
        return jsonify({"error": "emoji must be one of like, fire, cosign, yawa"}), 400

    data, status = rest_request(
        "POST", "message_reactions", token=g.token,
        json_body={"message_id": message_id, "user_id": g.user_id, "emoji": emoji},
        params={"on_conflict": "message_id,user_id"},
        prefer="return=representation,resolution=merge-duplicates",
    )
    if status >= 400:
        return jsonify({"error": "could not react to this message"}), status
    return jsonify(data[0] if isinstance(data, list) else data), 200


@bp.delete("/<conversation_id>/messages/<message_id>/react")
@require_auth
def remove_message_reaction(conversation_id, message_id):
    data, status = rest_request(
        "DELETE", "message_reactions", token=g.token,
        params={"message_id": f"eq.{message_id}", "user_id": f"eq.{g.user_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not remove reaction"}), status
    return jsonify({"ok": True}), 200
