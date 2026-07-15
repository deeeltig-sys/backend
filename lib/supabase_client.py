"""
Thin wrapper around Supabase's Auth and PostgREST HTTP APIs.

Deliberately uses plain `requests` instead of the supabase-py SDK so the
user's JWT can be forwarded per-request and Postgres RLS policies apply
exactly as written in db/schema.sql — Flask never bypasses RLS with a
service-role key. Every write goes through as the actual user, so
"only the author can edit their own post" is enforced by Postgres,
not by this backend trusting itself.
"""
import requests
from config import Config

AUTH = "/auth/v1"
REST = "/rest/v1"


def _headers(token=None):
    return {
        "apikey": Config.SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token or Config.SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }


def _json_or_none(resp):
    try:
        return resp.json()
    except ValueError:
        return None


# ---------- Auth ----------

def auth_signup(email, password, full_name, student_id_number):
    """Open signup — account is created and, once 'Confirm email' is
    disabled in the Supabase Auth dashboard, an active session comes
    back immediately. No OTP step, no waiting."""
    url = f"{Config.SUPABASE_URL}{AUTH}/signup"
    payload = {
        "email": email,
        "password": password,
        "data": {"full_name": full_name, "student_id_number": student_id_number},
    }
    resp = requests.post(url, json=payload, headers=_headers(), timeout=15)
    return _json_or_none(resp), resp.status_code


def auth_login(email, password):
    url = f"{Config.SUPABASE_URL}{AUTH}/token?grant_type=password"
    resp = requests.post(url, json={"email": email, "password": password}, headers=_headers(), timeout=15)
    return _json_or_none(resp), resp.status_code


def auth_get_user(token):
    url = f"{Config.SUPABASE_URL}{AUTH}/user"
    resp = requests.get(url, headers=_headers(token), timeout=15)
    return _json_or_none(resp), resp.status_code


# ---------- REST (respects RLS via the forwarded token) ----------

def rest_request(method, path, token=None, params=None, json_body=None, prefer=None):
    url = f"{Config.SUPABASE_URL}{REST}/{path}"
    headers = _headers(token)
    if prefer:
        headers["Prefer"] = prefer
    resp = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=15)
    return _json_or_none(resp), resp.status_code


# ---------- RPC (increment_view, verify_student, etc.) ----------

def rpc(fn_name, token=None, payload=None):
    url = f"{Config.SUPABASE_URL}{REST}/rpc/{fn_name}"
    resp = requests.post(url, headers=_headers(token), json=payload or {}, timeout=15)
    return _json_or_none(resp), resp.status_code
