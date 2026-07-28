-- ============================================================
-- REPOSTS — a repost is just a `posts` row that points at another
-- post via `repost_of`, optionally with its own commentary in
-- `content`. This is the same design X/Twitter uses (a Retweet is a
-- tweet with a pointer), which is why it slots into the EXISTING
-- feed/scoring/reaction system for free — a repost gets its own
-- reactions, comments, and feed ranking just like any other post,
-- no separate code path needed for any of that.
-- ============================================================

alter table posts add column if not exists repost_of uuid references posts(id) on delete set null;
create index if not exists idx_posts_repost_of on posts(repost_of);

-- content was NOT NULL with a 1-2000 char check — relaxed so a PURE
-- repost (no added commentary) can have empty content, while a
-- regular post or a quote-repost with commentary still requires text.
alter table posts alter column content drop not null;

alter table posts drop constraint if exists posts_content_check;
alter table posts add constraint posts_content_check check (
  (repost_of is not null and char_length(coalesce(content, '')) <= 2000)
  or (repost_of is null and content is not null and char_length(content) between 1 and 2000)
);

-- The `feed` view lists explicit columns rather than `p.*` (see
-- v2_migration.sql), so it needs repost_of added explicitly or every
-- repost silently vanishes from API responses despite existing in
-- the table.
drop view if exists feed;

create view feed as
select p.id,
    p.university_id,
    p.author_id,
    p.content,
    p.image_url,
    p.repost_of,
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
    p.comment_count
   from posts p
     left join users u on u.id = p.author_id
  where p.status = 'active'::post_status
  order by power(
    random(),
    1.0 / (feed_score(p.view_count, p.reaction_count, p.search_hit_count, p.created_at) + 1)
  ) desc;
