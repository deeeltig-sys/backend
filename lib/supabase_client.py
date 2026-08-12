"""
lib/supabase_client.py

Every function here talks to Supabase over plain HTTPS — PostgREST for
tables/RPCs, GoTrue for auth, Storage for images. Nothing in this file
ever uses a service-role key: table/storage access always runs as
either the anon key (public reads) or the caller's own JWT (anything
that should be subject to RLS). That's a deliberate constraint, not an
oversight — it's what makes it safe for this backend to have zero
special privileges beyond what the signed-in user already has.
"""

import requests

from config import Config

REQUEST_TIMEOUT = 15


def _headers(token: str | None = None, prefer: str | None = None, content_type: str = "application/json") -> dict:
    headers = {
        "apikey": Config.SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token or Config.SUPABASE_ANON_KEY}",
        "Content-Type": content_type,
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _parse(response: requests.Response):
    if response.status_code == 204 or not response.content:
        return None, response.status_code
    try:
        return response.json(), response.status_code
    except ValueError:
        return {"raw": response.text}, response.status_code


# ---------------------------------------------------------------
# PostgREST — table reads/writes and RPC calls, RLS-governed by
# whichever token is passed in.
# ---------------------------------------------------------------

def rest_request(method: str, table: str, token: str | None = None, params: dict | None = None,
                  json_body=None, prefer: str | None = None):
    url = f"{Config.SUPABASE_URL}/rest/v1/{table}"
    response = requests.request(
        method,
        url,
        headers=_headers(token, prefer),
        params=params,
        json=json_body,
        timeout=REQUEST_TIMEOUT,
    )
    return _parse(response)


