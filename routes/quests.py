from datetime import datetime, timezone

from flask import Blueprint, jsonify, g
from lib.supabase_client import rest_request
from lib.decorators import require_auth

bp = Blueprint("quests", __name__, url_prefix="/api/quests")


def _current_period_key(cadence: str) -> str:
    """Mirrors current_period_key() in db/reputation_system_migration.sql
    exactly — Postgres's to_char(now(), 'IYYY-"W"IW') is the ISO
    week-numbering year/week, which is precisely what Python's
    isocalendar() returns, so this can be computed here without an
    extra round-trip to the database."""
    now = datetime.now(timezone.utc)
    if cadence == "weekly":
        iso_year, iso_week, _ = now.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    return f"{now.year}-{now.month:02d}"


@bp.get("/mine")
@require_auth
def my_quests():
    """Every active quest, with the caller's progress for the CURRENT
    period attached. A quest the caller hasn't touched yet this period
    has no user_quest_progress row at all (record_quest_progress only
    inserts one on the first matching action) — this synthesizes a
    zero-progress entry for those instead of omitting the quest, so the
    frontend can always render "0/2 posts" rather than the quest just
    not appearing until someone happens to start it."""
    quests, qstatus = rest_request(
        "GET", "quests",
        params={"is_active": "eq.true", "select": "id,code,title,description,cadence,action_type,target_count,points_reward"},
    )
    if qstatus != 200:
        return jsonify({"error": "could not load quests"}), qstatus
    quests = quests or []
    if not quests:
        return jsonify([]), 200

    quest_ids = [q["id"] for q in quests]
    progress_rows, pstatus = rest_request(
        "GET", "user_quest_progress", token=g.token,
        params={
            "user_id": f"eq.{g.user_id}",
            "quest_id": f"in.({','.join(quest_ids)})",
            "select": "quest_id,period_key,progress_count,completed_at",
        },
    )
    progress_by_quest = {}
    if pstatus == 200:
        for row in progress_rows or []:
            progress_by_quest[(row["quest_id"], row["period_key"])] = row

    result = []
    for q in quests:
        period_key = _current_period_key(q["cadence"])
        progress = progress_by_quest.get((q["id"], period_key))
        result.append({
            **q,
            "period_key": period_key,
            "progress_count": progress["progress_count"] if progress else 0,
            "completed": bool(progress and progress["completed_at"]),
        })
    return jsonify(result), 200
