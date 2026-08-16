-- ============================================================
-- OKYEAME + CAMPUSMEET HQ
--
-- Two features sharing one migration since the second depends on the
-- access-control primitive the first introduces:
--
-- 1. Okyeame — a hidden, owner-only panel for posting platform-wide
--    announcements as a dedicated official account. Posts land in the
--    normal `posts` table and flow through the app exactly like any
--    other post (reactions, comments, realtime — all just work).
--
-- 2. CampusMEET HQ — a public, prominently-linked feed for publicly
--    acknowledging any notable person on or around the platform, not
--    just donors. Its own table, owner-only to write, public to read.
-- ============================================================

-- ------------------------------------------------------------
-- Access control
-- ------------------------------------------------------------
-- is_owner is deliberately separate from the existing `role` enum
-- (student/moderator/admin) rather than adding a new enum value —
-- every existing admin-access check in this codebase tests
-- `role in ('admin','moderator')`, and adding a new enum value would
-- silently fail all of them, locking the owner out of their own
-- existing admin access. This column is orthogonal to role instead:
-- the owner keeps role = 'admin' (nothing about existing access
-- changes) plus this one additional flag.
--
-- is_official marks the Okyeame system account for display purposes
-- only (gold/sparkle rendering instead of the normal diamond name
-- treatment) — it carries no permissions of its own.
--
-- Neither column is settable through any endpoint, RPC, or admin-UI
-- action anywhere in this codebase — by design, the only way either
-- is ever set is a direct UPDATE run by hand in the Supabase SQL
-- editor. No code path exists that could hand owner access to anyone
-- else, intentionally or by mistake.
alter table users add column if not exists is_owner boolean not null default false;
alter table users add column if not exists is_official boolean not null default false;

create or replace function is_owner()
returns boolean
language sql
stable
as $$
  select exists (
    select 1 from users
    where id = auth.uid() and is_owner = true
  );
$$;

-- ------------------------------------------------------------
-- Feed view: expose author_is_official so the campus-scope filter
-- (routes/posts.py) can special-case Okyeame's posts to bypass
-- per-university scoping — an announcement has to reach every
-- university, not just whichever one the Okyeame account happens to
-- be registered under. This is the exact same column list as
-- FIX_feed_score_and_view.sql (the canonical current version — same
-- feed_score signature, same columns) with one column appended at
-- the end; nothing existing removed or reordered.
--
-- CREATE OR REPLACE, not DROP + CREATE: two functions outside this
-- migration (feed_seeded, feed_seeded_for_viewer) depend on the
-- feed view's type, and dropping it would take them down too, even
-- with CASCADE forcing it through. Postgres allows CREATE OR REPLACE
-- VIEW to add columns at the end without disturbing dependents —
-- exactly this case — as long as nothing existing is removed or
-- reordered, which this doesn't.
create or replace view feed as
select p.id,
    p.university_id,
    p.author_id,
    p.content,
    p.image_url,
    p.repost_of,
    p.group_id,
    p.audience,
    p.view_count,
    p.search_hit_count,
    p.reaction_count,
    p.report_count,
    p.status,
    p.created_at,
    feed_score(p.view_count, p.reaction_count, p.search_hit_count, p.created_at) as score,
    u.full_name as author_full_name,
    u.avatar_url as author_avatar_url,
    u.verified_at is not null as author_verified,
    p.comment_count,
    u.is_official as author_is_official
   from posts p
     left join users u on u.id = p.author_id
  where p.status = 'active'::post_status
  order by power(
    random(),
    1.0 / (feed_score(p.view_count, p.reaction_count, p.search_hit_count, p.created_at) + 1)
  ) desc;

