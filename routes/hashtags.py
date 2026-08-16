from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import optional_auth
from lib.pagination import paginate_args
from routes.posts import (
    _filter_blocked,
    _filter_by_audience,
    _attach_user_reactions,
    _attach_original_posts,
    _attach_polls,
    _attach_mentions,
    _attach_images,
)

bp = Blueprint("hashtags", __name__, url_prefix="/api/hashtags")


def _normalize_tag(raw):
    """Matches the extraction trigger's own normalization (lowercase,
    no leading '#') so a lookup here always lines up with what got
    stored when the post was created."""
    return (raw or "").strip().lstrip("#").lower()


@bp.get("/trending")
def trending():
    """Backs the Explore/Search empty-state — tags with the most posts
    in the last 7 days, most active first. See db/hashtags_migration.sql
    for how `trending_hashtags` is computed."""
    limit, _ = paginate_args(default_limit=20, max_limit=50)
    data, status = rest_request(
        "GET", "trending_hashtags",
        params={"select": "tag,post_count,recent_count", "limit": limit},
    )
    if status != 200:
        return jsonify({"error": "could not load trending tags"}), status
    return jsonify(data or []), 200


@bp.get("/<tag>/posts")
@optional_auth
def posts_for_hashtag(tag):
    """Every active post carrying this tag, newest first. Same
    post_id -> feed lookup pattern as PostsAPI.saved() in posts.py,
    since `feed` is a view (no direct FK PostgREST could embed
    post_hashtags through) rather than a plain table."""
    clean = _normalize_tag(tag)
    if not clean:
        return jsonify({"error": "tag required"}), 400

    hashtag, hstatus = rest_request(
        "GET", "hashtags", params={"tag": f"eq.{clean}", "select": "id,tag,post_count"},
    )
    if hstatus != 200:
        return jsonify({"error": "could not resolve tag"}), hstatus
    if not hashtag:
        return jsonify({"tag": clean, "post_count": 0, "posts": []}), 200

    limit, _ = paginate_args(default_limit=50, max_limit=100)
    links, lstatus = rest_request(
        "GET", "post_hashtags",
        params={
            "hashtag_id": f"eq.{hashtag[0]['id']}",
            "select": "post_id,created_at",
            "order": "created_at.desc",
            "limit": limit,
        },
    )
    if lstatus != 200:
        return jsonify({"error": "could not load tagged posts"}), lstatus
    if not links:
        return jsonify({"tag": clean, "post_count": hashtag[0]["post_count"], "posts": []}), 200

    post_ids = [l["post_id"] for l in links]
    posts_data, pstatus = rest_request(
        "GET", "feed", params={"id": f"in.({','.join(post_ids)})", "select": "*"},
    )
    if pstatus != 200:
        return jsonify({"error": "could not load tagged posts"}), pstatus

    by_id = {p["id"]: p for p in (posts_data or [])}
    ordered = [by_id[pid] for pid in post_ids if pid in by_id]  # preserves tagged-order, not feed's random order

    ordered = _filter_blocked(ordered, g.token)
    ordered = _filter_by_audience(ordered, g.user_id, g.token)
    _attach_user_reactions(ordered, g.token, g.user_id)
    _attach_original_posts(ordered, g.token)
    _attach_polls(ordered, g.token)
    _attach_mentions(ordered, g.token)
    _attach_images(ordered, g.token)

    return jsonify({"tag": clean, "post_count": hashtag[0]["post_count"], "posts": ordered}), 200
