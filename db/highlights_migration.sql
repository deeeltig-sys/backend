-- ============================================================
-- STORY HIGHLIGHTS — IG-style: a Status is 24-hour and expiring
-- (see status_and_settings_migration.sql); a Highlight is a
-- permanent, user-curated collection pinned to a profile.
--
-- Deliberately NOT a foreign key to `statuses`. A status row is
-- allowed to disappear (RLS hides it after expiry, and a cron job
-- eventually purges it — see the comment in that migration). A
-- highlight has to survive that. So "adding a status to a highlight"
-- COPIES its content_type/image_url/text_content/background_color
-- into a new, independent row here — after that point the two are
-- unrelated records that happen to have started from the same photo.
-- ============================================================

create table status_highlights (
  id         uuid primary key default uuid_generate_v4(),
  user_id    uuid not null references users(id) on delete cascade,
  title      text not null check (char_length(title) between 1 and 40),
  order_index int not null default 0,
  created_at timestamptz not null default now()
);
create index idx_status_highlights_user on status_highlights(user_id);

create table status_highlight_items (
  id               uuid primary key default uuid_generate_v4(),
  highlight_id     uuid not null references status_highlights(id) on delete cascade,
  content_type     text not null check (content_type in ('image', 'text')),
  image_url        text,
  text_content     text check (text_content is null or char_length(text_content) <= 280),
  background_color text default '#7a2436',
  order_index      int not null default 0,
  created_at       timestamptz not null default now(),
  check (
    (content_type = 'image' and image_url is not null) or
    (content_type = 'text' and text_content is not null)
  )
);
create index idx_status_highlight_items_highlight on status_highlight_items(highlight_id);

-- ============================================================
-- RLS — highlights are a public-profile feature (same audience as
-- the profile they're pinned to); only the owner can curate them.
-- Ownership for items is checked via the parent highlight, since
-- items don't carry user_id directly.
-- ============================================================
alter table status_highlights enable row level security;
alter table status_highlight_items enable row level security;

create policy status_highlights_select_all on status_highlights for select using (true);
create policy status_highlights_insert_own on status_highlights for insert with check (user_id = auth.uid());
create policy status_highlights_update_own on status_highlights for update using (user_id = auth.uid());
create policy status_highlights_delete_own on status_highlights for delete using (user_id = auth.uid());

create policy status_highlight_items_select_all on status_highlight_items for select using (true);

create policy status_highlight_items_insert_own on status_highlight_items
  for insert with check (
    exists (select 1 from status_highlights h where h.id = highlight_id and h.user_id = auth.uid())
  );

create policy status_highlight_items_delete_own on status_highlight_items
  for delete using (
    exists (select 1 from status_highlights h where h.id = highlight_id and h.user_id = auth.uid())
  );

-- ============================================================
-- End highlights_migration
-- ============================================================
