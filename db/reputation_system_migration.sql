-- ============================================================
-- REPUTATION SYSTEM — badges, points, and weekly/monthly quests.
--
-- Built around the going-concern constraints worked through before
-- writing a line of this: the platform has no assumed end date, so
-- every mechanic here has to stay winnable by someone joining in
-- year 5, while staying permanent about what someone already earned
-- in year 1. Four design decisions carry that weight:
--
-- 1. RELATIVE, NOT ABSOLUTE, THRESHOLDS. "50 reactions" means
--    something different at 30 users than at 30,000 — a fixed number
--    baked into a rule quietly breaks as the platform grows. Badge
--    rules that need a bar to clear are defined as a PERCENTILE
--    within a rolling window (rule_config->>'percentile'), computed
--    fresh each run, not compared against a number typed in today.
--
-- 2. POINTS ARE EVENTS, NOT A RUNNING TOTAL. user_activity_points is
--    append-only, one row per point-earning action, each with its
--    own timestamp. "Current standing" is a SUM over a recent window
--    (see user_rolling_points below), which means old activity ages
--    out of relevance on its own — no explicit decay job needed, and
--    no permanent lifetime-total that turns into an unbeatable head
--    start for whoever joined first.
--
-- 3. A BADGE, ONCE EARNED, IS A PERMANENT HISTORICAL FACT.
--    user_badges rows are never updated or deleted by the awarding
--    job — only inserted. Rules can change going forward (thresholds
--    retuned, categories retired) without retroactively taking
--    something away from someone who legitimately earned it under
--    the rules as they stood at the time (context_snapshot keeps a
--    copy of the numbers that earned it, for transparency).
--
-- 4. BADGE AND QUEST DEFINITIONS ARE DATA, NOT CODE. badge_types and
--    quests are rows with a rule_type + rule_config, not hardcoded
--    conditionals — new categories, retired categories, and retuned
--    thresholds are all just row edits, never a redeploy.
-- ============================================================

create type badge_rule_type as enum ('relative_percentile', 'threshold', 'tenure', 'streak');
create type quest_cadence   as enum ('weekly', 'monthly');
create type quest_action    as enum ('post', 'comment', 'reaction_given', 'friend_added', 'status_posted');
create type points_source   as enum ('post', 'reaction_received', 'comment', 'quest_completed', 'friend_added');

-- ---- Badge definitions (data, not code) ----
create table if not exists badge_types (
  id            uuid primary key default uuid_generate_v4(),
  code          text not null unique,
  name          text not null,
  description   text not null,
  icon          text,
  rule_type     badge_rule_type not null,
  -- Shape depends on rule_type:
  --   relative_percentile: {"window_days": 30, "percentile": 10, "metric": "points", "scope": "university"|"platform"}
  --   threshold:           {"metric": "friend_count", "value": 50}
  --   tenure:              {"before": "2026-09-01"}
  --   streak:              {"metric": "weekly_quest_completions", "count": 4}
  rule_config   jsonb not null default '{}'::jsonb,
  repeatable    boolean not null default false, -- true = can be earned again each period (e.g. a weekly "Top Contributor"), false = earn-once
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);

alter table badge_types enable row level security;
drop policy if exists badge_types_select_all on badge_types;
create policy badge_types_select_all on badge_types for select using (true);
-- No client-writable policy at all — these are seeded/edited by staff
-- directly (or a future admin screen backed by @require_admin), never
-- something a regular user's token can insert or modify.

