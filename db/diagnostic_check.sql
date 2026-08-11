-- ============================================================
-- DIAGNOSTIC — read-only, changes nothing. Run this in the Supabase
-- SQL editor and paste the output back. It answers three open
-- questions in one pass instead of guessing from migration history:
--
--   1. Does the reaction_type enum still have 'doubt', or is it
--      already renamed to 'like'?
--   2. Does bump_reaction_count() have SECURITY DEFINER set?
--      (fix_reaction_count_guard.sql — if this shows security_definer
--      = false, that fix was never run and reaction counts are still
--      being silently reverted by guard_post_moderation_fields.)
--   3. Does send_push_for_notification() reference new.recipient_id
--      (broken, rolls back every reaction) or new.user_id (fixed)?
-- ============================================================

-- 1. Enum values on reaction_type
select enumlabel as reaction_type_value
from pg_enum
where enumtypid = 'reaction_type'::regtype
order by enumsortorder;

-- 2. security definer status of the two count triggers
select
  p.proname as function_name,
  p.prosecdef as security_definer
from pg_proc p
where p.proname in ('bump_reaction_count', 'bump_comment_count', 'bump_report_count');

-- 3. Which column send_push_for_notification actually reads —
--    look for 'recipient_id' (broken) vs 'user_id' (fixed) in the
--    output below.
select pg_get_functiondef(p.oid) as send_push_for_notification_source
from pg_proc p
where p.proname = 'send_push_for_notification';

-- 4. status_reactions check constraint — confirm it says 'like' not
--    'doubt' (only relevant once/if that table gets used).
select pg_get_constraintdef(c.oid) as status_reactions_type_check
from pg_constraint c
where c.conrelid = 'status_reactions'::regclass
  and c.contype = 'c';
