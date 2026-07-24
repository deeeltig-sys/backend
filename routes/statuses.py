import uuid

from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request, storage_upload
from lib.decorators import require_auth
from lib.watermark import apply_watermark

bp = Blueprint("statuses", __name__, url_prefix="/api/statuses")

MAX_IMAGE_BYTES = 6 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_TEXT_LENGTH = 280


@bp.post("/upload-image")
@require_auth
def upload_status_image():
    """Reuses the same `post-images` storage bucket as regular posts
    (under a status/ prefix within the caller's own folder) rather than
    standing up a whole separate bucket + RLS policy set for what's
    functionally the same thing — an image the caller owns."""
    if "image" not in request.files:
        return jsonify({"error": "attach an image file under the 'image' field"}), 400
    file = request.files["image"]
    content_type = file.mimetype
    if content_type not in ALLOWED_IMAGE_TYPES:
        return jsonify({"error": "only JPEG, PNG, or WEBP images are supported"}), 400
    file_bytes = file.read()
    if len(file_bytes) > MAX_IMAGE_BYTES:
        return jsonify({"error": "image must be under 6MB"}), 400

    file_bytes = apply_watermark(file_bytes, content_type)
    extension = ALLOWED_IMAGE_TYPES[content_type]
    path = f"{g.user_id}/status/{uuid.uuid4().hex}.{extension}"

    data, status = storage_upload("post-images", path, file_bytes, content_type, g.token)
    if status >= 400:
        return jsonify({"error": "image upload failed, try again"}), status
    return jsonify({"url": data["url"]}), 201


@bp.post("")
@require_auth
def create_status():
    body = request.get_json(silent=True) or {}
    content_type = body.get("content_type")
    if content_type not in ("image", "text"):
        return jsonify({"error": "content_type must be 'image' or 'text'"}), 400

    payload = {"author_id": g.user_id, "content_type": content_type}
    if content_type == "image":
        image_url = body.get("image_url")
        if not image_url:
            return jsonify({"error": "image_url is required for an image status"}), 400
        payload["image_url"] = image_url
    else:
        text = (body.get("text_content") or "").strip()
        if not text or len(text) > MAX_TEXT_LENGTH:
            return jsonify({"error": f"text_content must be 1-{MAX_TEXT_LENGTH} characters"}), 400
        payload["text_content"] = text
        payload["background_color"] = body.get("background_color") or "#7a2436"

    data, status = rest_request(
        "POST", "statuses", token=g.token, json_body=payload, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not post status"}), status
    return jsonify(data[0] if isinstance(data, list) else data), 201


@bp.get("")
@require_auth
def list_statuses():
    """Every active status, grouped by author. Own status (if any)
    always comes first; everyone else is sorted unseen-first so the
    strip surfaces what's actually new, then by most recent."""
    data, status = rest_request(
        "GET", "statuses", token=g.token,
        params={
            "select": "id,author_id,content_type,image_url,text_content,background_color,created_at,expires_at,"
                      "author:users!statuses_author_id_fkey(id,full_name,avatar_url,verified_at)",
            "order": "created_at.desc",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load statuses"}), status

    all_ids = [s["id"] for s in (data or [])]
    seen_ids = set()
    if all_ids:
        views, vstatus = rest_request(
            "GET", "status_views", token=g.token,
            params={"status_id": f"in.({','.join(all_ids)})", "viewer_id": f"eq.{g.user_id}", "select": "status_id"},
        )
        if vstatus == 200:
            seen_ids = {v["status_id"] for v in (views or [])}

    grouped = {}
    for s in data or []:
        author = s.pop("author") or {}
        author_id = s["author_id"]
        grouped.setdefault(author_id, {
            "author": {
                "id": author.get("id"),
                "full_name": author.get("full_name"),
                "avatar_url": author.get("avatar_url"),
                "verified": author.get("verified_at") is not None,
            },
            "statuses": [],
        })
        s["viewed"] = s["id"] in seen_ids
        grouped[author_id]["statuses"].append(s)

    groups = list(grouped.values())
    for g_row in groups:
        g_row["all_viewed"] = all(s["viewed"] for s in g_row["statuses"])

    groups.sort(key=lambda g_row: (
        g_row["author"]["id"] != g.user_id,   # own status group first
        g_row["all_viewed"],                   # then unseen-first
        -max(_ts(s["created_at"]) for s in g_row["statuses"]),  # then most recent
    ))
    return jsonify(groups), 200


def _ts(iso: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


@bp.post("/<status_id>/view")
@require_auth
def mark_viewed(status_id):
    rest_request(
        "POST", "status_views", token=g.token,
        json_body={"status_id": status_id, "viewer_id": g.user_id},
        prefer="resolution=ignore-duplicates",
    )
    return jsonify({"viewed": True}), 200


@bp.get("/<status_id>/viewers")
@require_auth
def list_viewers(status_id):
    """RLS already restricts this to the status's own author — anyone
    else's request just comes back empty rather than an explicit 403,
    same pattern as the rest of this backend."""
    data, status = rest_request(
        "GET", "status_views", token=g.token,
        params={
            "status_id": f"eq.{status_id}", "select": "viewed_at,"
            "viewer:users!status_views_viewer_id_fkey(id,full_name,avatar_url)",
            "order": "viewed_at.desc",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load viewers"}), status
    return jsonify(data or []), 200


@bp.delete("/<status_id>")
@require_auth
def delete_status(status_id):
    data, status = rest_request(
        "DELETE", "statuses", token=g.token, params={"id": f"eq.{status_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not delete status"}), status
    return jsonify({"deleted": True}), 200
