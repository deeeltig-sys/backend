"""
routes/comments.py — UPDATED VERSION

This route handler supports:
- Top-level comments (no parent_comment_id)
- Nested replies (parent_comment_id set to the comment being replied to)
- Thread structure in responses (top-level comment + replies nested)
- Edit/delete with status = 'removed' (soft delete)

Simply replace your existing routes/comments.py with this file.
"""

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
    """Load comments as a nested thread structure.
    
    Returns top-level comments with their replies nested inside under the 'replies' key.
    This matches Instagram/Twitter's comment threading model.
    
    Response format:
    [
      {
        "id": "...",
        "content": "Great post!",
        "author_full_name": "Alice",
        "author_avatar_url": "...",
        "author_verified": false,
        "created_at": "2026-07-30T...",
        "reply_count": 2,
        "replies": [
          {
            "id": "...",
            "content": "I agree!",
            "parent_comment_id": "uuid-of-alice-comment",
            "author_full_name": "Bob",
            ...
          },
          { ... }
        ]
      },
      ...
    ]
    """
    # 1. Fetch top-level comments (no parent)
    top_level, status = rest_request(
        "GET", "comments",
        params={
            "post_id": f"eq.{post_id}",
            "parent_comment_id": "is.null",
            "status": "eq.active",
            "select": "id,post_id,author_id,content,created_at,reply_count,author:users(full_name,avatar_url,verified_at)",
            "order": "created_at.asc",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load comments"}), status
    
    # Safety: ensure we always get a list
    if not isinstance(top_level, list):
        top_level = []
    
    # 2. For each top-level comment with replies, fetch its replies
    for comment in top_level:
        _flatten_author(comment)
        
        # Only query replies if reply_count > 0 (saves API calls)
        if comment.get("reply_count", 0) > 0:
            replies, r_status = rest_request(
                "GET", "comments",
                params={
                    "parent_comment_id": f"eq.{comment['id']}",
                    "status": "eq.active",
                    "select": "id,post_id,parent_comment_id,author_id,content,created_at,author:users(full_name,avatar_url,verified_at)",
                    "order": "created_at.asc",
                },
            )
            if r_status == 200 and isinstance(replies, list):
                comment["replies"] = [_flatten_author(r) for r in replies]
            else:
                comment["replies"] = []
        else:
            # No replies, but include empty array for consistent shape
            comment["replies"] = []
    
    return jsonify(top_level), 200


@bp.post("")
@require_auth
def create_comment(post_id):
    """Create a top-level comment or a reply to another comment.
    
    Request body:
    {
      "content": "Great post!",
      "parent_comment_id": "uuid-of-parent-comment"  // OPTIONAL: omit for top-level
    }
    
    Response: The created comment object (201 Created on success).
    """
    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    parent_comment_id = body.get("parent_comment_id")
    
    # Validate content length
    if not validate_comment_content(content):
        return jsonify({"error": "comment must be 1-1000 characters"}), 400
    
    # If replying to another comment, verify it exists and is on this post
    if parent_comment_id:
        parent, pstatus = rest_request(
            "GET", "comments",
            params={
                "id": f"eq.{parent_comment_id}",
                "select": "id,post_id,status"
            }
        )
        if pstatus != 200 or not parent:
            return jsonify({"error": "parent comment not found"}), 404
        
        parent_data = parent[0] if isinstance(parent, list) else parent
        
        # Verify parent comment is on the same post
        if parent_data.get("post_id") != post_id:
            return jsonify({"error": "parent comment is not on this post"}), 400
        
        # Verify parent comment is active (not removed/flagged)
        if parent_data.get("status") != "active":
            return jsonify({"error": "cannot reply to a removed comment"}), 400
    
    payload = {
        "post_id": post_id,
        "author_id": g.user_id,
        "content": content,
        "parent_comment_id": parent_comment_id,
    }
    
    data, status = rest_request(
        "POST", "comments", token=g.token, json_body=payload, prefer="return=representation",
    )
    
    if status >= 400:
        return jsonify({"error": "could not post comment"}), status
    
    # Flatten author if the response has it
    comment = data[0] if isinstance(data, list) else data
    if "author" in comment:
        _flatten_author(comment)
    
    return jsonify(comment), 201


@bp.patch("/<comment_id>")
@require_auth
def update_comment(post_id, comment_id):
    """Edit a comment's content or soft-delete it.
    
    Edit:
    {
      "content": "Updated text"
    }
    
    Soft delete:
    {
      "delete": true
    }
    
    RLS policy (comments_update_own) restricts this to:
    - The comment's own author
    - Staff (moderators/admins)
    
    The post_id filter ensures the comment belongs to this specific post.
    """
    body = request.get_json(silent=True) or {}
    updates = {}
    
    # Handle content update
    if "content" in body:
        content = (body["content"] or "").strip()
        if not validate_comment_content(content):
            return jsonify({"error": "comment must be 1-1000 characters"}), 400
        updates["content"] = content
    
    # Handle soft delete
    if body.get("delete") is True:
        updates["status"] = "removed"
    
    # Require at least one change
    if not updates:
        return jsonify({"error": "nothing to update"}), 400
    
    data, status = rest_request(
        "PATCH", "comments", token=g.token,
        params={
            "id": f"eq.{comment_id}",
            "post_id": f"eq.{post_id}"
        },
        json_body=updates,
        prefer="return=representation",
    )
    
    if status >= 400:
        return jsonify({"error": "update failed or not permitted"}), status
    
    if not data:
        return jsonify({"error": "comment not found or not yours"}), 404
    
    comment = data[0] if isinstance(data, list) else data
    if "author" in comment:
        _flatten_author(comment)
    
    return jsonify(comment), 200
