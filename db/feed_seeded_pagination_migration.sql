-- ============================================================
-- SEEDED FEED PAGINATION
--
-- The `feed` view (see feed_randomization_migration.sql) orders by
-- power(random(), ...) with zero seeding — every single query gets an
-- independent random order, including paginated offset requests. This
-- means offset 30 on a second page has no real relationship to what
-- offset 0 showed on the first: posts get skipped, duplicated, or
-- reordered on any re-fetch. In practice this is what made reacting
-- to a post look like it "disappeared and got replaced by another" —
-- any incidental re-fetch reshuffled the entire feed underneath it.
--
-- Fix: a seeded version of the same weighted-random formula. The
-- caller (routes/posts.py) generates one seed per feed session on the
-- frontend and reuses it across every paginated request until the
-- next manual refresh — same seed always produces the same order for
-- a given post (via hashtext, deterministic), so pagination is
-- finally stable. A fresh seed on each real feed load still gives the
-- "reload and the order shifts" behavior the randomization work was
-- originally going for.
-- ============================================================

create or replace function feed_seeded(p_seed text default null)
returns setof feed
language sql
stable
as $$
  select p.*, feed_score(p.view_count, p.reaction_count, p.search_hit_count) as score
  from posts p
  where p.status = 'active'
  order by power(
    (abs(hashtext(p.id::text || coalesce(p_seed, ''))) % 1000000)::float / 1000000,
    1.0 / (feed_score(p.view_count, p.reaction_count, p.search_hit_count) + 1)
  ) desc;
$$;

grant execute on function feed_seeded(text) to anon, authenticated;
