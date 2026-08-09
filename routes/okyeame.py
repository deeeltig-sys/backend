"""
routes/okyeame.py

Two owner-only write surfaces plus one public read:
- POST /api/okyeame/announce — post as the Okyeame official account,
  via the announce_as_okyeame() SECURITY DEFINER RPC (see
  db/okyeame_migration.sql). Deliberately calls it with the caller's
  own token, same as every other RPC in this codebase — the privilege
  elevation happens inside Postgres, not by this backend holding a
  service-role key (see the constraint documented at the top of
  lib/supabase_client.py).
- GET /api/spotlights — public, CampusMEET HQ feed.
- POST /api/spotlights — owner-only, add a new spotlight entry.
- DELETE /api/spotlights/<id> — owner-only, remove one.
"""

from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request, rpc
from lib.decorators import require_owner, optional_auth

bp = Blueprint("okyeame", __name__, url_prefix="/api")


@bp.post("/okyeame/announce")
@require_owner
def announce():
    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    image_url = body.get("image_url")
    if not content:
        return jsonify({"error": "content cannot be empty"}), 400

    data, status = rpc(
        "announce_as_okyeame", token=g.token,
        payload={"p_content": content, "p_image_url": image_url},
    )
    if status >= 400:
        # The RPC raises a plain exception (not authorized / okyeame
        # account not configured / empty content) — PostgREST surfaces
        # that as the error message text, which is more useful to show
        # here than a generic failure.
        message = (data or {}).get("message") if isinstance(data, dict) else None
        return jsonify({"error": message or "could not post announcement"}), status
    return jsonify(data), 200


@bp.get("/spotlights")
@optional_auth
def list_spotlights():
    data, status = rest_request(
        "GET", "spotlights",
        params={"select": "*", "order": "created_at.desc"},
    )
    if status != 200:
        return jsonify({"error": "could not load CampusMEET HQ"}), status
    return jsonify(data), 200


@bp.post("/spotlights")
@require_owner
def create_spotlight():
    body = request.get_json(silent=True) or {}
    subject_name = (body.get("subject_name") or "").strip()
    body_text = (body.get("body") or "").strip()
    if not subject_name or not body_text:
        return jsonify({"error": "subject_name and body are required"}), 400

    payload = {
        "subject_name": subject_name,
        "subject_role": body.get("subject_role"),
        "subject_user_id": body.get("subject_user_id"),
        "photo_url": body.get("photo_url"),
        "body": body_text,
        "created_by": g.user_id,
    }
    data, status = rest_request(
        "POST", "spotlights", token=g.token, json_body=payload,
        prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not create spotlight"}), status
    return jsonify(data[0] if isinstance(data, list) else data), 200


@bp.delete("/spotlights/<spotlight_id>")
@require_owner
def delete_spotlight(spotlight_id):
    data, status = rest_request(
        "DELETE", "spotlights", token=g.token,
        params={"id": f"eq.{spotlight_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not delete spotlight"}), status
    return jsonify({"deleted": True}), 200
