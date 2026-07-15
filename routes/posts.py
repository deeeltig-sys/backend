from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request, rpc
from lib.decorators import require_auth
from models.post import validate_post_content

bp = Blueprint("posts", __name__, url_prefix="/api/posts")


def _bearer_token_if_present():
    header = request.headers.get("Authorization", "")
    return header.split(" ", 1)[1] if header.startswith("Bearer ") else None


@bp.get("/feed")
def feed():
    """Reads from the `feed` view — active posts only, ranked by
    feed_score() (views + reactions + search_hits, equal weight for
    now — see db/schema.sql)."""
    limit = request.args.get("limit", 30)
    offset = request.args.get("offset", 0)
    data, status = rest_request(
        "GET", "feed", params={"select": "*", "limit": limit, "offset": offset},
    )
    if status != 200:
        return jsonify({"error": "could not load feed"}), status
    return jsonify(data), 200


@bp.post("")
@require_auth
def create_post():
    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    image_url = body.get("image_url")

    if not validate_post_content(content):
        return jsonify({"error": "post must be 1-2000 characters"}), 400

    profile, pstatus = rest_request(
        "GET", "users", token=g.token,
        params={"id": f"eq.{g.user_id}", "select": "university_id"},
    )
    if pstatus != 200 or not profile:
        return jsonify({"error": "could not resolve university"}), 400

    payload = {
        "author_id": g.user_id,
        "university_id": profile[0]["university_id"],
        "content": content,
        "image_url": image_url,
    }
    data, status = rest_request(
        "POST", "posts", token=g.token, json_body=payload, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not create post"}), status
    return jsonify(data[0] if isinstance(data, list) else data), 201


@bp.get("/<post_id>")
def get_post(post_id):
    data, status = rest_request("GET", "posts", params={"id": f"eq.{post_id}", "select": "*"})
    if status != 200 or not data:
        return jsonify({"error": "post not found"}), 404
    return jsonify(data[0]), 200


@bp.patch("/<post_id>")
@require_auth
def update_post(post_id):
    """Editing content or self-deleting (soft delete only — see
    migration v1.1. Hard delete is staff-only via posts_delete_staff)."""
    body = request.get_json(silent=True) or {}
    updates = {}

    if "content" in body:
        if not validate_post_content(body["content"]):
            return jsonify({"error": "post must be 1-2000 characters"}), 400
        updates["content"] = body["content"].strip()

    if body.get("delete") is True:
        updates["status"] = "removed"

    if not updates:
        return jsonify({"error": "nothing to update"}), 400

    data, status = rest_request(
        "PATCH", "posts", token=g.token,
        params={"id": f"eq.{post_id}"}, json_body=updates, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "update failed or not permitted"}), status
    if not data:
        return jsonify({"error": "post not found or not yours"}), 404
    return jsonify(data[0]), 200


@bp.post("/<post_id>/view")
def register_view(post_id):
    """No auth required — views come from anyone browsing, verified or
    not. Goes through the increment_view RPC because RLS wouldn't
    otherwise let a non-author touch this column (see db/schema.sql)."""
    data, status = rpc("increment_view", token=_bearer_token_if_present(),
                        payload={"p_post_id": post_id})
    if status >= 400:
        return jsonify({"error": "could not register view"}), status
    return jsonify({"ok": True}), 200


@bp.post("/<post_id>/search-hit")
def register_search_hit(post_id):
    data, status = rpc("increment_search_hit", token=_bearer_token_if_present(),
                        payload={"p_post_id": post_id})
    if status >= 400:
        return jsonify({"error": "could not register search hit"}), status
    return jsonify({"ok": True}), 200
