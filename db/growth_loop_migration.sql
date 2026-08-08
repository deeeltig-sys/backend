-- ============================================================
-- GROWTH LOOP — referral attribution, visible points, weekly
-- leaderboard, quest rotation, status_posted wiring, Campus
-- Ambassador badge. Builds directly on reputation_system_migration.sql
-- — same functions (award_points, record_quest_progress), same
-- append-only/rolling-window philosophy, nothing bypassed.
--
-- RUN IN THIS ORDER, AS SEPARATE EXECUTIONS (Postgres won't let a
-- new enum value be used in the same transaction that added it):
--   STEP 1 → run alone, wait for it to finish
--   STEP 2 → run everything after it together
-- ============================================================

-- ============================================================
-- STEP 1 — new enum value. MUST be its own execution.
-- ============================================================
alter type quest_action add value if not exists 'referral_activated';
alter type points_source add value if not exists 'referral_activated';


-- ============================================================
-- STEP 2 — everything else. Run only after Step 1 has committed.
-- ============================================================

-- ---- Referral attribution ----
alter table users add column if not exists referred_by uuid references users(id);
alter table users add column if not exists referral_credited boolean not null default false;
-- referral_credited is a one-shot guard, not a running total — it
-- only ever flips false->true once, the moment this specific user's
-- first post credits their referrer. Doesn't violate the
-- points-as-events principle: the actual reward still goes through
-- award_points()/record_quest_progress() as a normal event, this
-- column just stops it from firing twice for the same person.

create index if not exists idx_users_referred_by on users(referred_by);

-- Signup trigger now also resolves referred_by from metadata, in
-- addition to everything university_signup_migration.sql already
-- does. A referred_by that doesn't correspond to a real user is
-- silently ignored (left null) rather than raising — a malformed or
-- spoofed ?ref= value in a shared link should never be able to break
-- signup for the person who followed it.
create or replace function handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  resolved_university_id uuid;
  raw_university_id text;
  raw_university_name text;
  raw_referred_by text;
  resolved_referred_by uuid;
begin
  raw_university_id := new.raw_user_meta_data ->> 'university_id';
  raw_university_name := new.raw_user_meta_data ->> 'university_name';

  if raw_university_id is not null and raw_university_id <> '' then
    resolved_university_id := raw_university_id::uuid;
  elsif raw_university_name is not null and trim(raw_university_name) <> '' then
    resolved_university_id := get_or_create_university(raw_university_name);
  else
    raise exception 'university is required';
  end if;

  raw_referred_by := new.raw_user_meta_data ->> 'referred_by';
  if raw_referred_by is not null and raw_referred_by <> '' then
    begin
      select id into resolved_referred_by from users where id = raw_referred_by::uuid;
    exception when invalid_text_representation then
      resolved_referred_by := null;
    end;
  end if;

  insert into public.users (id, university_id, full_name, student_id_number, verified_at, referred_by)
  values (
    new.id,
    resolved_university_id,
    new.raw_user_meta_data ->> 'full_name',
    nullif(new.raw_user_meta_data ->> 'student_id_number', ''),
    null,
    resolved_referred_by
  );

  return new;
end;
$$;

-- ---- Referral activation: credited on the REFERRED user's first
-- post, to the REFERRER, not at raw signup (farmable) ----
create or replace function credit_referral_on_first_post()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_referrer uuid;
  v_already boolean;
begin
  select referred_by, referral_credited into v_referrer, v_already
  from users where id = new.author_id;

  if v_referrer is not null and not v_already and new.status = 'active' and new.repost_of is null then
    perform award_points(v_referrer, 10, 'referral_activated', new.author_id);
    perform record_quest_progress(v_referrer, 'referral_activated');
    update users set referral_credited = true where id = new.author_id;
  end if;
  return new;
end;
$$;
drop trigger if exists trg_credit_referral on posts;
create trigger trg_credit_referral after insert on posts
for each row execute function credit_referral_on_first_post();

-- ---- status_posted was defined in the quest_action enum from the
-- start but never actually wired to anything — genuinely dormant,
-- not something this feature broke. Wiring it now gives the quest
-- pool below a real fourth action type to rotate with, instead of
-- inventing something new. ----
create or replace function points_and_quest_on_status() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  perform award_points(new.author_id, 1, 'quest_completed', new.id);
  perform record_quest_progress(new.author_id, 'status_posted');
  return new;
