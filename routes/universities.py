from flask import Blueprint, jsonify
from lib.supabase_client import rest_request

bp = Blueprint("universities", __name__, url_prefix="/api/universities")


@bp.get("")
def list_universities():
    """Public — no auth. Powers the signup dropdown, which has to be
    readable before a session exists. The `universities` table has no
    RLS on it, so this is just a thin proxy for a consistent request
    shape with the rest of the frontend rather than a security gate."""
    data, status = rest_request(
        "GET", "universities",
        params={"select": "id,name", "order": "name.asc"},
    )
    if status != 200:
        return jsonify({"error": "could not load universities"}), status
    return jsonify(data or []), 200
