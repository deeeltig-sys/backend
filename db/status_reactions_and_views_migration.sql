-- ============================================================
-- STATUS VIEW COUNTS + REACTIONS + SHARE-TO-STORY
--
-- Three additions on top of status_and_settings_migration.sql:
--
-- 1. Public view_count — status_views RLS only lets a viewer see
--    their own row, or the author see all of theirs (the private
--    "seen by" list). That's correct for the list itself, but it
--    means nobody else can COUNT other people's view rows through
--    PostgREST to show a public number. Denormalized counter instead
--    (same pattern as posts.view_count) — a trigger increments it on
--    every new view insert, so displaying the count needs no RLS
--    exception at all.
--
-- 2. status_reactions — same four reaction types as posts (fire/
--    cosign/like/yawa), same one-reaction-per-person-per-item shape.
--    Kept in sync with reaction_like_rename_migration.sql from the
--    start (posts.reactions uses the reaction_type enum, this table
--    uses its own text + check constraint, so the two never inherit
--    each other's changes automatically — this one has to be updated
--    by hand whenever the enum is).
--
-- 3. original_post_id on statuses — mirrors posts.original_post_id
--    (the existing repost pattern) so "Share to Story" creates a real
--    status that links back to the source post rather than being a
--    disconnected copy.
-- ============================================================

alter table statuses add column if not exists view_count integer not null default 0;
alter table statuses add column if not exists original_post_id uuid references posts(id) on delete set null;

create or replace function increment_status_view_count()
returns trigger
language plpgsql
as $$
begin
  update statuses set view_count = view_count + 1 where id = new.status_id;
  return new;
end;
$$;

drop trigger if exists trg_increment_status_view_count on status_views;
create trigger trg_increment_status_view_count
  after insert on status_views
  for each row execute function increment_status_view_count();

create table if not exists status_reactions (
  status_id  uuid not null references statuses(id) on delete cascade,
  user_id    uuid not null references users(id) on delete cascade,
  type       text not null check (type in ('fire', 'cosign', 'like', 'yawa')),
  created_at timestamptz not null default now(),
  primary key (status_id, user_id)
);

alter table status_reactions enable row level security;

-- Same openness as post reactions — anyone can see reaction counts
-- and who reacted, same as a public campus broadcast.
drop policy if exists status_reactions_select on status_reactions;
create policy status_reactions_select on status_reactions
  for select using (true);

drop policy if exists status_reactions_upsert_own on status_reactions;
create policy status_reactions_upsert_own on status_reactions
  for insert with check (user_id = auth.uid());

drop policy if exists status_reactions_update_own on status_reactions;
create policy status_reactions_update_own on status_reactions
  for update using (user_id = auth.uid());

drop policy if exists status_reactions_delete_own on status_reactions;
create policy status_reactions_delete_own on status_reactions
  for delete using (user_id = auth.uid());

create index if not exists idx_status_reactions_status on status_reactions(status_id);
