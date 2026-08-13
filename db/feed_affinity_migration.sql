-- ============================================================
-- FEED AFFINITY — TikTok-style "all feed" (campus-wide discovery
-- stays mixed in, per product decision — CampusMEET is still
-- growing its user base and a follows-only feed would look dead
-- for most students right now) but HEAVILY weighted toward posts
-- from people the viewer follows or is mutual friends with, plus
-- your own posts, so those reliably surface near the top instead
-- of being an even lottery against the whole campus.
--
-- Layers on top of feed_score() (FIX_feed_score_and_view.sql,
-- which already recency-decays every post) rather than replacing
-- it — a fresh post from someone you follow gets BOTH the recency
-- boost every new post gets AND the affinity multiplier below, so
-- it's very likely (not database-guaranteed, this is still a
-- weighted-random draw by design) to land first for that viewer
-- specifically, without removing randomness for everyone else's
-- posts in the pool.
--
-- Also fixes a real bug found alongside this: routes/posts.py's
-- /feed route has never actually called feed_seeded() (see
-- feed_seeded_pagination_migration.sql) — it queried the plain
-- `feed` view directly, so every single request (including
-- paginated ones) got its own independent random draw with no
-- seed at all. feed_seeded_for_viewer() below is what the route
-- now calls instead, restoring stable pagination as a side effect.
-- ============================================================

create or replace function feed_affinity_multiplier(p_author_id uuid, p_viewer_id uuid)
returns numeric
language sql
stable
as $$
  select case
    when p_viewer_id is null then 1.0                 -- logged-out: no relationship to weigh by
    when p_author_id = p_viewer_id then 6.0            -- your own post
    when exists (
      select 1 from friendships f
      where (f.user_a = p_viewer_id and f.user_b = p_author_id)
         or (f.user_b = p_viewer_id and f.user_a = p_author_id)
    ) then 5.0                                          -- mutual friend
    when exists (
      select 1 from follows fo
      where fo.follower_id = p_viewer_id and fo.following_id = p_author_id
    ) then 3.0                                          -- one-way follow
    else 1.0                                             -- discovery pool — the "TikTok" part
  end;
$$;

grant execute on function feed_affinity_multiplier(uuid, uuid) to anon, authenticated;


create or replace function feed_seeded_for_viewer(p_seed text default null, p_viewer_id uuid default null)
returns setof feed
language sql
stable
as $$
  select ranked.id, ranked.university_id, ranked.author_id, ranked.content, ranked.image_url,
         ranked.repost_of, ranked.group_id, ranked.audience, ranked.view_count,
         ranked.search_hit_count, ranked.reaction_count, ranked.report_count, ranked.status,
         ranked.created_at, ranked.score, ranked.author_full_name, ranked.author_avatar_url,
         ranked.author_verified, ranked.comment_count
  from (
    select p.id, p.university_id, p.author_id, p.content, p.image_url, p.repost_of, p.group_id,
           p.audience, p.view_count, p.search_hit_count, p.reaction_count, p.report_count,
           p.status, p.created_at,
           feed_score(p.view_count, p.reaction_count, p.search_hit_count, p.created_at)
             * feed_affinity_multiplier(p.author_id, p_viewer_id) as score,
           u.full_name as author_full_name, u.avatar_url as author_avatar_url,
           u.verified_at is not null as author_verified, p.comment_count
    from posts p
    left join users u on u.id = p.author_id
    where p.status = 'active'::post_status
  ) ranked
  order by power(
    (abs(hashtext(ranked.id::text || coalesce(p_seed, ''))) % 1000000)::float / 1000000,
    1.0 / (ranked.score + 1)
  ) desc;
$$;

grant execute on function feed_seeded_for_viewer(text, uuid) to anon, authenticated;

-- Sanity check after running: a fresh post from someone you follow
-- should land in roughly the first few rows for your own p_viewer_id,
-- while the same post sits much further back (or not visible at all
-- without affinity) for a random other viewer's uuid.
-- select id, author_id, score from feed_seeded_for_viewer('test-seed', '<your-user-id>'::uuid) limit 10;
