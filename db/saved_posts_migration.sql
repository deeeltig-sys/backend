-- ============================================================
-- SAVED POSTS — bookmarking. Didn't exist before; needed now that
-- Save is one of the overflow-menu actions.
-- ============================================================

create table if not exists saved_posts (
  user_id    uuid not null references users(id) on delete cascade,
  post_id    uuid not null references posts(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, post_id)
);

create index if not exists idx_saved_posts_user on saved_posts(user_id, created_at desc);

alter table saved_posts enable row level security;

drop policy if exists saved_posts_own on saved_posts;
create policy saved_posts_own on saved_posts
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());
