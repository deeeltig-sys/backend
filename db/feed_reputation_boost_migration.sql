-- ============================================================
-- FEED REACH BOOST — the actual "engagement earns reach" mechanic.
--
-- Builds on growth_loop_migration.sql's weekly points/tier system
-- (points_tier(), calendar-week reset) rather than the 30-day rolling
-- window badges use — reach should reflect THIS week's activity, and
-- a hard Monday reset is what stops a high scorer from staying
-- permanently boosted off activity from a month ago.
--
-- Three deliberate design constraints, all there to stop this from
-- becoming a spam-to-reach exploit:
--
-- 1. BOUNDED, NOT PROPORTIONAL. The multiplier maps to the same 4
--    tiers users already see (Newcomer/Active/Connector/Campus
--    Voice), capped at 1.5x — a Campus Voice account gets HALF again
--    the reach of a Newcomer's post at equal per-post engagement,
--    not 10x. feed_affinity_multiplier() already goes up to 6.0 for
--    your own posts and 5.0 for mutual friends — reach from being
--    active stays deliberately smaller than reach from being someone
--    the viewer actually knows, so this amplifies relevance, it
--    doesn't override it.
--
-- 2. NEVER BELOW 1.0. A Newcomer's multiplier is 1.0 — the same as
--    today, no boost, no penalty. This is additive reach for the
--    engaged, never a de-facto shadowban for someone who's just new
--    or quiet. Punishing low points would be a much worse mechanic
--    than rewarding high points.
--
-- 3. BATCH-COMPUTED, NOT LIVE. Same reasoning evaluate_badges()
--    already documents: aggregating every user's weekly points on
--    every single feed request would be a full-table scan per page
--    load. This writes a plain numeric column instead, refreshed on
--    a schedule, so feed_seeded_for_viewer() just reads it off the
--    users row it already joins.
-- ============================================================

alter table users add column if not exists weekly_reach_multiplier numeric not null default 1.0;

create or replace function reach_multiplier_for_tier(p_tier text)
returns numeric
language sql immutable
as $$
  select case p_tier
    when 'Campus Voice' then 1.5
    when 'Connector'    then 1.3
    when 'Active'        then 1.15
    else 1.0
  end;
$$;

-- Batch refresh — recomputes every active user's tier from THIS
-- week's points (same query shape as leaderboard_this_week()) and
-- writes the resulting multiplier. Cheap to run often: one aggregate
-- over user_activity_points, one update, no per-user round trips.
create or replace function refresh_weekly_reach_multipliers()
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  with weekly as (
    select user_id, sum(points)::int as pts
    from user_activity_points
    where created_at >= date_trunc('week', now())
    group by user_id
  )
  update users u
  set weekly_reach_multiplier = reach_multiplier_for_tier(points_tier(coalesce(w.pts, 0)))
  from weekly w
  where u.id = w.user_id;

  -- Anyone with zero points this week (no row in `weekly` at all —
  -- last week's top scorer included) resets to 1.0. Without this,
  -- Monday's calendar reset on the points side would leave last
  -- week's multiplier stuck on their row forever, since the UPDATE
  -- above only touches users who show up in `weekly`.
  update users u
  set weekly_reach_multiplier = 1.0
  where weekly_reach_multiplier <> 1.0
    and not exists (
      select 1 from user_activity_points p
      where p.user_id = u.id and p.created_at >= date_trunc('week', now())
    );
end;
$$;

-- ---- Wire the multiplier into actual feed distribution ----
-- Same shape as feed_affinity_migration.sql's feed_seeded_for_viewer,
-- with one added factor. Affinity and reach are independent
-- multipliers on the same feed_score() base — a fresh post from a
-- Campus Voice friend gets recency decay, the 5.0 mutual-friend
-- affinity boost, AND the 1.5 reach boost, all three compounding;
-- an equally fresh post from a Campus Voice stranger only gets
-- recency + the 1.5 reach boost, still ranked below your friends'
-- posts by a wide margin. That ordering is the point.
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
             * feed_affinity_multiplier(p.author_id, p_viewer_id)
             * coalesce(u.weekly_reach_multiplier, 1.0) as score,
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

-- ============================================================
-- ANTI-SPAM GUARD — raises the stakes on an existing gap.
--
-- Before this migration, points only moved a leaderboard number —
-- low value to game. Now points move actual reach, which makes
-- "post constantly, earn points, get seen more" a real exploit path
-- that didn't matter as much before. This caps how many posts per
-- rolling 24h actually earn points (and by extension count toward
-- weekly_reach_multiplier) WITHOUT capping how many posts someone
-- can make — posting stays unlimited, only the reward for posting
-- past the cap goes to zero for that day.
-- ============================================================

create or replace function points_and_quest_on_post() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_recent_point_earning_posts int;
begin
  if new.status = 'active' and new.repost_of is null then
    select count(*) into v_recent_point_earning_posts
    from user_activity_points
    where user_id = new.author_id
      and source_type = 'post'
      and created_at >= now() - interval '24 hours';

    -- 5 point-earning posts per rolling 24h is generous for a real
    -- student's day (checked against nothing on this platform posting
    -- automatically) while making a spam script's return-on-effort
    -- flatten out fast instead of scaling linearly forever.
    if v_recent_point_earning_posts < 5 then
      perform award_points(new.author_id, 3, 'post', new.id);
    end if;

    -- Quest progress ("post twice this week") stays uncapped — a
    -- checklist quest is inherently bounded by its own target_count
    -- already, so it doesn't need a second cap layered on top.
    perform record_quest_progress(new.author_id, 'post');
  end if;
  return new;
end;
$$;
drop trigger if exists trg_points_on_post on posts;
create trigger trg_points_on_post after insert on posts
for each row execute function points_and_quest_on_post();

-- ============================================================
-- Run once now so reach reflects current standing immediately,
-- don't wait for the first scheduled refresh:
select refresh_weekly_reach_multipliers();

-- Schedule the refresh — every 3 hours keeps reach reasonably fresh
-- through the week without recomputing on every request. Also runs
-- once right after the Monday points reset so multipliers drop back
-- to 1.0 for everyone quickly, not up to 3 hours late:
--
--   select cron.schedule('refresh-reach-multipliers', '0 */3 * * *', 'select refresh_weekly_reach_multipliers()');
--   select cron.schedule('reset-reach-multipliers-monday', '5 0 * * 1', 'select refresh_weekly_reach_multipliers()');
-- ============================================================
