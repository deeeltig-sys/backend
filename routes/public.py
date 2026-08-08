"""
routes/public.py

Unauthenticated endpoints only — this is the one place in the backend
that's meant to be reachable with no session at all, for shared post
links (WhatsApp/FB/IG/X) that need to work for people who haven't
signed up yet. Everything here calls the `get_public_post` Postgres
function (db/public_share_migration.sql), which explicitly whitelists
safe columns and only ever returns posts where audience = 'public' —
so even if this route is called with garbage input, it can't leak a
friends-only post or any column that function doesn't select.
"""

from flask import Blueprint, jsonify
from lib.supabase_client import rpc

bp = Blueprint("public", __name__, url_prefix="/api/public")


@bp.get("/posts/<post_id>")
def get_public_post(post_id):
    data, status = rpc("get_public_post", token=None, payload={"p_post_id": post_id})
    if status >= 400:
        return jsonify({"error": "could not load post"}), status
    # security definer functions returning `table (...)` come back as a
    # list even for one row — empty list means no matching public post
    # (wrong id, not active, or audience = 'friends').
    if not data:
        return jsonify({"error": "post not found"}), 404
    return jsonify(data[0]), 200
