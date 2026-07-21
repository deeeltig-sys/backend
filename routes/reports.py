from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth

bp = Blueprint("reports", __name__, url_prefix="/api/reports")

VALID_TARGET_TYPES = {"post", "comment", "user"}
VALID_REASONS = {
    "sexual_harassment", "tribal_harassment", "bullying",
    "personal_harassment", "false_info_defamation", "impersonation", "other",
}


@bp.post("")
@require_auth
def create_report():
    """The DB has always allowed this (reports_insert RLS policy checks
    reporter_id = auth.uid()) — there just was no route exposing it.
    Admin's existing GET/PATCH on reports (routes/admin.py) is what
    reviews whatever lands here."""
    body = request.get_json(silent=True) or {}
    target_type = body.get("target_type")
    target_id = body.get("target_id")
    reason = body.get("reason")

    if target_type not in VALID_TARGET_TYPES:
        return jsonify({"error": "invalid target_type"}), 400
    if not target_id:
        return jsonify({"error": "target_id is required"}), 400
    if reason not in VALID_REASONS:
        return jsonify({"error": "invalid reason"}), 400

    payload = {
        "reporter_id": g.user_id,
        "target_type": target_type,
        "target_id": target_id,
        "reason": reason,
    }
    data, status = rest_request(
        "POST", "reports", token=g.token, json_body=payload, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not submit report"}), status
    return jsonify(data[0] if isinstance(data, list) else data), 201
