-- ============================================================
-- Run this once against the live DB. Supersedes
-- fix_push_notification_recipient_id.sql — same root fix
-- (new.recipient_id -> new.user_id, the column that doesn't exist
-- was rolling back every notification-triggering action, reactions
-- included), plus FB-style grouping on top:
--
--   - tag: repeat notifications from the same source (three reactions
--     on one post, three comments on one thread) collapse into a
--     single slot and update in place instead of stacking separately.
--   - requireInteraction: friend requests and messages stay on screen
--     until the person actually taps them, instead of auto-dismissing
--     after a few seconds like a reaction ping does.
--
-- create or replace is safe to run more than once.
-- ============================================================

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
  v_tag        text;
  v_require_interaction boolean;
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

  v_tag := new.type || ':' || coalesce(new.target_id::text, new.actor_id::text);
  v_require_interaction := new.type in ('friend_request', 'message');

  -- The actual root fix: user_id, not recipient_id — that column
  -- never existed on notifications, which is why this trigger has
  -- been erroring and rolling back the parent insert (the reaction,
  -- the comment, the follow, whatever triggered it) every time.
  for sub in select endpoint, p256dh, auth_key from push_subscriptions where user_id = new.user_id loop
    perform net.http_post(
      url := 'https://campus-backend-tz9q.onrender.com/api/push/send',
      headers := jsonb_build_object(
        'Content-Type', 'application/json',
        'X-Webhook-Secret', 'Fgd8TBTTwAm7CtVIGmxHt5PPpAxpSNWx46d4uzd1olI'
      ),
      body := jsonb_build_object(
        'endpoint', sub.endpoint, 'p256dh', sub.p256dh, 'auth', sub.auth_key,
        'title', v_title, 'body', v_body, 'url', v_url,
        'tag', v_tag, 'requireInteraction', v_require_interaction
      )
    );
  end loop;

  return new;
end;
$$;

-- ============================================================
-- VERIFY after running: react to a post from a second account, or
-- send a friend request. It should now both persist (no more gray
-- screen / post not found) and actually arrive as a push.
-- ============================================================
