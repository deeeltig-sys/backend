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

def auth_signup(email: str, password: str, full_name: str, student_id_number: str):
    url = f"{Config.SUPABASE_URL}/auth/v1/signup"
    response = requests.post(
        url,
        headers=_headers(),
        json={
            "email": email,
            "password": password,
            "data": {
                "full_name": full_name,
                "student_id_number": student_id_number,
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
