-- ============================================================
-- CAMPUS PLATFORM — CURRENT SCHEMA (consolidated)
-- ProjectX Web Development · USTED, Kumasi
-- Base schema + migration v1.1 + migration v1.2, applied in order.
-- This is the single source of truth as of this point in build.
-- ============================================================
--
-- Verification model: OPEN SIGNUP. Real email (any provider), no
-- OTP, no upload flow, no bottleneck. student_id_number is
-- format-checked and unique, but is NOT proof of identity on its
-- own. verified_at starts null for every new account and is only
-- ever set by an admin/moderator via the verify_student() RPC —
-- a manual, human "I know this person is really USTED" action.
-- Verified students get the gold "(USTED)" mark on the masthead.
-- V2: same mechanism, scoped per university_id.
-- ============================================================

create extension if not exists "uuid-ossp";

-- ============ ENUMS ============
create type user_role      as enum ('student','moderator','admin');
create type user_status    as enum ('active','suspended','deactivated');
create type post_status    as enum ('active','removed','flagged');
create type reaction_type  as enum ('fire','cosign','doubt','yawa');
create type report_target  as enum ('post','comment','user');
create type report_reason  as enum (
  'sexual_harassment','tribal_harassment','bullying',
  'personal_harassment','false_info_defamation','impersonation','other'
);
create type report_status  as enum ('pending','reviewed','actioned');

-- ============ UNIVERSITIES ============
-- university_id is first-class from day one, hardcoded to one row for V1.
-- This is the expansion hook for V2 multi-school + per-school verification.
create table universities (
  id          uuid primary key default uuid_generate_v4(),
  name        text not null,
  short_code  text not null unique,
  created_at  timestamptz not null default now()
);

insert into universities (name, short_code)
values ('University of Skills Training and Entrepreneurial Development, Kumasi', 'USTED');

-- ============ USERS ============
create table users (
  id                 uuid primary key references auth.users(id) on delete cascade,
  university_id      uuid not null references universities(id),
  full_name          text not null,
  student_id_number  text not null unique
                      check (student_id_number ~ '^52\d{8}$'),
  student_email      text,               -- real email, any provider; recovery/notifications only
  avatar_url         text,
  standing_count     int not null default 0,
  role               user_role   not null default 'student',
  status             user_status not null default 'active',
  verified_at        timestamptz,        -- null until an admin manually verifies
  verified_by        uuid references users(id),
  created_at         timestamptz not null default now()
);

create index idx_users_university  on users(university_id);
create index idx_users_verified_at on users(verified_at);

-- Single source of truth for staff bypass — referenced by every RLS
-- policy below instead of being duplicated table by table.
create or replace function is_staff()
returns boolean
language sql
stable
as $$
  select exists (
    select 1 from users
    where id = auth.uid() and role in ('moderator','admin')
  );
$$;

-- Auto-create the public.users row when Supabase Auth creates the
-- underlying auth.users row. full_name / student_id_number arrive
-- via signUp()'s options.data metadata from the client.
-- verified_at is always null here — open signup, no auto-verify.
create or replace function handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  usted_id uuid;
begin
  select id into usted_id from universities where short_code = 'USTED';

  begin
    insert into public.users (id, university_id, full_name, student_id_number, verified_at)
    values (
      new.id,
      usted_id,
      new.raw_user_meta_data ->> 'full_name',
      new.raw_user_meta_data ->> 'student_id_number',
      null
    );
  exception
    when unique_violation then
      raise exception 'that student ID is already registered';
    when check_violation then
      raise exception 'student ID must be 10 digits starting with 52';
  end;

  return new;
end;
$$;

create trigger trg_handle_new_auth_user
after insert on auth.users
for each row execute function handle_new_auth_user();

