-- ============================================================
-- ACCOUNT-LEVEL LOGIN LOCKOUT — closes the gap the existing per-IP
-- rate limit (Flask-Limiter, "10 per minute" on /login) doesn't
-- cover: a distributed attack using many IPs against ONE target
-- account never crosses any single IP's threshold. This tracks
-- failures against the email being attacked instead, regardless of
-- where the requests come from.
--
-- Accessed pre-authentication (someone attempting to log in has no
-- session yet), so the backend calls these as the anon key rather
-- than a user JWT. Rather than open the table itself to anon (which
-- would let anyone query /rest/v1/login_attempts directly and learn
-- whether a given email has been targeted — a small but real
-- reconnaissance leak), everything goes through three narrow
-- SECURITY DEFINER functions instead — same pattern already used for
-- announce_as_okyeame(). The table has NO policies granting anon or
-- authenticated access at all; only these functions (and staff, for
-- investigation) can touch it.
-- ============================================================

create table if not exists login_attempts (
  email           text primary key,
  failed_count    int not null default 0,
  locked_until    timestamptz,
  last_attempt_at timestamptz not null default now()
);

alter table login_attempts enable row level security;

-- Staff-only read, for investigating who's been targeting an account.
-- No insert/update/delete policy for anyone — all writes happen
-- through the SECURITY DEFINER functions below, which bypass RLS as
-- their owning role (same mechanism announce_as_okyeame() relies on).
drop policy if exists login_attempts_staff_select on login_attempts;
create policy login_attempts_staff_select on login_attempts
  for select using (is_staff());

-- Locks after 5 failed attempts, for 15 minutes. Called BEFORE
-- attempting auth — a check, not a write; doesn't consume an attempt
-- itself, so checking lockout status repeatedly can't accidentally
-- extend or trigger a lock on its own.
create or replace function check_login_lockout(p_email text)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  v_locked_until timestamptz;
begin
  select locked_until into v_locked_until
  from login_attempts where email = lower(trim(p_email));

  if v_locked_until is not null and v_locked_until > now() then
    return extract(epoch from (v_locked_until - now()))::int;
  end if;
  return 0;
end;
$$;

grant execute on function check_login_lockout(text) to anon, authenticated;

-- Called after a failed auth attempt. Increments the counter and
-- locks once it reaches 5 — a failed attempt while already locked
-- doesn't extend the lock further, it just re-confirms it.
create or replace function record_login_failure(p_email text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_email text := lower(trim(p_email));
  v_count int;
begin
  insert into login_attempts (email, failed_count, last_attempt_at)
  values (v_email, 1, now())
  on conflict (email) do update
    set failed_count = login_attempts.failed_count + 1,
        last_attempt_at = now()
  returning failed_count into v_count;

  if v_count >= 5 then
    update login_attempts
    set locked_until = now() + interval '15 minutes'
    where email = v_email;
  end if;
end;
$$;

grant execute on function record_login_failure(text) to anon, authenticated;

-- Called after a successful login — clears the slate, same as any
-- normal "reset on success" lockout design.
create or replace function record_login_success(p_email text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  delete from login_attempts where email = lower(trim(p_email));
end;
$$;

grant execute on function record_login_success(text) to anon, authenticated;

