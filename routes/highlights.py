from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth

bp = Blueprint("highlights", __name__, url_prefix="/api/highlights")


@bp.get("/users/<user_id>")
def list_for_user(user_id):
    """Powers the row of circles under a profile. `cover` is just the
    first item's image (or None for an all-text highlight — the
    frontend falls back to a colored circle with the title's first
    letter, same as an avatar with no photo)."""
    highlights, status = rest_request(
        "GET", "status_highlights",
        params={"user_id": f"eq.{user_id}", "select": "id,title,order_index,created_at", "order": "order_index.asc"},
    )
    if status != 200:
        return jsonify({"error": "could not load highlights"}), status
    if not highlights:
        return jsonify([]), 200

    ids = [h["id"] for h in highlights]
    items, istatus = rest_request(
        "GET", "status_highlight_items",
        params={"highlight_id": f"in.({','.join(ids)})", "select": "highlight_id,image_url,order_index", "order": "order_index.asc"},
    )
    cover_by_highlight = {}
    if istatus == 200 and items:
        for item in items:
            cover_by_highlight.setdefault(item["highlight_id"], item.get("image_url"))

    for h in highlights:
        h["cover_url"] = cover_by_highlight.get(h["id"])
    return jsonify(highlights), 200


@bp.post("")
@require_auth
def create_highlight():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not (1 <= len(title) <= 40):
        return jsonify({"error": "highlight title must be 1-40 characters"}), 400

    data, status = rest_request(
        "POST", "status_highlights", token=g.token,
        json_body={"user_id": g.user_id, "title": title},
        prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not create highlight"}), status
    return jsonify(data[0] if isinstance(data, list) else data), 201


@bp.get("/<highlight_id>")
def get_highlight(highlight_id):
    """Full item list for the viewer — one call, plays like a mini
    Status carousel that never expires."""
    highlight, hstatus = rest_request(
        "GET", "status_highlights", params={"id": f"eq.{highlight_id}", "select": "*"},
    )
    if hstatus != 200 or not highlight:
        return jsonify({"error": "highlight not found"}), 404

    items, istatus = rest_request(
        "GET", "status_highlight_items",
        params={"highlight_id": f"eq.{highlight_id}", "select": "*", "order": "order_index.asc"},
    )
    result = highlight[0]
    result["items"] = items if istatus == 200 and items else []
    return jsonify(result), 200


@bp.post("/<highlight_id>/items")
@require_auth
def add_item(highlight_id):
    """Adds a status (still live or not) into a highlight by copying
    its content — never a foreign key to the original row, since that
    row is allowed to expire and eventually be purged (see
    db/highlights_migration.sql for why)."""
    body = request.get_json(silent=True) or {}
    status_id = body.get("status_id")
    if not status_id:
        return jsonify({"error": "status_id required"}), 400

    owned, ostatus = rest_request(
        "GET", "status_highlights", token=g.token,
        params={"id": f"eq.{highlight_id}", "user_id": f"eq.{g.user_id}", "select": "id"},
    )
    if ostatus != 200 or not owned:
        return jsonify({"error": "highlight not found or not yours"}), 404

    source, sstatus = rest_request(
        "GET", "statuses", token=g.token,
        params={"id": f"eq.{status_id}", "author_id": f"eq.{g.user_id}", "select": "content_type,image_url,text_content,background_color"},
    )
    if sstatus != 200 or not source:
        return jsonify({"error": "that status wasn't found (it may have already expired)"}), 404

    src = source[0]
    count, cstatus = rest_request(
        "GET", "status_highlight_items", token=g.token,
        params={"highlight_id": f"eq.{highlight_id}", "select": "id"},
    )
    next_order = len(count) if cstatus == 200 and count else 0

    payload = {
        "highlight_id": highlight_id,
        "content_type": src["content_type"],
        "image_url": src.get("image_url"),
        "text_content": src.get("text_content"),
        "background_color": src.get("background_color"),
        "order_index": next_order,
    }
    data, status = rest_request(
        "POST", "status_highlight_items", token=g.token, json_body=payload, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not add to highlight"}), status
    return jsonify(data[0] if isinstance(data, list) else data), 201


@bp.delete("/<highlight_id>")
@require_auth
def delete_highlight(highlight_id):
    data, status = rest_request(
        "DELETE", "status_highlights", token=g.token,
        params={"id": f"eq.{highlight_id}", "user_id": f"eq.{g.user_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not delete highlight"}), status
    return jsonify({"deleted": True}), 200


@bp.delete("/<highlight_id>/items/<item_id>")
@require_auth
def delete_item(highlight_id, item_id):
    owned, ostatus = rest_request(
        "GET", "status_highlights", token=g.token,
        params={"id": f"eq.{highlight_id}", "user_id": f"eq.{g.user_id}", "select": "id"},
    )
    if ostatus != 200 or not owned:
        return jsonify({"error": "highlight not found or not yours"}), 404

    data, status = rest_request(
        "DELETE", "status_highlight_items", token=g.token,
        params={"id": f"eq.{item_id}", "highlight_id": f"eq.{highlight_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not remove item"}), status
    return jsonify({"deleted": True}), 200
