-- ============================================================
-- MENTIONS — there's no @username handle system in this schema
-- (people are identified by full_name), so mentions work the way the
-- composer's autocomplete already has to work anyway: picking an
-- exact person from a dropdown, not parsing free-typed text for a
-- pattern. The frontend sends explicit user ids alongside the post;
-- this table just records who was tagged.
-- ============================================================

create table post_mentions (
  post_id           uuid not null references posts(id) on delete cascade,
  mentioned_user_id uuid not null references users(id) on delete cascade,
  created_at        timestamptz not null default now(),
  primary key (post_id, mentioned_user_id)
);
create index idx_post_mentions_user on post_mentions(mentioned_user_id);

-- Same notification_type enum every other notification trigger in
-- this schema extends (see notifications_fix_migration.sql,
-- notifications_threaded_migration.sql) — adding here rather than
-- inventing a parallel mechanism.
alter type notification_type add value if not exists 'mention';

create or replace function notify_on_mention()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_author_id uuid;
begin
  select author_id into v_author_id from posts where id = new.post_id;
  -- Tagging yourself (or a post somehow missing its author) doesn't
  -- notify — same "don't notify someone about their own action" rule
  -- every other trigger here already follows.
  if v_author_id is not null and v_author_id <> new.mentioned_user_id then
    insert into notifications (recipient_id, actor_id, type, target_type, target_id)
    values (new.mentioned_user_id, v_author_id, 'mention', 'post', new.post_id);
  end if;
  return new;
end;
$$;

create trigger trg_notify_on_mention
after insert on post_mentions
for each row execute function notify_on_mention();

-- ============================================================
-- RLS — mentions are public (anyone can see who's tagged in a post
-- they can already see); only the post's own author can tag someone
-- in it, checked the same way poll_options' insert policy checks
-- post ownership.
-- ============================================================
alter table post_mentions enable row level security;

create policy post_mentions_select_all on post_mentions for select using (true);

create policy post_mentions_insert_own_post on post_mentions
  for insert with check (
    exists (select 1 from posts p where p.id = post_id and p.author_id = auth.uid())
  );

-- ============================================================
-- End mentions_migration
-- ============================================================
