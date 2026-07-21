-- ============================================================
-- V2 MIGRATION — comment_count denormalization + feed view update
-- Run this once in the Supabase SQL editor before deploying the
-- comments backend/frontend changes.
-- ============================================================

alter table posts add column if not exists comment_count int not null default 0;

-- Guard comment_count the same way reaction_count/report_count are
-- already guarded — a student can't set it directly via PATCH; only
-- the trigger below (SECURITY DEFINER) or staff can move it.
create or replace function guard_post_moderation_fields()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
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

-- NOTE: marked SECURITY DEFINER on purpose, unlike bump_reaction_count.
-- Without it, this trigger runs as whichever student fired the INSERT
-- on comments — and posts_update_own only allows a post's own author
-- to update it, so a comment on someone else's post would silently
-- fail to increment their comment_count (RLS filters the UPDATE down
-- to zero rows, no error, just a stuck counter). This is very likely
-- the same root cause behind the reaction_count bug flagged earlier —
-- bump_reaction_count has the identical gap. Worth fixing that one the
-- same way when you're ready; separate change, not touched here.
create or replace function bump_comment_count()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
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

drop trigger if exists trg_comment_count on comments;
create trigger trg_comment_count
after insert or update of status or delete on comments
for each row execute function bump_comment_count();

-- Feed view now also surfaces comment_count, same as reaction_count.
-- NOTE: comment_count is appended at the very END of the column list on
-- purpose — CREATE OR REPLACE VIEW only allows adding new columns after
-- the last existing one; inserting it in the middle (e.g. next to
-- reaction_count) makes Postgres think existing columns were renamed
-- and it refuses with a 42P16 error. Dropping and recreating sidesteps
-- the append-only rule entirely, which is why this uses DROP + CREATE
-- rather than CREATE OR REPLACE.
drop view if exists feed;
create view feed as
select p.id,
    p.university_id,
    p.author_id,
    p.content,
    p.image_url,
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
  order by (feed_score(p.view_count, p.reaction_count, p.search_hit_count, p.created_at)) desc, p.created_at desc;
