import json

from pywebpush import webpush, WebPushException

from config import Config


def send_web_push(endpoint: str, p256dh: str, auth_key: str, title: str, body_text: str, url: str) -> bool:
    """Returns True if the push service accepted the message, False on
    any failure (expired subscription, bad keys, missing VAPID config,
    network error). Callers treat False as "nothing to do" — there's
    no cleanup of the dead subscription wired up yet (see the note in
    push_migration.sql), so a failure here is silent by design, not
    swallowed by accident.
    """
    if not (endpoint and p256dh and auth_key):
        return False
    if not Config.VAPID_PRIVATE_KEY:
        return False

    try:
        webpush(
            subscription_info={
                "endpoint": endpoint,
                "keys": {"p256dh": p256dh, "auth": auth_key},
            },
            data=json.dumps({
                "title": title or "CampusMEET",
                "body": body_text or "",
                "url": url or "/",
            }),
            vapid_private_key=Config.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": f"mailto:{Config.VAPID_CLAIMS_EMAIL}"},
        )
        return True
    except WebPushException:
        return False
