"""
lib/audit.py — one place every admin-action-logging call goes through,
so the shape of an audit entry stays consistent no matter which route
writes it.

Deliberately never raises: a failure to log an action must never block
or roll back the actual action it's describing. If this silently drops
an entry, that is judged an acceptable failure mode — a missing log
line is far less costly than a legitimate role change or report
resolution failing because logging hiccuped.
"""
import logging
from lib.supabase_client import rest_request

logger = logging.getLogger(__name__)


def log_admin_action(actor_id, token, action_type, target_type=None, target_id=None, detail=None):
    payload = {"actor_id": actor_id, "action_type": action_type}
    if target_type is not None:
        payload["target_type"] = target_type
    if target_id is not None:
        payload["target_id"] = target_id
    if detail is not None:
        payload["detail"] = detail

    try:
        _, status = rest_request("POST", "admin_actions", token=token, json_body=payload)
        if status >= 400:
            logger.warning("admin_actions insert failed with status %s: %s", status, payload)
    except Exception:
        logger.exception("admin_actions insert raised for payload: %s", payload)
