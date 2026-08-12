-- ============================================================
-- Voice note lifecycle — the SCALE version.
--
-- Two independent layers, both already safe to run together:
--   1. Lazy per-open expiry (routes/messages.py, already shipped) —
--      free, instant, catches active conversations. No change here.
--   2. THIS migration: a bounded, batched pg_cron sweep that
--      guarantees cleanup EVEN for conversations nobody ever reopens
--      — the gap layer 1 can't close on its own. Runs every 15
--      minutes, only ever touches a fixed-size batch per run, so it
--      costs the same whether the backlog is 50 rows or 50 million.
--
-- The privileged credential this needs (cross-user Storage delete)
-- lives ONLY in Supabase Vault — never in this file, never in your
-- Flask app's env vars, never in your repo. See
-- VOICE_NOTE_CLEANUP_SETUP.md for the one manual step that goes
-- with this file (inserting the actual key — deliberately NOT
-- scripted here, a secret has no business sitting in a migration).
-- ============================================================

-- Partial index — only indexes voice messages that still hold a
-- file, which stays tiny relative to your whole messages table no
-- matter how large that table grows. Makes both the lazy per-open
-- check AND the global sweep below cheap at any scale.
create index if not exists idx_messages_voice_expiry on messages (created_at)
  where type = 'voice' and voice_path is not null;

-- One row per cron run — not per deleted file. At 100M users this
-- table stays small forever (one row every 15 minutes), while still
-- answering the actual operational question that matters: "is this
-- still running, and is it keeping up with the backlog?"
create table if not exists voice_note_cleanup_log (
  id           bigint generated always as identity primary key,
  run_at       timestamptz not null default now(),
  queued_count int not null,
  error        text
);

-- ------------------------------------------------------------
-- The cleanup function itself.
--
-- SECURITY DEFINER so it can read the Vault secret and reach
-- storage.objects' effective delete rights regardless of who (or
-- what — pg_cron has no "user") is running it. This is the ONE
-- place in the entire platform that holds elevated Storage rights —
-- everything else, including the lazy per-open path, still runs on
-- each user's own token.
--
-- Deliberately bounded (BATCH_SIZE) and fire-and-forget on the HTTP
-- side, matching the exact pattern push_migration.sql already uses
-- elsewhere in this codebase (queue via pg_net, don't block waiting
-- for the response) — consistent with infra you've already tested
-- and trust, not a new pattern.
-- ------------------------------------------------------------
create or replace function cleanup_expired_voice_notes()
returns void
language plpgsql
security definer
set search_path = public, extensions, vault
as $$
declare
  -- REPLACE before running: your Supabase project's URL, exactly as
  -- used everywhere else in this codebase's SUPABASE_URL env var.
  -- Not a secret — the project ref is public, only the key below is
  -- sensitive — so it's fine as a literal here.
  project_url constant text := 'https://hoeslmqgmefrcqqpbgnc.supabase.co';

  -- Keep this well under pg_net's shared 200 req/s budget (see
  -- Supabase's pg_net docs) — this queue is shared with the existing
  -- push-notification traffic from push_migration.sql, so cleanup
  -- should never be able to crowd out a real-time push. 1,500 queued
  -- every 15 minutes is ~1.7/s sustained, nowhere close to that
  -- ceiling, with headroom to raise it later if the backlog ever
  -- outpaces this rate.
  batch_size constant int := 1500;

  service_key text;
  voice_row record;
  queued int := 0;
begin
  select decrypted_secret into service_key
  from vault.decrypted_secrets
  where name = 'voice_notes_service_role_key'
  limit 1;

  if service_key is null then
    insert into voice_note_cleanup_log (queued_count, error)
    values (0, 'voice_notes_service_role_key not found in Vault — see VOICE_NOTE_CLEANUP_SETUP.md');
    return;
  end if;

  for voice_row in
    select id, voice_path
    from messages
    where type = 'voice'
      and voice_path is not null
      and created_at < now() - interval '5 days'
    order by created_at asc
    limit batch_size
  loop
    -- Async — queues instantly, actual HTTP delete happens in
    -- pg_net's background worker. No body needed: unlike the bulk-
    -- delete endpoint, the single-object delete takes the path in
    -- the URL itself, which is exactly what net.http_delete supports
    -- (it has no body parameter — confirmed against pg_net's actual
    -- function signature, not assumed).
    perform net.http_delete(
      url := project_url || '/storage/v1/object/voice-notes/' || voice_row.voice_path,
      headers := jsonb_build_object(
        'Authorization', 'Bearer ' || service_key,
        'apikey', service_key
      )
    );

    -- Cleared optimistically alongside queuing the delete, same
    -- fire-and-forget contract push_migration.sql already relies on
    -- elsewhere in this codebase. In the rare case the queued HTTP
    -- call itself fails, the file is orphaned in storage but the
    -- message row already reads as expired — acceptable for a
    -- cost-control mechanism, not a place correctness-critical data
    -- lives.
    update messages
    set voice_path = null, voice_duration_ms = null, voice_waveform = null,
        content = '🎤 Voice message (expired)'
    where id = voice_row.id;

    queued := queued + 1;
  end loop;

  insert into voice_note_cleanup_log (queued_count) values (queued);
exception when others then
  insert into voice_note_cleanup_log (queued_count, error) values (queued, SQLERRM);
end;
$$;

-- Runs every 15 minutes, forever, regardless of whether Render/Flask
-- is even up — this entire mechanism lives inside Supabase itself.
select cron.schedule('cleanup-expired-voice-notes', '*/15 * * * *', 'select cleanup_expired_voice_notes()');

-- ============================================================
-- After running this file (and completing the Vault step in
-- VOICE_NOTE_CLEANUP_SETUP.md), verify it actually works before
-- trusting it unattended:
--
--   select cleanup_expired_voice_notes();
--   select * from voice_note_cleanup_log order by run_at desc limit 5;
--   select jobname, schedule, active from cron.job where jobname = 'cleanup-expired-voice-notes';
-- ============================================================
