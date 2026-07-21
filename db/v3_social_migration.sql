-- ============================================================
-- V3 MIGRATION — follows, notifications, direct messages
-- Safe to run more than once (uses if not exists / or replace
-- throughout).
-- ============================================================

-- ============ FOLLOWS ============
create table if not exists follows (
  follower_id  uuid not null references users(id) on delete cascade,
  followed_id  uuid not null references users(id) on delete cascade,
  created_at   timestamptz not null default now(),
  primary key (follower_id, followed_id),
  check (follower_id <> followed_id)
);

alter table users add column if not exists follower_count int not null default 0;
alter table users add column if not exists following_count int not null default 0;

alter table follows enable row level security;

drop policy if exists follows_select_all on follows;
create policy follows_select_all on follows for select using (true);

drop policy if exists follows_insert_own on follows;
create policy follows_insert_own on follows for insert with check (follower_id = auth.uid());

drop policy if exists follows_delete_own on follows;
create policy follows_delete_own on follows for delete using (follower_id = auth.uid());

-- No guard/moderation trigger exists on `users` today, so a plain
-- SECURITY DEFINER function is enough to bypass RLS here — no need
-- for the campmeet.system_update flag the posts triggers use.
create or replace function bump_follow_counts()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if tg_op = 'INSERT' then
    update users set following_count = following_count + 1 where id = new.follower_id;
    update users set follower_count = follower_count + 1 where id = new.followed_id;
  elsif tg_op = 'DELETE' then
    update users set following_count = greatest(following_count - 1, 0) where id = old.follower_id;
    update users set follower_count = greatest(follower_count - 1, 0) where id = old.followed_id;
  end if;
  return null;
end;
$$;

drop trigger if exists trg_follow_counts on follows;
create trigger trg_follow_counts
after insert or delete on follows
for each row execute function bump_follow_counts();

-- ============ NOTIFICATIONS ============
do $$
begin
  if not exists (select 1 from pg_type where typname = 'notification_type') then
    create type notification_type as enum ('follow', 'comment', 'reaction', 'message');
  end if;
end $$;

create table if not exists notifications (
  id           uuid primary key default uuid_generate_v4(),
  user_id      uuid not null references users(id) on delete cascade,
  actor_id     uuid references users(id) on delete set null,
  type         notification_type not null,
  target_type  text,
  target_id    uuid,
  read         boolean not null default false,
  created_at   timestamptz not null default now()
);

create index if not exists idx_notifications_user on notifications(user_id, created_at desc);

alter table notifications enable row level security;

-- Deliberately NO insert/delete policy for regular users — the only
-- way a notification row gets created is through the SECURITY
-- DEFINER trigger functions below, so nobody can forge a
-- notification appearing to be from someone else.
drop policy if exists notifications_select_own on notifications;
create policy notifications_select_own on notifications for select using (user_id = auth.uid());

drop policy if exists notifications_update_own on notifications;
create policy notifications_update_own on notifications for update
  using (user_id = auth.uid()) with check (user_id = auth.uid());

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

-- ============ DIRECT MESSAGES ============
-- One row per pair, canonical ordering (user_a is always the
-- lexicographically-smaller uuid) enforced by start_conversation()
-- below rather than by raw client inserts, so a conversation between
-- two people can never accidentally end up as two separate rows.
create table if not exists conversations (
  id               uuid primary key default uuid_generate_v4(),
  user_a           uuid not null references users(id) on delete cascade,
  user_b           uuid not null references users(id) on delete cascade,
  status           text not null default 'pending' check (status in ('pending', 'accepted')),
  initiated_by     uuid not null references users(id) on delete cascade,
  last_message_at  timestamptz not null default now(),
  created_at       timestamptz not null default now(),
  check (user_a <> user_b),
  check (user_a < user_b),
  unique (user_a, user_b)
);

create table if not exists messages (
  id               uuid primary key default uuid_generate_v4(),
  conversation_id  uuid not null references conversations(id) on delete cascade,
  sender_id        uuid not null references users(id) on delete cascade,
  content          text not null check (char_length(content) between 1 and 2000),
  created_at       timestamptz not null default now()
);

create index if not exists idx_messages_conversation on messages(conversation_id, created_at);
create index if not exists idx_conversations_participants on conversations(user_a, user_b);

alter table conversations enable row level security;
alter table messages enable row level security;

drop policy if exists conversations_select_own on conversations;
create policy conversations_select_own on conversations for select
  using (auth.uid() in (user_a, user_b));

drop policy if exists messages_select_own on messages;
create policy messages_select_own on messages for select
  using (
    exists (
      select 1 from conversations c
      where c.id = messages.conversation_id
        and auth.uid() in (c.user_a, c.user_b)
    )
  );

-- This is the actual message-request gate, enforced at the database
-- level rather than trusted to application code: a conversation's
-- recipient cannot insert a reply until the conversation is
-- 'accepted'. The person who started it can keep sending into their
-- own pending request (it just sits unseen/unreplied until accepted).
drop policy if exists messages_insert_own on messages;
create policy messages_insert_own on messages for insert
  with check (
    sender_id = auth.uid()
    and exists (
      select 1 from conversations c
      where c.id = messages.conversation_id
        and auth.uid() in (c.user_a, c.user_b)
        and (
          c.status = 'accepted'
          or (c.status = 'pending' and c.initiated_by = auth.uid())
        )
    )
  );

-- Starts (or resumes) a conversation with another user. SECURITY
-- DEFINER so it can canonicalize user_a/user_b ordering and dedupe
-- reliably regardless of who calls it first; also the natural place
-- to check blocks in either direction before anything gets created.
create or replace function start_conversation(p_other_user_id uuid)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_me uuid := auth.uid();
  v_a uuid;
  v_b uuid;
  v_id uuid;
begin
  if v_me is null then
    raise exception 'must be signed in';
  end if;
  if p_other_user_id = v_me then
    raise exception 'cannot message yourself';
  end if;

  if exists (
    select 1 from blocks
    where (blocker_id = v_me and blocked_id = p_other_user_id)
       or (blocker_id = p_other_user_id and blocked_id = v_me)
  ) then
    raise exception 'cannot start a conversation with this user';
  end if;

  if v_me < p_other_user_id then
    v_a := v_me; v_b := p_other_user_id;
  else
    v_a := p_other_user_id; v_b := v_me;
  end if;

  select id into v_id from conversations where user_a = v_a and user_b = v_b;
  if v_id is not null then
    return v_id;
  end if;

  insert into conversations (user_a, user_b, status, initiated_by)
  values (v_a, v_b, 'pending', v_me)
  returning id into v_id;

  return v_id;
end;
$$;

-- Only the non-initiator can accept, and only while still pending —
-- prevents the sender from "accepting" their own request to skip
-- the gate.
create or replace function accept_conversation(p_conversation_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_me uuid := auth.uid();
begin
  update conversations
  set status = 'accepted'
  where id = p_conversation_id
    and v_me in (user_a, user_b)
    and initiated_by <> v_me
    and status = 'pending';
end;
$$;

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
