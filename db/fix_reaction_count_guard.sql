-- ============================================================
-- FIX (part 2) — fix_guard_exemption.sql's own comment claims it
-- fixed reaction_count, but the code in that file only ever
-- redefined bump_comment_count() to set the campmeet.system_update
-- exemption flag. bump_reaction_count() (and bump_report_count(),
-- same bug) were never touched, so guard_post_moderation_fields()
-- has been silently reverting every reaction-count increment ever
-- since — the flag it checks for was simply never being set for
-- reactions. This is what's been reported as "hitting a reaction
-- doesn't add numbers."
--
-- guard_post_moderation_fields() itself is untouched here — it
-- already checks the exemption flag correctly (that part of the
-- earlier fix was right). Only the two trigger functions that were
-- missed are being fixed.
-- ============================================================

-- guard_post_moderation_fields() is redefined here too, identically to
-- fix_guard_exemption.sql — not because it needs to change again, but
-- so this migration is self-sufficient. I can't verify from the repo
-- alone whether fix_guard_exemption.sql was ever actually run against
-- your live database; CREATE OR REPLACE makes re-running it here
-- harmless either way.
create or replace function guard_post_moderation_fields()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if current_setting('campmeet.system_update', true) = 'on' then
    return new;
  end if;

  if not is_staff() then
    if new.status <> old.status then
      raise exception 'only staff can change post status';
    end if;
    if new.report_count <> old.report_count then
      new.report_count := old.report_count;
    end if;
    if new.reaction_count <> old.reaction_count then
      new.reaction_count := old.reaction_count;
    end if;
    if new.comment_count <> old.comment_count then
      new.comment_count := old.comment_count;
    end if;
  end if;
  return new;
end;
$$;

create or replace function bump_reaction_count()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  perform set_config('campmeet.system_update', 'on', true);
  if (tg_op = 'INSERT') then
    update posts set reaction_count = reaction_count + 1 where id = new.post_id;
  elsif (tg_op = 'DELETE') then
    update posts set reaction_count = reaction_count - 1 where id = old.post_id;
  end if;
  return null;
end;
$$;

create or replace function bump_report_count()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  perform set_config('campmeet.system_update', 'on', true);
  if new.target_type = 'post' then
    update posts set report_count = report_count + 1 where id = new.target_id;
  end if;
  return new;
end;
$$;

-- Nothing to change about the triggers themselves (trg_reaction_count,
-- trg_report_count) — CREATE OR REPLACE FUNCTION updates the function
-- body in place, the existing trigger definitions already point at it.

-- ============================================================
-- VERIFY after running:
--   react to a post you didn't author (as a non-staff user), then:
--   select id, reaction_count from posts where id = '<that post id>';
-- reaction_count should now actually increment.
-- ============================================================
