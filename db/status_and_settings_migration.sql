-- ============================================================
-- STATUS / STORIES — 24-hour expiring updates (Facebook/IG/X-style),
-- plus the two new `users` columns Settings needs for a default chat
-- wallpaper (applied to conversations that haven't set their own).
-- ============================================================

create table if not exists statuses (
  id             uuid primary key default uuid_generate_v4(),
  author_id      uuid not null references users(id) on delete cascade,
  content_type   text not null check (content_type in ('image', 'text')),
  image_url      text,
  text_content   text check (text_content is null or char_length(text_content) <= 280),
  background_color text default '#7a2436',
  created_at     timestamptz not null default now(),
  expires_at     timestamptz not null default (now() + interval '24 hours'),
  check (
    (content_type = 'image' and image_url is not null) or
    (content_type = 'text' and text_content is not null)
  )
);

create index if not exists idx_statuses_author on statuses(author_id);
create index if not exists idx_statuses_expires on statuses(expires_at);

create table if not exists status_views (
  status_id  uuid not null references statuses(id) on delete cascade,
  viewer_id  uuid not null references users(id) on delete cascade,
  viewed_at  timestamptz not null default now(),
  primary key (status_id, viewer_id)
);

alter table statuses enable row level security;
alter table status_views enable row level security;

-- Visible to any signed-in student, same openness as the main feed —
-- statuses aren't a follower-only DM-adjacent thing here, they're a
-- public campus broadcast, same audience as a regular post.
drop policy if exists statuses_select_active on statuses;
create policy statuses_select_active on statuses
  for select using (expires_at > now());

drop policy if exists statuses_insert_own on statuses;
create policy statuses_insert_own on statuses
  for insert with check (author_id = auth.uid());

drop policy if exists statuses_delete_own on statuses;
create policy statuses_delete_own on statuses
  for delete using (author_id = auth.uid());

drop policy if exists status_views_insert_own on status_views;
create policy status_views_insert_own on status_views
  for insert with check (viewer_id = auth.uid());

-- A viewer can see their own view rows; a status author can see who
-- viewed THEIR status (the "seen by" list) — nobody else.
drop policy if exists status_views_select on status_views;
create policy status_views_select on status_views
  for select using (
    viewer_id = auth.uid()
    or exists (select 1 from statuses s where s.id = status_views.status_id and s.author_id = auth.uid())
  );

-- Expired rows are just filtered out by the SELECT policy above, not
-- hard-deleted — actual cleanup (freeing the storage objects) needs a
-- scheduled job, same caveat as the chat 60-day purge:
--   select cron.schedule('purge-expired-statuses', '0 4 * * *',
--     $$delete from statuses where expires_at < now() - interval '7 days'$$);
-- (kept 7 days past expiry rather than deleting instantly, in case a
-- report/moderation review needs to look at something that just expired)

-- ============================================================
-- SETTINGS — default chat wallpaper, used when a specific
-- conversation hasn't set its own override (see messages.py).
-- ============================================================
alter table users add column if not exists default_wallpaper text default 'system'
  check (default_wallpaper in ('black','white','system','cream','green','custom'));
alter table users add column if not exists default_wallpaper_url text;

-- Safety net: if chat_overhaul_migration.sql already ran with its
-- original column default of 'system' on conversation_user_state,
-- drop that default now so a brand-new conversation can actually
-- fall back to this user-level default instead of always reading
-- 'system' from that column's default value.
alter table conversation_user_state alter column wallpaper drop default;
