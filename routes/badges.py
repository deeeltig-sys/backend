from flask import Blueprint, jsonify
from lib.supabase_client import rest_request

bp = Blueprint("badges", __name__, url_prefix="/api/badges")


@bp.get("")
def list_badge_types():
    """Every active badge someone could earn — powers a "badges you can
    earn" screen. Deliberately public/no-auth: seeing what's earnable
    is part of what makes the system motivating, same as a game showing
    locked achievements before you've unlocked them."""
    data, status = rest_request(
        "GET", "badge_types",
        params={"is_active": "eq.true", "select": "id,code,name,description,icon,rule_type,repeatable", "order": "created_at.asc"},
    )
    if status != 200:
        return jsonify({"error": "could not load badges"}), status
    return jsonify(data or []), 200


@bp.get("/users/<user_id>")
def list_earned_badges(user_id):
    """Everything a given user has actually earned, most recent first —
    same public-read pattern as highlights (routes/highlights.py):
    reputation is meant to be visible on a profile, not gated. The
    explicit !user_badges_badge_type_id_fkey isn't strictly required
    here (user_badges only has one FK into badge_types, so there's no
    ambiguity the way follows.py had with two FKs into users), but
    naming it matches how this codebase already writes every other
    embed and costs nothing."""
    data, status = rest_request(
        "GET", "user_badges",
        params={
            "user_id": f"eq.{user_id}",
            "select": "id,period_key,earned_at,context_snapshot,badge:badge_types!user_badges_badge_type_id_fkey(code,name,description,icon)",
            "order": "earned_at.desc",
        },
    )
    if status != 200:
        return jsonify({"error": "could not load badges"}), status
    # Flatten the embedded badge object onto each row so the frontend
    # doesn't need to reach into a nested `.badge.name` for every field.
    result = []
    for row in data or []:
        badge = row.pop("badge", None) or {}
        result.append({**row, **badge})
    return jsonify(result), 200