-- ------------------------------------------------------------
-- Announcing as Okyeame
-- ------------------------------------------------------------
-- posts_insert's RLS policy requires author_id = auth.uid() (see
-- rls_policies.sql) — correct and load-bearing for every normal post,
-- and exactly why a plain insert can't be used to post "as" a
-- different account. SECURITY DEFINER is the established pattern
-- this codebase already uses for a controlled, narrow bypass of that
-- kind (see get_other_typing in typing_indicator_migration.sql) —
-- same approach here: the function itself re-checks authorization
-- with is_owner() before doing anything, so this doesn't weaken
-- posts_insert for anyone else, it just gives the owner one specific,
-- audited way through it.
--
-- Deliberately takes no author_id parameter — the caller can never
-- specify who the post is "from", only the owner-only Okyeame account
-- this function is hardcoded to use. That's what stops this from
-- becoming a general "post as anyone" backdoor.
create or replace function announce_as_okyeame(p_content text, p_image_url text default null)
returns posts
language plpgsql
security definer
set search_path = public
as $$
declare
  v_okyeame_id uuid;
  v_university_id uuid;
  v_post posts;
begin
  if not is_owner() then
    raise exception 'not authorized';
  end if;

  select id, university_id into v_okyeame_id, v_university_id
  from users where is_official = true limit 1;
  if v_okyeame_id is null then
    raise exception 'okyeame account not configured — set is_official = true on its users row first';
  end if;

  if p_content is null or length(trim(p_content)) = 0 then
    raise exception 'content cannot be empty';
  end if;

  -- university_id is NOT NULL on posts. The reach-to-every-campus
  -- behavior comes from routes/posts.py's feed filter bypass
  -- (author_is_official.eq.true), not from leaving this column empty
  -- — it still needs a real value to satisfy the constraint, this
  -- just isn't what the feed actually filters on for this account.
  insert into posts (author_id, university_id, content, image_url, status, audience)
  values (v_okyeame_id, v_university_id, trim(p_content), p_image_url, 'active', 'public')
  returning * into v_post;

  return v_post;
end;
$$;

grant execute on function announce_as_okyeame(text, text) to authenticated;

-- ------------------------------------------------------------
-- CampusMEET HQ — public acknowledgment feed
-- ------------------------------------------------------------
-- subject_user_id is nullable and deliberately NOT a hard requirement
-- — the person being acknowledged may not be a registered CampusMEET
-- user at all (a lecturer, an alum, an external supporter), so this
-- can't be a strict FK-only relationship the way most of this schema
-- links to `users`.
create table if not exists spotlights (
  id              uuid primary key default uuid_generate_v4(),
  subject_name    text not null,
  subject_role    text,                          -- free text, e.g. "Lecturer, Computer Science" or "Founding Patron"
  subject_user_id uuid references users(id) on delete set null,
  photo_url       text,
  body            text not null,
  created_by      uuid not null references users(id),
  created_at      timestamptz not null default now()
);

create index if not exists idx_spotlights_created_at on spotlights(created_at desc);

alter table spotlights enable row level security;

-- Public read — CampusMEET HQ is explicitly meant to be a "must-know"
-- feed for everyone, not gated content.
-- drop-then-create rather than a bare create: policies have no
-- IF NOT EXISTS in Postgres, and this migration has already partially
-- run at least once — re-running the bare version hit "already
-- exists" on the very first policy.
drop policy if exists spotlights_select_all on spotlights;
create policy spotlights_select_all on spotlights for select using (true);

-- Write access enforced entirely through is_owner() — deliberately no
-- policy lets a plain admin/moderator insert here, only the owner.
drop policy if exists spotlights_write_owner on spotlights;
create policy spotlights_write_owner on spotlights for insert with check (is_owner());
drop policy if exists spotlights_update_owner on spotlights;
create policy spotlights_update_owner on spotlights for update using (is_owner());
drop policy if exists spotlights_delete_owner on spotlights;
create policy spotlights_delete_owner on spotlights for delete using (is_owner());

-- ============================================================
-- SETUP — run once, by hand, after this migration:
--
-- 1. Sign up a real account through the normal CampusMEET signup flow
--    for "Okyeame" (any university, any valid-format student ID —
--    university no longer matters for reach now that the feed view
--    bypasses campus-scoping for official posts).
--
-- 2. Mark it official:
--      update users set is_official = true where full_name = 'Okyeame';
--
-- 3. Mark your OWN account as owner (replace with your real id/email):
--      update users set is_owner = true where id = '<your-user-id>';
--
-- Verify:
--   select id, full_name, is_owner, is_official from users
--   where is_owner = true or is_official = true;
-- ============================================================