def rest_count(table: str, token: str | None = None, params: dict | None = None):
    """Returns just the row count for `table` filtered by `params`,
    without ever transferring the matching rows themselves. Uses a
    HEAD request with `Prefer: count=exact` — Postgres computes the
    count once and PostgREST reports it in the `Content-Range`
    response header (e.g. "*/42"), which we read straight off the
    HTTP response with no body to parse.

    The rest of this codebase counts things by fetching every row and
    calling len() on the list (see the original routes/stats.py) —
    fine at 30 users, but that approach re-downloads the entire table
    on every call and gets slower with every signup. This is the
    version that stays flat as CampusMEET grows, used by the admin
    stats dashboard (routes/admin.py) where several counts are needed
    on one screen load.
    """
    url = f"{Config.SUPABASE_URL}/rest/v1/{table}"
    response = requests.head(
        url,
        headers=_headers(token, prefer="count=exact"),
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    content_range = response.headers.get("Content-Range", "")
    if "/" in content_range:
        total = content_range.rsplit("/", 1)[-1]
        if total.isdigit():
            return int(total), response.status_code
    return None, response.status_code


def rpc(function_name: str, token: str | None = None, payload: dict | None = None):
    url = f"{Config.SUPABASE_URL}/rest/v1/rpc/{function_name}"
    response = requests.post(
        url,
        headers=_headers(token, prefer="return=representation"),
        json=payload or {},
        timeout=REQUEST_TIMEOUT,
    )
    return _parse(response)


# ---------------------------------------------------------------
# GoTrue — auth. Signup/login return whatever GoTrue returns
# (access_token, refresh_token, user, ...) untouched, so the
# frontend shape stays a direct mirror of Supabase's own response.
# ---------------------------------------------------------------

def auth_signup(email: str, password: str, full_name: str, university_id: str = None, university_name: str = None, referred_by: str = None):
    url = f"{Config.SUPABASE_URL}/auth/v1/signup"
    response = requests.post(
        url,
        headers=_headers(),
        json={
            "email": email,
            "password": password,
            "data": {
                "full_name": full_name,
                "university_id": university_id,
                "university_name": university_name,
                "referred_by": referred_by,
            },
        },
        timeout=REQUEST_TIMEOUT,
    )
    return _parse(response)


def auth_login(email: str, password: str):
    url = f"{Config.SUPABASE_URL}/auth/v1/token"
    response = requests.post(
        url,
        headers=_headers(),
        params={"grant_type": "password"},
        json={"email": email, "password": password},
        timeout=REQUEST_TIMEOUT,
    )
    return _parse(response)


def auth_refresh(refresh_token: str):
    """Exchanges a refresh token for a new access/refresh pair. This is
    what keeps a student signed in indefinitely without ever storing a
    password — the frontend calls this quietly whenever a request comes
    back 401, rather than forcing a re-login."""
    url = f"{Config.SUPABASE_URL}/auth/v1/token"
    response = requests.post(
        url,
        headers=_headers(),
        params={"grant_type": "refresh_token"},
        json={"refresh_token": refresh_token},
        timeout=REQUEST_TIMEOUT,
    )
    return _parse(response)


def auth_get_user(token: str):
    """Resolves a bearer token to the auth user it belongs to. Used by
    require_auth so token validity is always checked against Supabase
    itself, never just decoded and trusted client-side."""
    url = f"{Config.SUPABASE_URL}/auth/v1/user"
    response = requests.get(url, headers=_headers(token), timeout=REQUEST_TIMEOUT)
    return _parse(response)


def auth_recover(email: str):
    """Triggers GoTrue's password-recovery email. The redirect_to here
    is what points the emailed link at reset-password.html on the
    Netlify site rather than wherever the Supabase project's default
    site URL happens to be set — worth double-checking that page is
    actually deployed there before relying on this."""
    url = f"{Config.SUPABASE_URL}/auth/v1/recover"
    response = requests.post(
        url,
        headers=_headers(),
        json={"email": email, "redirect_to": Config.PASSWORD_RESET_REDIRECT_URL},
        timeout=REQUEST_TIMEOUT,
    )
    return _parse(response)


def auth_update_password(recovery_token: str, new_password: str):
    """Sets a new password using the short-lived token from a clicked
    recovery link — NOT the person's normal session token. GoTrue
    treats this the same as any other 'update the current user'
    call, just authenticated with the recovery token instead of a
    login session."""
    url = f"{Config.SUPABASE_URL}/auth/v1/user"
    response = requests.put(
        url,
        headers=_headers(recovery_token),
        json={"password": new_password},
        timeout=REQUEST_TIMEOUT,
    )
    return _parse(response)


def auth_delete_self(token: str):
    """Self-service account deletion via the caller's own session
    token. This requires GoTrue's 'allow users to delete their own
    account' setting to be enabled in the Supabase project — if it
    isn't, this call fails and routes/auth.py falls back to
    anonymizing the profile instead of hard-erroring the request."""
    url = f"{Config.SUPABASE_URL}/auth/v1/user"
    response = requests.delete(url, headers=_headers(token), timeout=REQUEST_TIMEOUT)
    return _parse(response)


# ---------------------------------------------------------------
# Storage — post images. Uploads go up under the caller's own user
# id as a path prefix and run with the caller's JWT, so the bucket's
# RLS policy (db/storage_policies.sql) is what actually decides
# whether the write is allowed, not this function.
# ---------------------------------------------------------------

def storage_upload(bucket: str, path: str, file_bytes: bytes, content_type: str, token: str):
    url = f"{Config.SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
    response = requests.put(
        url,
        headers=_headers(token, content_type=content_type),
        data=file_bytes,
        timeout=REQUEST_TIMEOUT,
    )
    data, status = _parse(response)
    if status >= 400:
        return data, status
    public_url = f"{Config.SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"
    return {"url": public_url}, status


def storage_upload_private(bucket: str, path: str, file_bytes: bytes, content_type: str, token: str):
    """Same PUT as storage_upload, but returns just the storage PATH,
    never a public URL — for private buckets (voice-notes) where a
    bare /object/public/... link would bypass RLS entirely. Callers
    must fetch a short-lived link via storage_create_signed_url
    whenever the object actually needs to be played/downloaded."""
    url = f"{Config.SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
    response = requests.put(
        url,
        headers=_headers(token, content_type=content_type),
        data=file_bytes,
        timeout=REQUEST_TIMEOUT,
    )
    data, status = _parse(response)
    if status >= 400:
        return data, status
    return {"path": path}, status


def storage_create_signed_url(bucket: str, path: str, token: str, expires_in: int = 3600):
    """Requests a time-limited signed URL for a private object, using
    the CALLER's own JWT — Supabase Storage still checks the select
    RLS policy on storage.objects before it will sign anything, so
    this can't be used to read an object the caller isn't actually
    allowed to see. expires_in is in seconds (default 1 hour)."""
    url = f"{Config.SUPABASE_URL}/storage/v1/object/sign/{bucket}/{path}"
    response = requests.post(
        url,
        headers=_headers(token),
        json={"expiresIn": expires_in},
        timeout=REQUEST_TIMEOUT,
    )
    data, status = _parse(response)
    if status >= 400:
        return data, status
    signed_path = (data or {}).get("signedURL")
    if not signed_path:
        return {"error": "no signed URL returned"}, 500
    return {"url": f"{Config.SUPABASE_URL}/storage/v1{signed_path}"}, status


def storage_delete_object(bucket: str, path: str, token: str):
    """Deletes an object via the Storage API (NOT a raw SQL DELETE on
    storage.objects) — this matters: a plain SQL delete only removes
    the Postgres metadata row, it does NOT free the underlying bytes
    in Supabase's S3-compatible backend, so the storage bill keeps
    growing even after the row is gone. Going through this HTTP
    endpoint is what actually reclaims the space. Runs as the CALLER's
    own JWT (see module docstring — never service-role), which is why
    this can only ever delete something the caller's storage RLS
    policy already lets them delete."""
    url = f"{Config.SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
    response = requests.delete(url, headers=_headers(token), timeout=REQUEST_TIMEOUT)
    return _parse(response)
