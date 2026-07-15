from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth
from models.reaction import is_valid_reaction

bp = Blueprint("reactions", __name__, url_prefix="/api/posts/<post_id>/reactions")


@bp.post("")
@require_auth
def set_reaction(post_id):
    """One live reaction per user per post — switchable, not stackable
    (unique(post_id, user_id) in the schema). Uses PostgREST's upsert
    via Prefer: resolution=merge-duplicates, so switching Fire -> Doubt
    is a single call, not a delete-then-insert."""
    body = request.get_json(silent=True) or {}
    reaction_type = body.get("type")
    if not is_valid_reaction(reaction_type):
        return jsonify({"error": "type must be one of fire, cosign, doubt, yawa"}), 400

    payload = {"post_id": post_id, "user_id": g.user_id, "type": reaction_type}
    data, status = rest_request(
        "POST", "reactions", token=g.token, json_body=payload,
        prefer="return=representation,resolution=merge-duplicates",
    )
    if status >= 400:
        return jsonify({"error": "could not set reaction"}), status
    return jsonify(data[0] if isinstance(data, list) else data), 200


@bp.delete("")
@require_auth
def remove_reaction(post_id):
    data, status = rest_request(
        "DELETE", "reactions", token=g.token,
        params={"post_id": f"eq.{post_id}", "user_id": f"eq.{g.user_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not remove reaction"}), status
    return jsonify({"ok": True}), 200