end;
$$;
drop trigger if exists trg_points_on_status on statuses;
create trigger trg_points_on_status after insert on statuses
for each row execute function points_and_quest_on_status();

-- ---- Campus Ambassador badge — extends evaluate_badges()'s existing
-- threshold branch with a second metric, same pattern follower_count
-- already uses, not a parallel system. ----
create or replace function evaluate_badges()
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_badge record;
  v_period text;
begin
  for v_badge in select * from badge_types where is_active loop
    v_period := case when v_badge.repeatable then to_char(now(), 'YYYY-MM') else 'once' end;

    if v_badge.rule_type = 'relative_percentile' then
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
      insert into user_badges (user_id, badge_type_id, period_key, context_snapshot)
      select u.id, v_badge.id, v_period, jsonb_build_object('value', u.follower_count)
      from users u
      where v_badge.rule_config->>'metric' = 'follower_count'
        and u.follower_count >= (v_badge.rule_config->>'value')::int
      on conflict (user_id, badge_type_id, period_key) do nothing;

      -- NEW: referral_count metric, e.g. {"metric": "referral_count", "value": 15}.
      -- Counted from user_activity_points, not a mutated column — stays
      -- consistent with "points are events, not a running total".
      insert into user_badges (user_id, badge_type_id, period_key, context_snapshot)
      select p.user_id, v_badge.id, v_period, jsonb_build_object('value', p.referral_count)
      from (
        select user_id, count(*) as referral_count
        from user_activity_points
        where source_type = 'referral_activated'
        group by user_id
      ) p
      where v_badge.rule_config->>'metric' = 'referral_count'
        and p.referral_count >= (v_badge.rule_config->>'value')::int
      on conflict (user_id, badge_type_id, period_key) do nothing;

    elsif v_badge.rule_type = 'tenure' then
      insert into user_badges (user_id, badge_type_id, period_key, context_snapshot)
      select u.id, v_badge.id, v_period, jsonb_build_object('joined_at', u.created_at)
      from users u
      where u.created_at < (v_badge.rule_config->>'before')::timestamptz
      on conflict (user_id, badge_type_id, period_key) do nothing;
    end if;
  end loop;
end;
$$;

insert into badge_types (code, name, description, icon, rule_type, rule_config, repeatable)
values ('campus_ambassador', 'Campus Ambassador', 'Brought 15 new students who each made their first post.', '🎖️', 'threshold', '{"metric": "referral_count", "value": 15}', false)
on conflict (code) do nothing;

-- ---- Referral quest — deliberately NOT in the rotating pool below.
-- Growth is something you always want, unlike "react 5 times" which
-- benefits from novelty — an always-on quest here is the right call,
-- not an oversight. ----
insert into quests (code, title, description, cadence, action_type, target_count, points_reward, is_active)
values ('monthly_referral_2', 'Bring New Students', 'Invite 2 friends who join and make their first post this month.', 'monthly', 'referral_activated', 2, 15, true)
on conflict (code) do nothing;


-- ============================================================
-- QUEST ROTATION POOL
--
-- Addresses the "same 3 quests forever" staleness problem directly:
-- a pool of variants per cadence, of which only a subset is
-- is_active at any time. rotate_active_quests() swaps which ones are
-- live — same is_active column that already existed, no schema
-- change to how quests are queried anywhere else in the app.
-- ============================================================

alter table quests add column if not exists is_rotating boolean not null default false;

-- Existing 3 quests join the weekly/monthly rotating pools instead of
-- running unchanged forever. Referral quest above stays untouched —
-- is_rotating defaults false, so it's permanently unaffected by rotation.
update quests set is_rotating = true
where code in ('weekly_post_2', 'weekly_react_5', 'monthly_comment_10');

