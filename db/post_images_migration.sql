-- ============================================================
-- MULTI-PHOTO POSTS (carousel) — posts.image_url stays exactly as it
-- is (a single URL) so every existing query/view/frontend path that
-- reads it directly keeps working untouched. This table holds the
-- FULL image set for any post that has more than one photo;
-- posts.image_url is always set to images[0]'s url for a post
-- created this way, purely as a backward-compatible convenience.
-- ============================================================

create table post_images (
  id          uuid primary key default uuid_generate_v4(),
  post_id     uuid not null references posts(id) on delete cascade,
  image_url   text not null,
  order_index int not null default 0,
  created_at  timestamptz not null default now()
);
create index idx_post_images_post on post_images(post_id, order_index);

-- ============================================================
-- RLS — same public-read / author-only-write shape as post_mentions.
-- ============================================================
alter table post_images enable row level security;

create policy post_images_select_all on post_images for select using (true);

create policy post_images_insert_own_post on post_images
  for insert with check (
    exists (select 1 from posts p where p.id = post_id and p.author_id = auth.uid())
  );

-- ============================================================
-- End post_images_migration
-- ============================================================
