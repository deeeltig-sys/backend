from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth
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


@bp.get("/suggested")
@require_auth
def suggested_users():
    """People-to-follow for the feed carousel. Same-university first
    (the people actually relevant to a campus platform), ranked by
    follower_count, excluding the caller and anyone already followed.
    Two queries instead of one — PostgREST can't subquery `follows`
    inline, so we fetch the exclusion list first."""
    limit = min(int(request.args.get("limit", 10)), 25)

    following, status = rest_request(
        "GET", "follows", token=g.token,
        params={"select": "followed_id", "follower_id": f"eq.{g.user_id}"},
    )
    if status != 200:
        return jsonify({"error": "could not load suggestions"}), status

    exclude_ids = {g.user_id, *(row["followed_id"] for row in (following or []))}
    id_filter = f"not.in.({','.join(exclude_ids)})"

    me, status = rest_request(
        "GET", "users", token=g.token,
        params={"select": "university_id", "id": f"eq.{g.user_id}"},
    )
    university_id = (me or [{}])[0].get("university_id") if status == 200 else None

    def fetch(params):
        data, status = rest_request("GET", "users", token=g.token, params=params)
        return data if status == 200 else []

    results = []
    if university_id:
        results = fetch({
            "select": "*", "id": id_filter, "university_id": f"eq.{university_id}",
            "order": "follower_count.desc", "limit": limit,
        })
    if len(results) < limit:
        # Top up with anyone else once same-university runs out — better
        # to show *someone* than an empty carousel on a small campus.
        seen = exclude_ids | {r["id"] for r in results}
        topup = fetch({
            "select": "*", "id": f"not.in.({','.join(seen)})",
            "order": "follower_count.desc", "limit": limit - len(results),
        })
        results += topup

    return jsonify([public_user_fields(row) for row in results]), 200
