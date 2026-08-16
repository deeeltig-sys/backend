-- FIX: feed_seeded_for_viewer() was defined (feed_reputation_boost_migration.sql,
-- Aug 14) before okyeame_migration.sql (Aug 16) appended `author_is_official`
-- to the `feed` view. The function still declares `returns setof feed` but
-- its explicit select list never picks up the new column, so:
--   1. It throws "structure of query does not match function result type"
--      on every call now that `feed` has 19 columns instead of 18.
--   2. Even past that, routes/posts.py filters the RPC result on
--      `author_is_official`, a column this function's select never outputs.
--
-- This re-applies the exact same function body as
-- feed_reputation_boost_migration.sql with one line added: selecting
-- u.is_official as author_is_official, so the function's output finally
-- matches the current `feed` view shape again.

create or replace function feed_seeded_for_viewer(p_seed text default null, p_viewer_id uuid default null)
returns setof feed
language sql
stable
as $$
  select ranked.id, ranked.university_id, ranked.author_id, ranked.content, ranked.image_url,
         ranked.repost_of, ranked.group_id, ranked.audience, ranked.view_count,
         ranked.search_hit_count, ranked.reaction_count, ranked.report_count, ranked.status,
         ranked.created_at, ranked.score, ranked.author_full_name, ranked.author_avatar_url,
         ranked.author_verified, ranked.comment_count, ranked.author_is_official
  from (
    select p.id, p.university_id, p.author_id, p.content, p.image_url, p.repost_of, p.group_id,
           p.audience, p.view_count, p.search_hit_count, p.reaction_count, p.report_count,
           p.status, p.created_at,
           feed_score(p.view_count, p.reaction_count, p.search_hit_count, p.created_at)
             * feed_affinity_multiplier(p.author_id, p_viewer_id)
             * coalesce(u.weekly_reach_multiplier, 1.0) as score,
           u.full_name as author_full_name, u.avatar_url as author_avatar_url,
           u.verified_at is not null as author_verified, p.comment_count,
           u.is_official as author_is_official
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

-- There is a second function with the same fragile pattern:
-- feed_seeded (in feed_affinity_migration.sql / referenced by
-- feed_randomization_migration.sql) — check whether anything still calls
-- it. If routes/posts.py no longer references it, it's dead and safe to
-- drop; if something does still call it, it needs the same
-- author_is_official fix applied.


-- ============================================================
-- FIX 2: private-group membership was fully public.
--
-- group_members_select (db/groups_migration.sql) was `using (true)` —
-- literally anyone, including anonymous requests, could list every
-- member (name, avatar, role, joined_at) of ANY group, private or
-- not. The migration's own comment says this should mirror
-- groups_select (visible to anyone who can see the group), but the
-- policy body never actually encoded that condition. Combined with
-- routes/groups.py's /<group_id>/members route having no auth
-- decorator at all (now fixed separately), this was a real, live
-- privacy leak on every private group on the platform.
--
-- This replaces it with the condition the comment always described:
-- readable if the group is public, OR the caller is a member of it.
--
-- A plain `exists (select 1 from group_members ...)` inside
-- group_members' own SELECT policy would make Postgres re-apply this
-- same policy to evaluate itself — "infinite recursion detected in
-- policy for relation group_members". Routed through a SECURITY
-- DEFINER function instead (same established pattern as is_owner()/
-- is_staff() elsewhere in this codebase) so the membership check
-- runs with RLS bypassed for that one lookup, breaking the cycle.
create or replace function is_group_member(p_group_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from group_members
    where group_id = p_group_id and user_id = auth.uid()
  );
$$;

-- (Postgres policies don't support CREATE OR REPLACE — drop + create.)
drop policy if exists group_members_select on group_members;
create policy group_members_select on group_members
  for select using (
    exists (
      select 1 from groups g
      where g.id = group_members.group_id
        and (g.privacy = 'public' or is_group_member(g.id))
    )
  );

