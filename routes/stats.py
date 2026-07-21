from flask import Blueprint, jsonify
from lib.supabase_client import rest_request

bp = Blueprint("stats", __name__, url_prefix="/api/stats")


@bp.get("/public")
def public_stats():
    """Just a headline number for the landing page / admin overview —
    total registered users. No auth needed; nothing sensitive in a
    count."""
    data, status = rest_request("GET", "users", params={"select": "id"})
    count = len(data) if status == 200 and isinstance(data, list) else None
    return jsonify({"total_users": count}), 200
