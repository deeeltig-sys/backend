from flask import Blueprint, request, jsonify, g
from lib.decorators import require_auth
from lib.supabase_client import rpc

bp = Blueprint("leaderboard", __name__, url_prefix="/api/leaderboard")


@bp.get("")
@require_auth
def leaderboard():
    # 'university' (default) or 'global' — anything else falls back to
    # university rather than erroring, since a typo'd param shouldn't
    # break the page.
    scope = request.args.get("scope", "university")
    if scope not in ("university", "global"):
        scope = "university"
    limit = min(max(request.args.get("limit", 20, type=int) or 20, 1), 50)

    data, status = rpc(
        "leaderboard_this_week", token=g.token,
        payload={"p_scope": scope, "p_limit": limit},
    )
    if status >= 400:
        return jsonify({"error": "could not load leaderboard"}), status
    return jsonify({"scope": scope, "entries": data or []}), 200
