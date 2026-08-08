-- ============================================================
-- MENTIONS NOTIFICATION FIX — notify_on_mention() inserted into a
-- column called recipient_id, but the actual notifications table
-- (v3_social_migration.sql) has never had that column; the real
-- column is user_id, exactly like every other notification trigger
-- in this codebase already uses correctly.
--
-- This isn't a hypothetical — reproduced against a real Postgres
-- instance: tagging someone in a post throws
--   ERROR: column "recipient_id" of relation "notifications" does
--   not exist
-- every single time, because PL/pgSQL function bodies aren't checked
-- against table schemas at CREATE FUNCTION time, only when they
-- actually run — so this shipped and sat looking completely fine
-- until the exact moment someone tried to use it.
-- ============================================================

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
  if v_author_id is not null and v_author_id <> new.mentioned_user_id then
    insert into notifications (user_id, actor_id, type, target_type, target_id)
    values (new.mentioned_user_id, v_author_id, 'mention', 'post', new.post_id);
  end if;
  return new;
end;
$$;

-- Trigger itself doesn't need recreating (same name, same function
-- name, CREATE OR REPLACE FUNCTION above already updates what it
-- points to) — included anyway for a clean, self-contained re-run.
drop trigger if exists trg_notify_on_mention on post_mentions;
create trigger trg_notify_on_mention
after insert on post_mentions
for each row execute function notify_on_mention();

-- ============================================================
-- Verify it worked — tag someone in a post, then:
-- select user_id, actor_id, type, target_type, target_id
--   from notifications where type = 'mention' order by created_at desc limit 1;
-- Should return a row with no error at insert time.
-- ============================================================
