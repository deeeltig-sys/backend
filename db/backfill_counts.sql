-- ============================================================
-- BACKFILL (corrected) — the first version of this script silently
-- did nothing: guard_post_moderation_fields reverted the very update
-- this script was trying to make, because running it directly in the
-- SQL editor isn't "staff" and didn't set the system_update exemption
-- flag that bump_reaction_count/bump_comment_count normally set
-- before touching these columns. This version sets that flag first,
-- inside an explicit transaction so it stays in effect for every
-- statement below.
-- ============================================================

begin;

select set_config('campmeet.system_update', 'on', true);

-- Recalculate reaction_count from the actual reactions table.
update posts p
set reaction_count = sub.cnt
from (
  select post_id, count(*) as cnt
  from reactions
  group by post_id
) sub
where p.id = sub.post_id
  and p.reaction_count <> sub.cnt;

-- Posts with zero real reactions but a non-zero stored count.
update posts
set reaction_count = 0
where reaction_count <> 0
  and id not in (select distinct post_id from reactions);

-- Same repair for comment_count, counting only active (non-removed) comments.
update posts p
set comment_count = sub.cnt
from (
  select post_id, count(*) as cnt
  from comments
  where status = 'active'
  group by post_id
) sub
where p.id = sub.post_id
  and p.comment_count <> sub.cnt;

update posts
set comment_count = 0
where comment_count <> 0
  and id not in (select distinct post_id from comments where status = 'active');

commit;
