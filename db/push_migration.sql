-- ============================================================
-- PUSH NOTIFICATIONS
--
-- IMPORTANT — two placeholders below need editing before you run
-- this: YOUR_BACKEND_URL and YOUR_SHARED_SECRET. Pick any long random
-- string for the secret and set the same value as PUSH_WEBHOOK_SECRET
-- in your backend's environment variables.
--
-- Why this is built as a Postgres trigger calling OUT to Flask,
-- rather than Flask reading push_subscriptions itself: this backend
-- deliberately never uses a service-role key (see the docstring at
-- the top of lib/supabase_client.py) — every table access runs as
-- either the anon key or a signed-in user's own JWT, subject to RLS.
-- But the moment a notification is created there's no "signed-in
-- user" in the request at all — it's an internal DB event. A
-- security-definer trigger (same elevated-but-scoped mechanism every
-- other notification-creating trigger already uses to bypass RLS) is
-- what looks up the recipient's subscriptions and builds the message,
-- then calls a Flask endpoint that only ever receives exactly the one
-- subscription + message it needs to send — it never touches Supabase
-- at all, so the "zero special privileges in Flask" rule holds even
-- for this feature.
-- ============================================================

create table push_subscriptions (
  id         uuid primary key default uuid_generate_v4(),
  user_id    uuid not null references users(id) on delete cascade,
  endpoint   text not null unique,
  p256dh     text not null,
  auth_key   text not null,
  created_at timestamptz not null default now()
);
create index idx_push_subscriptions_user on push_subscriptions(user_id);

alter table push_subscriptions enable row level security;
create policy push_subscriptions_select_own on push_subscriptions for select using (user_id = auth.uid());
create policy push_subscriptions_insert_own on push_subscriptions for insert with check (user_id = auth.uid());
create policy push_subscriptions_delete_own on push_subscriptions for delete using (user_id = auth.uid());

-- pg_net is a Supabase-maintained extension available on every plan,
-- including free tier — this is what lets a trigger make an outbound
-- HTTP call.
create extension if not exists pg_net;

create or replace function send_push_for_notification()
returns trigger
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  v_actor_name text;
  v_title      text := 'CampusMEET';
  v_body       text;
  v_url        text := '/notifications';
  sub          record;
begin
  select full_name into v_actor_name from users where id = new.actor_id;
  v_actor_name := coalesce(v_actor_name, 'Someone');

  v_body := case new.type
    when 'follow'         then v_actor_name || ' followed you'
    when 'reaction'        then v_actor_name || ' reacted to your post'
    when 'comment'         then v_actor_name || ' commented on your post'
    when 'comment_reply'   then v_actor_name || ' replied to your comment'
    when 'mention'         then v_actor_name || ' tagged you in a post'
    when 'friend_request'  then v_actor_name || ' sent you a friend request'
    when 'friend_accept'   then v_actor_name || ' accepted your friend request'
    when 'message'         then v_actor_name || ' sent you a message'
    else 'You have a new notification'
  end;

  if new.target_type = 'post' and new.target_id is not null then
    v_url := '/post/' || new.target_id;
  elsif new.type = 'message' then
    v_url := '/inbox';
  end if;

  -- Fire-and-forget: pg_net queues the HTTP call asynchronously and
  -- doesn't block this trigger waiting for a response. A dead/expired
  -- subscription just silently fails to deliver — there's no cleanup
  -- of stale rows built into this pass (a follow-up worth doing once
  -- this is live, not a correctness issue today).
  for sub in select endpoint, p256dh, auth_key from push_subscriptions where user_id = new.recipient_id loop
    perform net.http_post(
      url := 'https://campus-backend-tz9q.onrender.com/api/push/send',
      headers := jsonb_build_object(
        'Content-Type', 'application/json',
        'X-Webhook-Secret', 'pnPTdCxlYFD1rrKHmYhyQICon43AYsKvR1T1CtaMfNo'
      ),
      body := jsonb_build_object(
        'endpoint', sub.endpoint, 'p256dh', sub.p256dh, 'auth', sub.auth_key,
        'title', v_title, 'body', v_body, 'url', v_url
      )
    );
  end loop;

  return new;
end;
$$;

create trigger trg_send_push_for_notification
after insert on notifications
for each row execute function send_push_for_notification();

-- ============================================================
-- End push_migration
-- ============================================================
