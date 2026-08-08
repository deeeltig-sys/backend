-- ============================================================
-- SAVED COLLECTIONS — saved_posts stays exactly as it is (a flat
-- user_id/post_id pair); this just adds an optional folder on top.
-- A post with collection_id = null is "uncategorized" — same default
-- bucket IG uses before you ever organize anything.
-- ============================================================

create table saved_collections (
  id         uuid primary key default uuid_generate_v4(),
  user_id    uuid not null references users(id) on delete cascade,
  title      text not null check (char_length(title) between 1 and 60),
  created_at timestamptz not null default now()
);
create index idx_saved_collections_user on saved_collections(user_id);

alter table saved_posts add column collection_id uuid references saved_collections(id) on delete set null;
-- ON DELETE SET NULL is deliberate: deleting a collection un-organizes
-- the posts inside it back into "uncategorized" — it never deletes the
-- saves themselves. Losing a folder shouldn't lose the bookmarks.

create index idx_saved_posts_collection on saved_posts(collection_id) where collection_id is not null;

alter table saved_collections enable row level security;

create policy saved_collections_select_own on saved_collections for select using (user_id = auth.uid());
create policy saved_collections_insert_own on saved_collections for insert with check (user_id = auth.uid());
create policy saved_collections_update_own on saved_collections for update using (user_id = auth.uid());
create policy saved_collections_delete_own on saved_collections for delete using (user_id = auth.uid());

-- ============================================================
-- End saved_collections_migration
-- ============================================================
