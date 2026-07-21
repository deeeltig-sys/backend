import uuid

from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request, rpc, storage_upload
from lib.decorators import require_auth
from models.post import validate_post_content

bp = Blueprint("posts", __name__, url_prefix="/api/posts")

MAX_IMAGE_BYTES = 6 * 1024 * 1024  # 6MB
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def _bearer_token_if_present():
    header = request.headers.get("Authorization", "")
    return header.split(" ", 1)[1] if header.startswith("Bearer ") else None


def _attach_user_reactions(posts, token):
    """Mutates `posts` in place, adding `user_reaction` (or None) to each
    row. Without this the frontend has no way to know "you already
    reacted to this one" -- the feed/search views themselves can't carry
    that, since it's specific to whoever is asking, not the post itself.
    Silently does nothing if there's no token (anonymous browsing) or no
    posts to annotate."""
    if not token or not posts:
        return
    post_ids = ",".join(p["id"] for p in posts)
    reactions, status = rest_request(
        "GET", "reactions", token=token,
        params={"post_id": f"in.({post_ids})", "select": "post_id,type"},
    )
    if status != 200 or not isinstance(reactions, list):
        return
    by_post = {r["post_id"]: r["type"] for r in reactions}
    for p in posts:
        p["user_reaction"] = by_post.get(p["id"])


def _blocked_author_ids(token):
    """Posts from anyone the caller has blocked, filtered out of
    whatever feed/search they're looking at. Only reads the caller's
    own block rows (RLS only allows that anyway) — there's no way,
    by design, to discover who has blocked *you*."""
    if not token:
        return set()
    data, status = rest_request(
        "GET", "blocks", token=token,
        params={"select": "blocked_id"},
    )
    if status != 200 or not isinstance(data, list):
        return set()
    return {row["blocked_id"] for row in data}


def _filter_blocked(posts, token):
    blocked = _blocked_author_ids(token)
    if not blocked:
        return posts
    return [p for p in posts if p.get("author_id") not in blocked]


@bp.post("/upload-image")
@require_auth
def upload_image():
    """Powers image posts. The file goes straight to the `post-images`
    Supabase Storage bucket under the caller's own user id as a path
    prefix, using the caller's own JWT — same no-service-role rule as
    every other write in this backend. Storage RLS (db/storage_policies.sql)
    is what actually enforces that a student can only upload into their
    own folder; this route just shapes the path and forwards the bytes."""
    if "image" not in request.files:
        return jsonify({"error": "attach an image file under the 'image' field"}), 400

    file = request.files["image"]
    content_type = file.mimetype
    if content_type not in ALLOWED_IMAGE_TYPES:
        return jsonify({"error": "only JPEG, PNG, or WEBP images are supported"}), 400

    file_bytes = file.read()
    if len(file_bytes) > MAX_IMAGE_BYTES:
        return jsonify({"error": "image must be under 6MB"}), 400

    extension = ALLOWED_IMAGE_TYPES[content_type]
    path = f"{g.user_id}/{uuid.uuid4().hex}.{extension}"

    data, status = storage_upload("post-images", path, file_bytes, content_type, g.token)
    if status >= 400:
        return jsonify({"error": "image upload failed, try again"}), status

    return jsonify({"url": data["url"]}), 201


@bp.get("/feed")
def feed():
    """Reads from the `feed` view — active posts only, ranked by
    feed_score() (views + reactions + search_hits, equal weight for
    now — see db/schema.sql). The view itself now also joins author
    name/avatar/verified (db/avatar_and_search_migration.sql); this
    route additionally stamps each post with the caller's own
    user_reaction so the frontend can highlight it, when a token
    is present."""
    limit = request.args.get("limit", 30)
    offset = request.args.get("offset", 0)
    data, status = rest_request(
        "GET", "feed", params={"select": "*", "limit": limit, "offset": offset},
    )
    if status != 200:
        return jsonify({"error": "could not load feed"}), status

    token = _bearer_token_if_present()
    data = _filter_blocked(data, token)
    _attach_user_reactions(data, token)
    return jsonify(data), 200


@bp.get("/search")
def search_posts():
    """Simple ILIKE search over post content, scoped to the same
    active-only `feed` view so results carry author info and
    reaction/view counts identically to the main feed. Not a
    full-text-search ranking yet -- fine at current volume, backed
    by a trigram index (db/avatar_and_search_migration.sql) so it
    stays fast as content grows."""
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify([]), 200
    if len(query) < 2:
        return jsonify({"error": "type at least 2 characters to search"}), 400

    limit = request.args.get("limit", 30)
    data, status = rest_request(
        "GET", "feed",
        params={"select": "*", "content": f"ilike.*{query}*", "limit": limit},
    )
    if status != 200:
        return jsonify({"error": "search failed"}), status

    token = _bearer_token_if_present()
    data = _filter_blocked(data, token)
    _attach_user_reactions(data, token)
    return jsonify(data), 200


@bp.post("")
@require_auth
def create_post():
    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    image_url = body.get("image_url")

    if not content and not image_url:
        return jsonify({"error": "write something or attach a photo before posting"}), 400
    if content and not validate_post_content(content):
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
