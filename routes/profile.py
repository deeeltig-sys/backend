import uuid

from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request, storage_upload
from lib.decorators import require_auth, optional_auth
from models.user import sanitize_social_links, sanitize_bio, public_user_fields

bp = Blueprint("profile", __name__, url_prefix="/api/profile")

MAX_AVATAR_BYTES = 4 * 1024 * 1024  # 4MB — smaller cap than post images, it's a thumbnail
ALLOWED_AVATAR_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


@bp.get("/<user_id>")
@optional_auth
def get_profile(user_id):
    """Public — but was previously returning the ENTIRE raw users row
    (student_id_number, student_email, verified_by, etc.) to anyone
    unauthenticated. Now shaped through public_user_fields() like
    every other public-facing user shape, plus follow counts and
    whether the caller (if signed in) already follows this person."""
    data, status = rest_request(
        "GET", "users",
        params={"id": f"eq.{user_id}", "select": "*,university:universities(name)"},
    )
    if status != 200 or not data:
        return jsonify({"error": "user not found"}), 404

    row = data[0]
    result = public_user_fields(row)
    result["follower_count"] = row.get("follower_count", 0)
    result["following_count"] = row.get("following_count", 0)
    result["university_name"] = (row.get("university") or {}).get("name")
    result["is_following"] = False

    if g.user_id and g.user_id != user_id:
        follow_data, fstatus = rest_request(
            "GET", "follows", token=g.token,
            params={"follower_id": f"eq.{g.user_id}", "followed_id": f"eq.{user_id}", "select": "follower_id"},
        )
        result["is_following"] = fstatus == 200 and bool(follow_data)

    return jsonify(result), 200


@bp.post("/upload-avatar")
@require_auth
def upload_avatar():
    """Uploads to the `avatars` bucket under the caller's own folder,
    then immediately writes the resulting URL onto their own users
    row. Two Supabase calls (storage, then a table update) rather
    than one, but it means the frontend gets a single request that
    returns the finished profile — no separate 'now call updateMe'
    step to remember."""
    if "avatar" not in request.files:
        return jsonify({"error": "attach an image file under the 'avatar' field"}), 400

    file = request.files["avatar"]
    content_type = file.mimetype
    if content_type not in ALLOWED_AVATAR_TYPES:
        return jsonify({"error": "only JPEG, PNG, or WEBP images are supported"}), 400

    file_bytes = file.read()
    if len(file_bytes) > MAX_AVATAR_BYTES:
        return jsonify({"error": "image must be under 4MB"}), 400

    extension = ALLOWED_AVATAR_TYPES[content_type]
    # Fixed filename per user (not a fresh uuid each time) so a
    # re-upload overwrites the old avatar instead of orphaning it in
    # storage — the update policy in avatar_storage_policies.sql
    # exists specifically to allow this overwrite.
    path = f"{g.user_id}/avatar.{extension}"

    upload_data, status = storage_upload("avatars", path, file_bytes, content_type, g.token)
    if status >= 400:
        return jsonify({"error": "avatar upload failed, try again"}), status

    # Cache-bust the URL so the new photo shows immediately instead of
    # the browser/CDN serving the previous image at the same path.
    avatar_url = f"{upload_data['url']}?v={uuid.uuid4().hex[:8]}"

    updated, ustatus = rest_request(
        "PATCH", "users", token=g.token,
        params={"id": f"eq.{g.user_id}"}, json_body={"avatar_url": avatar_url},
        prefer="return=representation",
    )
    if ustatus >= 400 or not updated:
        return jsonify({"error": "avatar uploaded but profile update failed"}), 500

    return jsonify(updated[0]), 200


@bp.patch("/me")
@require_auth
def update_own_profile():
    body = request.get_json(silent=True) or {}
    allowed_fields = {"full_name", "avatar_url", "social_links", "bio"}
    updates = {k: v for k, v in body.items() if k in allowed_fields}

    if "social_links" in updates:
        # Never trust the raw payload straight into Postgres — strip
        # unknown platform keys and anything that isn't a clean handle.
        updates["social_links"] = sanitize_social_links(updates["social_links"])

    if "bio" in updates:
        updates["bio"] = sanitize_bio(updates["bio"])

    if not updates:
        return jsonify({"error": "nothing to update"}), 400

    data, status = rest_request(
        "PATCH", "users", token=g.token,
        params={"id": f"eq.{g.user_id}"}, json_body=updates, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "update failed"}), status
    return jsonify(data[0] if data else {}), 200