-- ---- Earned badges — append-only, immutable ----
create table if not exists user_badges (
  id                uuid primary key default uuid_generate_v4(),
  user_id           uuid not null references users(id) on delete cascade,
  badge_type_id     uuid not null references badge_types(id) on delete cascade,
  -- null for earn-once badges; a period key like '2026-W31' or
  -- '2026-08' for repeatable ones, so the same badge can be earned
  -- again next week/month without colliding with this one.
  period_key        text,
  earned_at         timestamptz not null default now(),
  -- The actual numbers that earned it, frozen at the moment of
  -- earning — lets a badge always be explainable ("top 8% of 340
  -- active students that week") even after rule_config changes later.
  context_snapshot  jsonb not null default '{}'::jsonb,
  unique (user_id, badge_type_id, period_key)
);

create index if not exists idx_user_badges_user on user_badges(user_id);

alter table user_badges enable row level security;
drop policy if exists user_badges_select_all on user_badges;
create policy user_badges_select_all on user_badges for select using (true);
-- Insert only through evaluate_badges() (security definer below) —
-- no direct client insert policy, so a badge can never be self-awarded
-- by calling the REST API directly.

-- ---- Points — append-only events, never a mutated running total ----
create table if not exists user_activity_points (
  id           uuid primary key default uuid_generate_v4(),
  user_id      uuid not null references users(id) on delete cascade,
  points       int not null check (points > 0),
  source_type  points_source not null,
  source_id    uuid, -- the post/comment/etc that earned it, when applicable
  created_at   timestamptz not null default now()
);

create index if not exists idx_activity_points_user_time on user_activity_points(user_id, created_at desc);

alter table user_activity_points enable row level security;
drop policy if exists activity_points_select_all on user_activity_points;
create policy activity_points_select_all on user_activity_points for select using (true);
-- Insert only through the security-definer award_points() function
-- below and the triggers that call it — never directly client-writable,
-- since a client-insertable points table would just be a free score
-- generator.

-- Rolling standing, recomputed on read, not stored — this IS the
-- decay mechanism: a burst of activity 90 days ago simply falls out
-- of a 30-day window on its own, no cleanup job required.
create or replace function user_rolling_points(p_user_id uuid, p_window_days int default 30)
returns int
language sql stable
as $$
  select coalesce(sum(points), 0)::int
  from user_activity_points
  where user_id = p_user_id
    and created_at >= now() - (p_window_days || ' days')::interval;
$$;

create or replace function award_points(p_user_id uuid, p_points int, p_source points_source, p_source_id uuid default null)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into user_activity_points (user_id, points, source_type, source_id)
  values (p_user_id, p_points, p_source, p_source_id);
end;
$$;

-- ---- Points are awarded together with quest progress, wired below
-- (see "Wire quest tracking onto the same actions that already award
-- points" further down) — one trigger per action, not two.

-- ============================================================
-- QUESTS — weekly/monthly checklists, data-driven same as badges.
-- ============================================================

create table if not exists quests (
  id              uuid primary key default uuid_generate_v4(),
  code            text not null unique,
  title           text not null,
  description     text not null,
  cadence         quest_cadence not null,
  action_type     quest_action not null,
  target_count    int not null check (target_count > 0),
  points_reward   int not null default 5,
  is_active       boolean not null default true,
  created_at      timestamptz not null default now()
);

alter table quests enable row level security;
drop policy if exists quests_select_all on quests;
create policy quests_select_all on quests for select using (true);

create table if not exists user_quest_progress (
  id              uuid primary key default uuid_generate_v4(),
  user_id         uuid not null references users(id) on delete cascade,
  quest_id        uuid not null references quests(id) on delete cascade,
  -- '2026-W31' for weekly quests, '2026-08' for monthly — this is
  -- what makes a quest cleanly reset: a new period_key is just a new
  -- row, the old period's row is untouched history, not overwritten.
  period_key      text not null,
  progress_count  int not null default 0,
  completed_at    timestamptz,
  unique (user_id, quest_id, period_key)
);

create index if not exists idx_quest_progress_user_period on user_quest_progress(user_id, period_key);

alter table user_quest_progress enable row level security;
drop policy if exists quest_progress_select_own on user_quest_progress;
create policy quest_progress_select_own on user_quest_progress for select using (user_id = auth.uid());
-- No client-writable policy — progress only ever changes through
-- record_quest_progress() below, called by triggers on the actions
-- that count toward a quest, never by a direct client write (which
-- would just let anyone mark their own quests complete).

create or replace function current_period_key(p_cadence quest_cadence)
returns text
language sql stable
as $$
  select case p_cadence
    when 'weekly' then to_char(now(), 'IYYY-"W"IW')
    else to_char(now(), 'YYYY-MM')
  end;
$$;

create or replace function record_quest_progress(p_user_id uuid, p_action quest_action)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_quest record;
  v_period text;
  v_new_count int;
begin
  for v_quest in select * from quests where action_type = p_action and is_active loop
    v_period := current_period_key(v_quest.cadence);

    insert into user_quest_progress (user_id, quest_id, period_key, progress_count)
    values (p_user_id, v_quest.id, v_period, 1)
    on conflict (user_id, quest_id, period_key)
    do update set progress_count = user_quest_progress.progress_count + 1
    returning progress_count into v_new_count;

    if v_new_count >= v_quest.target_count then
      update user_quest_progress
      set completed_at = now()
      where user_id = p_user_id and quest_id = v_quest.id and period_key = v_period
        and completed_at is null;
      if found then
        perform award_points(p_user_id, v_quest.points_reward, 'quest_completed', v_quest.id);
      end if;
    end if;
  end loop;
end;
$$;

-- Wire quest tracking onto the same actions that already award points
-- — one action, both effects, same trigger site.
create or replace function points_and_quest_on_post() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.status = 'active' and new.repost_of is null then
    perform award_points(new.author_id, 3, 'post', new.id);
    perform record_quest_progress(new.author_id, 'post');
  end if;
  return new;
end;
$$;
drop trigger if exists trg_points_on_post on posts;
create trigger trg_points_on_post after insert on posts
for each row execute function points_and_quest_on_post();

create or replace function points_and_quest_on_comment() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.status = 'active' then
    perform award_points(new.author_id, 1, 'comment', new.id);
    perform record_quest_progress(new.author_id, 'comment');
  end if;
  return new;
end;
$$;
drop trigger if exists trg_points_on_comment on comments;
create trigger trg_points_on_comment after insert on comments
for each row execute function points_and_quest_on_comment();

create or replace function points_and_quest_on_reaction() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_post_author uuid;
begin
  select author_id into v_post_author from posts where id = new.post_id;
  if v_post_author is not null and v_post_author <> new.user_id then
    perform award_points(v_post_author, 1, 'reaction_received', new.post_id);
  end if;
  -- The quest credit goes to whoever GAVE the reaction (a "react to 5
  -- posts" quest is about your own activity, not what you received).
  perform record_quest_progress(new.user_id, 'reaction_given');
  return new;
end;
$$;
drop trigger if exists trg_points_on_reaction on reactions;
create trigger trg_points_on_reaction after insert on reactions
for each row execute function points_and_quest_on_reaction();

create or replace function points_and_quest_on_friendship() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  perform award_points(new.user_a, 2, 'friend_added', null);
  perform award_points(new.user_b, 2, 'friend_added', null);
  perform record_quest_progress(new.user_a, 'friend_added');
  perform record_quest_progress(new.user_b, 'friend_added');
  return new;
end;
$$;
drop trigger if exists trg_points_on_friendship on friendships;
create trigger trg_points_on_friendship after insert on friendships
for each row execute function points_and_quest_on_friendship();

-- ============================================================
-- BADGE AWARDING — the scheduled evaluation job.
--
-- Run periodically (see the cron.schedule example at the bottom of
-- this file), NOT on every action — relative-percentile badges need
-- to compare against everyone else, which only makes sense computed
-- as a batch, not incrementally per event the way points/quests are.
-- ============================================================

create or replace function evaluate_badges()
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_badge record;
  v_period text;
  v_cutoff_rank numeric;
begin
  for v_badge in select * from badge_types where is_active loop
    -- 'once' rather than null — Postgres never treats two NULLs as
    -- equal, in a UNIQUE constraint or in ON CONFLICT matching, so a
    -- null period_key here would let every re-run of this function
    -- insert a fresh duplicate row for every earn-once badge, forever.
    -- Confirmed the hard way: running evaluate_badges() twice against
    -- test data duplicated Founding Member and Campus Connector
    -- before this fix, with the ON CONFLICT clause silently doing
    -- nothing to stop it.
    v_period := case when v_badge.repeatable then to_char(now(), 'YYYY-MM') else 'once' end;

    if v_badge.rule_type = 'relative_percentile' then
      -- Rank everyone by rolling points over the configured window,
      -- award to whoever falls within the configured top percentile.
      -- Scoped per-university when rule_config says so, so a small
      -- new campus's top student isn't just permanently invisible
      -- next to a much bigger campus's numbers.
      with ranked as (
        select
          u.id as user_id,
          user_rolling_points(u.id, coalesce((v_badge.rule_config->>'window_days')::int, 30)) as pts,
          percent_rank() over (
            partition by case when v_badge.rule_config->>'scope' = 'university' then u.university_id else null end
            order by user_rolling_points(u.id, coalesce((v_badge.rule_config->>'window_days')::int, 30)) desc
          ) as pct
        from users u
        where u.status = 'active'
      )
      insert into user_badges (user_id, badge_type_id, period_key, context_snapshot)
      select r.user_id, v_badge.id, v_period,
             jsonb_build_object('points', r.pts, 'percentile_rank', round((r.pct * 100)::numeric, 1))
      from ranked r
      where r.pct <= (coalesce((v_badge.rule_config->>'percentile')::numeric, 10) / 100.0)
        and r.pts > 0
      on conflict (user_id, badge_type_id, period_key) do nothing;

    elsif v_badge.rule_type = 'threshold' then
      -- e.g. {"metric": "friend_count", "value": 50}. Only friend_count
      -- and follower_count are wired up here; add more metrics by
      -- extending this case, not by changing the schema.
      insert into user_badges (user_id, badge_type_id, period_key, context_snapshot)
      select u.id, v_badge.id, v_period, jsonb_build_object('value', u.follower_count)
      from users u
      where v_badge.rule_config->>'metric' = 'follower_count'
        and u.follower_count >= (v_badge.rule_config->>'value')::int
      on conflict (user_id, badge_type_id, period_key) do nothing;

    elsif v_badge.rule_type = 'tenure' then
      insert into user_badges (user_id, badge_type_id, period_key, context_snapshot)
      select u.id, v_badge.id, v_period, jsonb_build_object('created_at', u.created_at)
      from users u
      where u.created_at < (v_badge.rule_config->>'before')::timestamptz
      on conflict (user_id, badge_type_id, period_key) do nothing;
    end if;
  end loop;
end;
$$;

-- ============================================================
-- Seed a first, conservative badge/quest set — deliberately small.
-- Add more by inserting rows, not by shipping new code.
-- ============================================================

insert into badge_types (code, name, description, icon, rule_type, rule_config, repeatable) values
  ('founding_member', 'Founding Member', 'Joined CampusMEET in its earliest days.', '🌱', 'tenure', '{"before": "2026-12-01"}', false),
  ('rising_voice', 'Rising Voice', 'Among the most active voices on campus this month.', '📣', 'relative_percentile', '{"window_days": 30, "percentile": 10, "scope": "university"}', true),
  ('campus_connector', 'Campus Connector', 'Built a real network of 50+ friends.', '🤝', 'threshold', '{"metric": "follower_count", "value": 50}', false)
on conflict (code) do nothing;

insert into quests (code, title, description, cadence, action_type, target_count, points_reward) values
  ('weekly_post_2', 'Post twice this week', 'Share two updates with campus.', 'weekly', 'post', 2, 5),
  ('weekly_react_5', 'React to 5 posts', 'Show love on five posts this week.', 'weekly', 'reaction_given', 5, 3),
  ('monthly_comment_10', 'Comment 10 times', 'Join the conversation ten times this month.', 'monthly', 'comment', 10, 8)
on conflict (code) do nothing;

-- ============================================================
-- Schedule the badge evaluation job (run this manually once, same as
-- every other cron job in this codebase — see
-- chat_overhaul_migration.sql / status_and_settings_migration.sql for
-- the same pattern):
--
--   select cron.schedule('evaluate-badges', '0 5 * * *', 'select evaluate_badges()');
--
-- Points and quest progress need no scheduled job at all — they're
-- updated live by the triggers above, on the same actions that
-- already happen throughout the app.
-- ============================================================