-- Manual verification action — the "Verify USTED" admin button.
-- security definer so verified_by is set server-side and can't be
-- spoofed by a client passing an arbitrary admin id.
create or replace function verify_student(p_user_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not is_staff() then
    raise exception 'only staff can verify students';
  end if;

  update users
  set verified_at = now(),
      verified_by = auth.uid()
  where id = p_user_id;
end;
$$;

revoke all on function verify_student(uuid) from public;
grant execute on function verify_student(uuid) to authenticated;

-- Companion action: un-verify, for mistaken approvals or later
-- status changes (e.g. graduation).
create or replace function unverify_student(p_user_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not is_staff() then
    raise exception 'only staff can unverify students';
  end if;

  update users
  set verified_at = null,
      verified_by = null
  where id = p_user_id;
end;
$$;

revoke all on function unverify_student(uuid) from public;
grant execute on function unverify_student(uuid) to authenticated;

-- ============ POSTS ============
create table posts (
  id                uuid primary key default uuid_generate_v4(),
  university_id     uuid not null references universities(id),
  author_id         uuid not null references users(id) on delete cascade,
  content           text not null check (char_length(content) between 1 and 2000),
  image_url         text,
  view_count        int not null default 0,
  search_hit_count  int not null default 0,
  reaction_count    int not null default 0,
  report_count      int not null default 0,
  status            post_status not null default 'active',
  created_at        timestamptz not null default now()
);

create index idx_posts_university  on posts(university_id);
create index idx_posts_author      on posts(author_id);
create index idx_posts_status      on posts(status);
create index idx_posts_created_at  on posts(created_at desc);

-- Blocks authors from touching moderation-only fields directly.
-- status can only move via staff action; report_count/reaction_count
-- can only move via their own triggers below.
create or replace function guard_post_moderation_fields()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if not is_staff() then
    if new.status <> old.status then
      raise exception 'only staff can change post status';
    end if;
    if new.report_count <> old.report_count then
      new.report_count := old.report_count;
    end if;
    if new.reaction_count <> old.reaction_count then
      new.reaction_count := old.reaction_count;
    end if;
  end if;
  return new;
end;
$$;

create trigger trg_guard_post_moderation_fields
before update on posts
for each row execute function guard_post_moderation_fields();

-- ============ REACTIONS ============
-- One live reaction per user per post (switchable, not stackable).
create table reactions (
  id          uuid primary key default uuid_generate_v4(),
  post_id     uuid not null references posts(id) on delete cascade,
  user_id     uuid not null references users(id) on delete cascade,
  type        reaction_type not null,
  created_at  timestamptz not null default now(),
  unique (post_id, user_id)
);

create index idx_reactions_user on reactions(user_id);

-- ============ COMMENTS ============
create table comments (
  id          uuid primary key default uuid_generate_v4(),
  post_id     uuid not null references posts(id) on delete cascade,
  author_id   uuid not null references users(id) on delete cascade,
  content     text not null check (char_length(content) between 1 and 1000),
  status      post_status not null default 'active',
  created_at  timestamptz not null default now()
);

create index idx_comments_post on comments(post_id);

-- ============ REPORTS ============
create table reports (
  id           uuid primary key default uuid_generate_v4(),
  reporter_id  uuid not null references users(id) on delete cascade,
  target_type  report_target not null,
  target_id    uuid not null,
  reason       report_reason not null,
  status       report_status not null default 'pending',
  created_at   timestamptz not null default now()
);

create index idx_reports_target on reports(target_type, target_id);

-- ============ HIDDEN POSTS / BLOCKS ============
create table hidden_posts (
  user_id     uuid not null references users(id) on delete cascade,
  post_id     uuid not null references posts(id) on delete cascade,
  created_at  timestamptz not null default now(),
  primary key (user_id, post_id)
);

create table blocks (
  blocker_id  uuid not null references users(id) on delete cascade,
  blocked_id  uuid not null references users(id) on delete cascade,
  created_at  timestamptz not null default now(),
  primary key (blocker_id, blocked_id),
  check (blocker_id <> blocked_id)
);

-- ============================================================
-- TRIGGERS — keep denormalized counts in sync automatically
-- ============================================================

create or replace function bump_reaction_count()
returns trigger language plpgsql as $$
begin
  if (tg_op = 'INSERT') then
    update posts set reaction_count = reaction_count + 1 where id = new.post_id;
  elsif (tg_op = 'DELETE') then
    update posts set reaction_count = reaction_count - 1 where id = old.post_id;
  end if;
  return null;
end; $$;

create trigger trg_reaction_count
after insert or delete on reactions
for each row execute function bump_reaction_count();

create or replace function bump_report_count()
returns trigger language plpgsql as $$
begin
  if new.target_type = 'post' then
    update posts set report_count = report_count + 1 where id = new.target_id;
  end if;
  return new;
end; $$;

create trigger trg_report_count
after insert on reports
for each row execute function bump_report_count();

-- Standing = total reactions received across a student's own posts.
create or replace function bump_standing_count()
returns trigger language plpgsql as $$
declare
  post_author uuid;
begin
  select author_id into post_author from posts where id = coalesce(new.post_id, old.post_id);
  if (tg_op = 'INSERT') then
    update users set standing_count = standing_count + 1 where id = post_author;
  elsif (tg_op = 'DELETE') then
    update users set standing_count = standing_count - 1 where id = post_author;
  end if;
  return null;
end; $$;

create trigger trg_standing_count
after insert or delete on reactions
for each row execute function bump_standing_count();

-- ============================================================
-- VIEW / SEARCH COUNTERS — RPCs, not direct table updates.
-- posts_update_own (below) only allows the author or staff to
-- UPDATE a post, but views/search hits come from any other
-- student browsing. These bypass RLS for just these two columns.
-- ============================================================
create or replace function increment_view(p_post_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update posts set view_count = view_count + 1 where id = p_post_id and status = 'active';
end;
$$;

create or replace function increment_search_hit(p_post_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update posts set search_hit_count = search_hit_count + 1 where id = p_post_id and status = 'active';
end;
$$;

revoke all on function increment_view(uuid) from public;
revoke all on function increment_search_hit(uuid) from public;
grant execute on function increment_view(uuid) to authenticated;
grant execute on function increment_search_hit(uuid) to authenticated;

-- ============================================================
-- FEED — score = views + reactions + search_hits, equal weight
-- to start (Blueprint §4/§11: tune once real usage data exists).
-- ============================================================
create or replace function feed_score(
  p_views int, p_reactions int, p_search_hits int
) returns numeric
language sql immutable as $$
  select (p_views * 1.0) + (p_reactions * 1.0) + (p_search_hits * 1.0);
$$;

create or replace view feed as
select
  p.*,
  feed_score(p.view_count, p.reaction_count, p.search_hit_count) as score
from posts p
where p.status = 'active'
order by score desc, p.created_at desc;

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================
alter table users        enable row level security;
alter table posts        enable row level security;
alter table reactions    enable row level security;
alter table comments     enable row level security;
alter table reports      enable row level security;
alter table hidden_posts enable row level security;
alter table blocks       enable row level security;

-- USERS: profiles are public; you can only edit your own; staff can moderate.
create policy users_select_all   on users for select using (true);
create policy users_update_own   on users for update using (id = auth.uid());
create policy users_staff_update on users for update using (is_staff());

-- POSTS: active posts are public; author sees their own regardless of status.
-- No hard-delete policy for authors — self-delete goes through UPDATE to
-- status = 'removed' instead, so reaction/report history isn't orphaned.
-- Staff retain a real DELETE path for legal takedowns.
create policy posts_select      on posts for select using (status = 'active' or author_id = auth.uid() or is_staff());
create policy posts_insert      on posts for insert with check (author_id = auth.uid());
create policy posts_update_own  on posts for update using (author_id = auth.uid() or is_staff());
create policy posts_delete_staff on posts for delete using (is_staff());

-- REACTIONS: visible to all, only the reactor can create/remove their own.
create policy reactions_select     on reactions for select using (true);
create policy reactions_insert     on reactions for insert with check (user_id = auth.uid());
create policy reactions_delete_own on reactions for delete using (user_id = auth.uid());

-- COMMENTS: same shape as posts.
create policy comments_select     on comments for select using (status = 'active' or author_id = auth.uid() or is_staff());
create policy comments_insert     on comments for insert with check (author_id = auth.uid());
create policy comments_update_own on comments for update using (author_id = auth.uid() or is_staff());
create policy comments_delete_own on comments for delete using (author_id = auth.uid() or is_staff());

-- REPORTS: reporter sees only their own; staff see all; only staff can update status.
create policy reports_insert            on reports for insert with check (reporter_id = auth.uid());
create policy reports_select_own_staff  on reports for select using (reporter_id = auth.uid() or is_staff());
create policy reports_staff_update      on reports for update using (is_staff());

-- HIDDEN POSTS: fully private to the user who hid the post.
create policy hidden_posts_all_own on hidden_posts for all
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- BLOCKS: fully private to the user who created the block.
create policy blocks_all_own on blocks for all
  using (blocker_id = auth.uid()) with check (blocker_id = auth.uid());

-- ============================================================
-- End current schema
-- ============================================================
