-- ============================================================
-- FIX — restores the campmeet.system_update exemption that
-- guard_post_moderation_fields lost when it was rewritten for
-- comment_count support. This is what silently blocked
-- reaction_count (and comment_count) from incrementing whenever
-- someone reacted to / commented on a post they didn't author.
-- ============================================================

create or replace function guard_post_moderation_fields()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  -- Set by bump_reaction_count / bump_comment_count right before their
  -- own automated updates, so this guard doesn't revert them. Only
  -- user-initiated PATCH requests should ever hit the checks below.
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

-- Same fix for comment_count — set the exemption flag before updating,
-- matching bump_reaction_count's existing pattern exactly.
create or replace function bump_comment_count()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  perform set_config('campmeet.system_update', 'on', true);
  if tg_op = 'INSERT' then
    if new.status = 'active' then
      update posts set comment_count = comment_count + 1 where id = new.post_id;
    end if;
  elsif tg_op = 'UPDATE' then
    if old.status = 'active' and new.status <> 'active' then
      update posts set comment_count = greatest(comment_count - 1, 0) where id = new.post_id;
    elsif old.status <> 'active' and new.status = 'active' then
      update posts set comment_count = comment_count + 1 where id = new.post_id;
    end if;
  elsif tg_op = 'DELETE' then
    if old.status = 'active' then
      update posts set comment_count = greatest(comment_count - 1, 0) where id = old.post_id;
    end if;
  end if;
  return null;
end;
$$;
