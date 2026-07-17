from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth
from models.comment import validate_comment_content

bp = Blueprint("comments", __name__, url_prefix="/api/posts/<post_id>/comments")


def _flatten_author(row: dict) -> dict:
    """PostgREST embeds the joined author as a nested `author` object —
    flatten verified_at -> verified so the frontend doesn't need to know
    about the underlying column name, same convention as PostCard's
    author shape on the feed view."""
    author = row.pop("author", None) or {}
    row["author_full_name"] = author.get("full_name")
    row["author_avatar_url"] = author.get("avatar_url")
    row["author_verified"] = author.get("verified_at") is not None
    return row


@bp.get("")
def list_comments(post_id):
    """Public — mirrors comments_select's RLS (active comments visible
    to everyone). Chronological, oldest first, like a normal comment
    thread rather than the feed's engagement ranking."""
    data, status = rest_request(
        "GET", "comments",
        params={
            "post_id": f"eq.{post_id}",
            "status": "eq.active",
            "select": "id,post_id,author_id,content,created_at,author:users(full_name,avatar_url,verified_at)",
            "order": "created_at.asc",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load comments"}), status
    return jsonify([_flatten_author(row) for row in (data or [])]), 200


@bp.post("")
@require_auth
def create_comment(post_id):
    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    if not validate_comment_content(content):
        return jsonify({"error": "comment must be 1-1000 characters"}), 400

    payload = {"post_id": post_id, "author_id": g.user_id, "content": content}
    data, status = rest_request(
        "POST", "comments", token=g.token, json_body=payload, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not post comment"}), status
    return jsonify(data[0] if isinstance(data, list) else data), 201


@bp.patch("/<comment_id>")
@require_auth
def update_comment(post_id, comment_id):
    """Same shape as posts.update_post — edit content, or soft-delete
    via {"delete": true}. RLS (comments_update_own) already restricts
    this to the comment's own author or staff; the params filter on
    post_id too just so a mismatched post_id/comment_id pair 404s
    instead of silently succeeding against the wrong post's comment."""
    body = request.get_json(silent=True) or {}
    updates = {}

    if "content" in body:
        content = (body["content"] or "").strip()
        if not validate_comment_content(content):
            return jsonify({"error": "comment must be 1-1000 characters"}), 400
        updates["content"] = content

    if body.get("delete") is True:
        updates["status"] = "removed"

    if not updates:
        return jsonify({"error": "nothing to update"}), 400

    data, status = rest_request(
        "PATCH", "comments", token=g.token,
        params={"id": f"eq.{comment_id}", "post_id": f"eq.{post_id}"},
        json_body=updates, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "update failed or not permitted"}), status
    if not data:
        return jsonify({"error": "comment not found or not yours"}), 404
    return jsonify(data[0]), 200
