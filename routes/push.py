from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth
from lib.push_sender import send_web_push
from config import Config

bp = Blueprint("push", __name__, url_prefix="/api/push")


@bp.get("/vapid-public-key")
def vapid_public_key():
    """The frontend needs this to call pushManager.subscribe() — it's
    a public key by design (that's the whole point of VAPID), so no
    auth needed to read it."""
    return jsonify({"key": Config.VAPID_PUBLIC_KEY}), 200


@bp.post("/subscribe")
@require_auth
def subscribe():
    body = request.get_json(silent=True) or {}
    sub = body.get("subscription") or {}
    endpoint = sub.get("endpoint")
    keys = sub.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth_key = keys.get("auth")
    if not (endpoint and p256dh and auth_key):
        return jsonify({"error": "invalid push subscription"}), 400

    # Same endpoint can belong to a different signed-in person later
    # (shared device, browser profile switch) — merge-duplicates on
    # the unique endpoint keeps only the latest owner's row, rather
    # than erroring on a re-subscribe.
    data, status = rest_request(
        "POST", "push_subscriptions", token=g.token,
        json_body={"user_id": g.user_id, "endpoint": endpoint, "p256dh": p256dh, "auth_key": auth_key},
        prefer="resolution=merge-duplicates,return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not save push subscription"}), status
    return jsonify({"subscribed": True}), 201


@bp.delete("/subscribe")
@require_auth
def unsubscribe():
    body = request.get_json(silent=True) or {}
    endpoint = body.get("endpoint")
    if not endpoint:
        return jsonify({"error": "endpoint required"}), 400
    rest_request(
        "DELETE", "push_subscriptions", token=g.token,
        params={"endpoint": f"eq.{endpoint}", "user_id": f"eq.{g.user_id}"},
    )
    return jsonify({"subscribed": False}), 200


@bp.post("/send")
def send_push():
    """Called only by the Postgres trigger in push_migration.sql via
    pg_net — there's no signed-in user in this request at all, so this
    is gated by a shared secret instead of @require_auth. Deliberately
    touches nothing in Supabase: every piece of data it needs (which
    subscription, what message) was already looked up inside the
    trigger itself. See push_migration.sql for why it's built this
    way rather than having this endpoint query push_subscriptions
    directly."""
    if not Config.PUSH_WEBHOOK_SECRET or request.headers.get("X-Webhook-Secret") != Config.PUSH_WEBHOOK_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    sent = send_web_push(
        endpoint=body.get("endpoint"),
        p256dh=body.get("p256dh"),
        auth_key=body.get("auth"),
        title=body.get("title"),
        body_text=body.get("body"),
        url=body.get("url"),
        tag=body.get("tag"),
        require_interaction=body.get("requireInteraction", False),
    )
    return jsonify({"sent": sent}), 200
