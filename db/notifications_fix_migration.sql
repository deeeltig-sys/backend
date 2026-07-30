-- ============================================================
-- NOTIFICATIONS FIX — safe to run more than once.
--
-- Two things this does:
-- 1. Re-creates the follow/comment/reaction/message notification
--    triggers exactly as defined in v3_social_migration.sql. If they
--    already exist, this is a no-op (CREATE OR REPLACE + DROP/CREATE
--    TRIGGER). If v3_social_migration.sql was only partially applied
--    to this project, this guarantees they actually exist now.
-- 2. Adds notification support for friend requests and acceptances,
--    which never existed anywhere in the codebase — no trigger, and
--    the notification_type enum didn't even have the values for it.
-- ============================================================

-- ---- 1. Re-affirm the original four triggers ----

create or replace function notify_on_follow()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into notifications (user_id, actor_id, type, target_type, target_id)
  values (new.followed_id, new.follower_id, 'follow', 'user', new.follower_id);
  return new;
end;
$$;

drop trigger if exists trg_notify_follow on follows;
create trigger trg_notify_follow after insert on follows
for each row execute function notify_on_follow();

create or replace function notify_on_comment()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_post_author uuid;
begin
  if new.status = 'active' then
    select author_id into v_post_author from posts where id = new.post_id;
    if v_post_author is not null and v_post_author <> new.author_id then
      insert into notifications (user_id, actor_id, type, target_type, target_id)
      values (v_post_author, new.author_id, 'comment', 'post', new.post_id);
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_notify_comment on comments;
create trigger trg_notify_comment after insert on comments
for each row execute function notify_on_comment();

create or replace function notify_on_reaction()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_post_author uuid;
begin
  select author_id into v_post_author from posts where id = new.post_id;
  if v_post_author is not null and v_post_author <> new.user_id then
    insert into notifications (user_id, actor_id, type, target_type, target_id)
    values (v_post_author, new.user_id, 'reaction', 'post', new.post_id);
  end if;
  return new;
end;
$$;

drop trigger if exists trg_notify_reaction on reactions;
create trigger trg_notify_reaction after insert on reactions
for each row execute function notify_on_reaction();

create or replace function bump_conversation_and_notify()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_conv record;
  v_recipient uuid;
begin
  select * into v_conv from conversations where id = new.conversation_id;
  update conversations set last_message_at = new.created_at where id = new.conversation_id;
  v_recipient := case when v_conv.user_a = new.sender_id then v_conv.user_b else v_conv.user_a end;
  insert into notifications (user_id, actor_id, type, target_type, target_id)
  values (v_recipient, new.sender_id, 'message', 'conversation', new.conversation_id);
  return null;
end;
$$;

drop trigger if exists trg_message_notify on messages;
create trigger trg_message_notify
after insert on messages
for each row execute function bump_conversation_and_notify();


-- ---- 2. Friend request / accept notifications (new) ----

-- Postgres 12+: safe to run even if the value already exists, and
-- usable later in this same script (just not in the exact same
-- statement that adds it).
alter type notification_type add value if not exists 'friend_request';
alter type notification_type add value if not exists 'friend_accept';

-- Fires on a fresh request (INSERT) and on a re-sent request after a
-- prior decline (UPDATE ... status back to 'pending' — see
-- send_friend_request()'s ON CONFLICT DO UPDATE in friends_migration.sql).
create or replace function notify_on_friend_request()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if tg_op = 'INSERT' then
    if new.status = 'pending' then
      insert into notifications (user_id, actor_id, type, target_type, target_id)
      values (new.receiver_id, new.sender_id, 'friend_request', 'friend_request', new.id);
    end if;
  elsif tg_op = 'UPDATE' then
    if new.status = 'pending' and old.status is distinct from 'pending' then
      insert into notifications (user_id, actor_id, type, target_type, target_id)
      values (new.receiver_id, new.sender_id, 'friend_request', 'friend_request', new.id);
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_notify_friend_request on friend_requests;
create trigger trg_notify_friend_request
after insert or update on friend_requests
for each row execute function notify_on_friend_request();

-- Fires when the receiver accepts — notifies the original sender.
create or replace function notify_on_friend_accept()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.status = 'accepted' and old.status is distinct from 'accepted' then
    insert into notifications (user_id, actor_id, type, target_type, target_id)
    values (new.sender_id, new.receiver_id, 'friend_accept', 'user', new.receiver_id);
  end if;
  return new;
end;
$$;

drop trigger if exists trg_notify_friend_accept on friend_requests;
create trigger trg_notify_friend_accept
after update on friend_requests
for each row execute function notify_on_friend_accept();
