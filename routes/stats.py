from flask import Blueprint, jsonify, g
from lib.supabase_client import rest_request, rest_count
from lib.decorators import require_auth

bp = Blueprint("stats", __name__, url_prefix="/api/stats")


@bp.get("/public")
def public_stats():
    """Just a headline number for the landing page / admin overview —
    total registered users. No auth needed; nothing sensitive in a
    count."""
    data, status = rest_request("GET", "users", params={"select": "id"})
    count = len(data) if status == 200 and isinstance(data, list) else None
    return jsonify({"total_users": count}), 200


@bp.get("/insights")
@require_auth
def my_insights():
    """Private, own-account-only performance dashboard — never shown
    to anyone else, same distinction IG/X draw between a public
    profile and a private Insights tab. Sums up every active post's
    counters (view_count/reaction_count/comment_count/search_hit_count
    already exist on posts — nothing new tracked here) and surfaces
    the best-performing posts so a creator can see what's landing."""
    posts, status = rest_request(
        "GET", "posts", token=g.token,
        params={
            "author_id": f"eq.{g.user_id}", "status": "eq.active",
            "select": "id,content,image_url,view_count,reaction_count,comment_count,search_hit_count,created_at",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load your posts"}), status
    posts = posts or []

    total_views = sum(p["view_count"] for p in posts)
    total_reactions = sum(p["reaction_count"] for p in posts)
    total_comments = sum(p["comment_count"] for p in posts)

    for p in posts:
        p["_score"] = p["view_count"] + p["reaction_count"] * 3 + p["search_hit_count"]
    top_posts = sorted(posts, key=lambda p: p["_score"], reverse=True)[:5]
    for p in top_posts:
        p.pop("_score", None)

    follower_count, fstatus = rest_count(
        "follows", token=g.token, params={"followed_id": f"eq.{g.user_id}"},
    )

    return jsonify({
        "total_posts": len(posts),
        "total_views": total_views,
        "total_reactions": total_reactions,
        "total_comments": total_comments,
        "follower_count": follower_count if fstatus == 200 else None,
        "top_posts": top_posts,
    }), 200