-- New pool variants — same 4 action types the app already tracks
-- (post/comment/reaction_given/status_posted), different targets and
-- framing, so rotation actually feels different week to week rather
-- than just reshuffling identical copies.
insert into quests (code, title, description, cadence, action_type, target_count, points_reward, is_active, is_rotating) values
  ('weekly_post_1', 'Share Something', 'Make 1 post this week.', 'weekly', 'post', 1, 3, false, true),
  ('weekly_comment_5', 'Join the Conversation', 'Comment on 5 posts this week.', 'weekly', 'comment', 5, 5, false, true),
  ('weekly_status_3', 'Post Your Story', 'Share 3 statuses this week.', 'weekly', 'status_posted', 3, 3, false, true),
  ('weekly_react_10', 'Show Some Love', 'React to 10 posts this week.', 'weekly', 'reaction_given', 10, 5, false, true),
  ('monthly_post_8', 'Stay Active', 'Post 8 times this month.', 'monthly', 'post', 8, 10, false, true),
  ('monthly_status_10', 'Story Regular', 'Share 10 statuses this month.', 'monthly', 'status_posted', 10, 8, false, true)
on conflict (code) do nothing;

create or replace function rotate_active_quests(p_cadence quest_cadence, p_active_count int)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update quests set is_active = false
  where cadence = p_cadence and is_rotating = true;

  update quests set is_active = true
  where id in (
    select id from quests
    where cadence = p_cadence and is_rotating = true
    order by random()
    limit p_active_count
  );
end;
$$;

-- Run once now so there's an active set immediately, don't wait for
-- the first scheduled rotation:
select rotate_active_quests('weekly', 3);
select rotate_active_quests('monthly', 2);


-- ============================================================
-- VISIBLE POINTS + WEEKLY LEADERBOARD
--
-- Deliberately calendar-week-based (Monday reset), NOT the 30-day
-- rolling window user_rolling_points() uses for badges. A leaderboard
-- that never fully resets calcifies around whoever joined earliest —
-- exactly the early-user-advantage problem already flagged elsewhere
-- in this project. A weekly reset means a student who joined
-- yesterday competes on equal footing with one who joined 3 years ago.
-- ============================================================

create or replace function points_tier(p_points int)
returns text
language sql immutable
as $$
  select case
    when p_points >= 200 then 'Campus Voice'
    when p_points >= 100 then 'Connector'
    when p_points >= 40  then 'Active'
    else 'Newcomer'
  end;
$$;
-- Thresholds are round numbers, not a hard science — easy to retune
-- later since this is one function, not scattered logic.

create or replace function my_weekly_standing()
returns table (points int, rank bigint, tier text)
language sql
security definer
stable
set search_path = public
as $$
  with weekly as (
    select user_id, sum(points)::int as pts
    from user_activity_points
    where created_at >= date_trunc('week', now())
    group by user_id
  ),
  ranked as (
    select user_id, pts, rank() over (order by pts desc) as rnk
    from weekly
  )
  select coalesce(r.pts, 0), r.rnk, points_tier(coalesce(r.pts, 0))
  from ranked r
  where r.user_id = auth.uid()
  union all
  select 0, null, points_tier(0)
  where not exists (select 1 from ranked where user_id = auth.uid());
$$;
grant execute on function my_weekly_standing() to authenticated;

create or replace function leaderboard_this_week(p_scope text default 'university', p_limit int default 20)
returns table (user_id uuid, full_name text, avatar_url text, verified boolean, points int, tier text)
language sql
security definer
stable
set search_path = public
as $$
  select u.id, u.full_name, u.avatar_url, u.verified_at is not null, w.pts, points_tier(w.pts)
  from (
    select user_id, sum(points)::int as pts
    from user_activity_points
    where created_at >= date_trunc('week', now())
    group by user_id
  ) w
  join users u on u.id = w.user_id
  where p_scope = 'global'
     or u.university_id = (select university_id from users where id = auth.uid())
  order by w.pts desc
  limit p_limit;
$$;
grant execute on function leaderboard_this_week(text, int) to authenticated;


-- ============================================================
-- Schedule these two (see the existing evaluate-badges example for
-- the pattern) — run once, after enabling pg_cron:
--
--   select cron.schedule('rotate-weekly-quests', '0 0 * * 1', $$select rotate_active_quests('weekly', 3)$$);
--   select cron.schedule('rotate-monthly-quests', '0 0 1 * *', $$select rotate_active_quests('monthly', 2)$$);
-- ============================================================
