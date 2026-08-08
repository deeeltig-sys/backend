-- ============================================================
-- STEP 1 — see what's actually live right now, before changing
-- anything. Run this by itself first.
-- ============================================================

select exists (
  select 1 from pg_proc where proname = 'feed_score' and pronargs = 4
) as four_arg_feed_score_exists;
-- false confirms the bug: every migration calling the 4-arg version
-- has been silently failing to apply.

select pg_get_viewdef('feed'::regclass, true) as live_feed_view_definition;
-- Read this to see which columns are actually there right now —
-- specifically check for author_full_name, audience, comment_count.

-- ============================================================
-- STEP 2 — the actual fix: define the missing 4-arg overload
-- (recency-decayed, so a 2-week-old post doesn't keep outscoring
-- everything new forever) and rebuild `feed` as one canonical
-- version with every column every other migration expected it to
-- have. This supersedes audience_migration.sql, groups_migration.sql,
-- reposts_migration.sql, and v2_migration.sql's versions — run this
-- instead of trying to re-run any of those.
-- ============================================================

create or replace function feed_score(
  p_views int, p_reactions int, p_search_hits int, p_created_at timestamptz
) returns numeric
language sql immutable as $$
  select ((p_views * 1.0) + (p_reactions * 2.0) + (p_search_hits * 1.0))
         / power(extract(epoch from (now() - p_created_at)) / 3600 + 2, 1.4);
$$;
-- Reactions weighted 2x views (a reaction is a stronger signal than a
-- view), divided by a growing power of hours-since-posted — this is
-- what makes a 3-day-old post naturally fade in favor of newer ones
-- even if its raw totals are higher, without an explicit cutoff/cron.

drop view if exists feed;

create view feed as
select p.id,
    p.university_id,
    p.author_id,
    p.content,
    p.image_url,
    p.repost_of,
    p.group_id,
    p.audience,
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

-- ============================================================
-- STEP 3 — confirm it's the version you'd expect now:
-- ============================================================
select pg_get_viewdef('feed'::regclass, true);
