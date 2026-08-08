from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth

bp = Blueprint("collections", __name__, url_prefix="/api/collections")


@bp.get("")
@require_auth
def list_collections():
    """Every collection the caller owns, each with a post count so the
    list screen doesn't need a second round-trip per folder."""
    collections, status = rest_request(
        "GET", "saved_collections", token=g.token,
        params={"user_id": f"eq.{g.user_id}", "select": "id,title,created_at", "order": "created_at.asc"},
    )
    if status != 200:
        return jsonify({"error": "could not load collections"}), status
    if not collections:
        return jsonify([]), 200

    saves, sstatus = rest_request(
        "GET", "saved_posts", token=g.token,
        params={"user_id": f"eq.{g.user_id}", "collection_id": "not.is.null", "select": "collection_id"},
    )
    counts = {}
    if sstatus == 200 and saves:
        for s in saves:
            counts[s["collection_id"]] = counts.get(s["collection_id"], 0) + 1

    for c in collections:
        c["post_count"] = counts.get(c["id"], 0)
    return jsonify(collections), 200


@bp.post("")
@require_auth
def create_collection():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not (1 <= len(title) <= 60):
        return jsonify({"error": "collection name must be 1-60 characters"}), 400

    data, status = rest_request(
        "POST", "saved_collections", token=g.token,
        json_body={"user_id": g.user_id, "title": title},
        prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not create collection"}), status
    created = data[0] if isinstance(data, list) else data
    created["post_count"] = 0
    return jsonify(created), 201


@bp.patch("/<collection_id>")
@require_auth
def rename_collection(collection_id):
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not (1 <= len(title) <= 60):
        return jsonify({"error": "collection name must be 1-60 characters"}), 400

    data, status = rest_request(
        "PATCH", "saved_collections", token=g.token,
        params={"id": f"eq.{collection_id}", "user_id": f"eq.{g.user_id}"},
        json_body={"title": title}, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not rename collection"}), status
    if not data:
        return jsonify({"error": "collection not found"}), 404
    return jsonify(data[0]), 200


@bp.delete("/<collection_id>")
@require_auth
def delete_collection(collection_id):
    """Deletes the folder only — the RLS-adjacent ON DELETE SET NULL
    on saved_posts.collection_id (db/saved_collections_migration.sql)
    means the actual bookmarks inside just fall back to uncategorized,
    never get deleted themselves."""
    data, status = rest_request(
        "DELETE", "saved_collections", token=g.token,
        params={"id": f"eq.{collection_id}", "user_id": f"eq.{g.user_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not delete collection"}), status
    return jsonify({"deleted": True}), 200
