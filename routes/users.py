from flask import Blueprint, request, jsonify
from lib.supabase_client import rest_request
from models.user import public_user_fields

bp = Blueprint("users", __name__, url_prefix="/api/users")


@bp.get("/search")
def search_users():
    """Simple ILIKE search over full_name — same shape as
    posts.search_posts, but over students instead of posts. This is
    what makes Follow actually usable: without it there's no way to
    find a specific person beyond stumbling on their posts."""
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify([]), 200
    if len(query) < 2:
        return jsonify({"error": "type at least 2 characters to search"}), 400

    limit = request.args.get("limit", 20)
    data, status = rest_request(
        "GET", "users",
        params={
            "select": "*",
            "full_name": f"ilike.*{query}*",
            "order": "follower_count.desc",
            "limit": limit,
        },
    )
    if status != 200:
        return jsonify({"error": "search failed"}), status
    return jsonify([public_user_fields(row) for row in (data or [])]), 200
