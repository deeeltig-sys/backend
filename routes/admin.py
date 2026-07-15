from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request, rpc
from lib.decorators import require_staff

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.get("/users")
@require_staff
def list_users():
    """Powers the admin dashboard's user list. ?verified=false shows
    the pending queue — everyone who signed up but hasn't been
    manually verified yet."""
    params = {"select": "*", "order": "created_at.desc"}
    verified_filter = request.args.get("verified")
    if verified_filter == "false":
        params["verified_at"] = "is.null"
    elif verified_filter == "true":
        params["verified_at"] = "not.is.null"

    data, status = rest_request("GET", "users", token=g.token, params=params)
    if status != 200:
        return jsonify({"error": "could not load users"}), status
    return jsonify(data), 200


@bp.post("/users/<user_id>/verify")
@require_staff
def verify_user(user_id):
    """The 'Verify USTED' button. Goes through the verify_student RPC,
    not a raw UPDATE, so verified_by is set server-side to the actual
    admin's own id and can't be spoofed."""
    data, status = rpc("verify_student", token=g.token, payload={"p_user_id": user_id})
    if status >= 400:
        return jsonify({"error": "verification failed"}), status
    return jsonify({"ok": True}), 200


@bp.post("/users/<user_id>/unverify")
@require_staff
def unverify_user(user_id):
    data, status = rpc("unverify_student", token=g.token, payload={"p_user_id": user_id})
    if status >= 400:
        return jsonify({"error": "unverify failed"}), status
    return jsonify({"ok": True}), 200


@bp.get("/reports")
@require_staff
def list_reports():
    data, status = rest_request(
        "GET", "reports", token=g.token, params={"select": "*", "order": "created_at.desc"},
    )
    if status != 200:
        return jsonify({"error": "could not load reports"}), status
    return jsonify(data), 200


@bp.patch("/reports/<report_id>")
@require_staff
def update_report(report_id):
    body = request.get_json(silent=True) or {}
    status_val = body.get("status")
    if status_val not in ("pending", "reviewed", "actioned"):
        return jsonify({"error": "status must be pending, reviewed, or actioned"}), 400

    data, status = rest_request(
        "PATCH", "reports", token=g.token,
        params={"id": f"eq.{report_id}"}, json_body={"status": status_val},
        prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "update failed"}), status
    return jsonify(data[0] if data else {}), 200
