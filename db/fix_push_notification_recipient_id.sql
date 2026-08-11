-- ============================================================
-- FIX — send_push_for_notification() references new.recipient_id,
-- but `notifications` has no such column (the real column is
-- user_id — same bug family as mentions_notification_column_fix.sql,
-- different function). This trigger fires on EVERY insert into
-- notifications, for every notification type, not just reactions —
-- follows, comments, mentions, friend requests, all of it. Whichever
-- action triggers a notification insert has been failing outright
-- since this shipped, because the error happens inside the same
-- transaction as the original insert and rolls the whole thing back.
--
-- This is what's been silently killing reactions: notify_on_reaction()
-- correctly inserts into notifications -> that insert fires this
-- trigger -> this trigger errors on new.recipient_id -> the entire
-- transaction rolls back, including the reaction itself. Every layer
-- checked earlier today (bump_reaction_count, guard_post_moderation_
-- fields, notify_on_reaction, bump_standing_count) was correct in
-- isolation; this is the one actually breaking the chain.
-- ============================================================

create or replace function send_push_for_notification()
returns trigger
language plpgsql
security definer
set search_path = public
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

  -- The actual fix: user_id, not recipient_id — everything else in
  -- this function is untouched, byte-for-byte identical to what's
  -- already live.
  for sub in select endpoint, p256dh, auth_key from push_subscriptions where user_id = new.user_id loop
    perform net.http_post(
      url := 'https://campus-backend-tz9q.onrender.com/api/push/send',
      headers := jsonb_build_object(
        'Content-Type', 'application/json',
        'X-Webhook-Secret', 'Fgd8TBTTwAm7CtVIGmxHt5PPpAxpSNWx46d4uzd1olI'
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

-- ============================================================
-- VERIFY after running: re-run the same Console fetch() snippet
-- from earlier (or just tap a reaction in the app) — it should
-- return 200 / actually persist this time. This one change likely
-- also fixes notifications not showing up for follows, comments,
-- friend requests, etc., since they all go through this same trigger.
-- ============================================================
