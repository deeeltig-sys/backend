-- ============================================================
-- CHAT HYBRID V2 — delivered receipts, voice notes, message
-- reactions, first-time stickers, and last-seen presence.
--
-- Builds on chat_overhaul_migration.sql (read_at) and
-- typing_indicator_migration.sql (typing_at). Nothing here
-- touches those columns/policies/functions.
--
-- Receipt model this adds:
--   sent      -> the messages row exists (always true once insert
--                succeeds, no column needed)
--   delivered -> delivered_at is set the moment the recipient's
--                client actually receives the row (realtime INSERT
--                event, or the poll fallback / opening the thread —
--                whichever happens first)
--   read      -> read_at is set when the recipient opens the thread
--                (unchanged from chat_overhaul_migration.sql)
-- ============================================================

alter table messages add column if not exists delivered_at timestamptz;

-- Message type. Existing rows are all plain text, hence the default —
-- this is a widening change, every current row/query that only knows
-- about `content` keeps working unmodified.
alter table messages add column if not exists type text not null default 'text'
  check (type in ('text', 'voice', 'sticker'));

-- Voice notes store a STORAGE PATH, not a public URL — the
-- `voice-notes` bucket is private (see voice_notes_storage_migration.sql),
-- so playback goes through a short-lived signed URL generated
-- on-demand server-side, never a bare public link.
alter table messages add column if not exists voice_path text;
alter table messages add column if not exists voice_duration_ms int;
alter table messages add column if not exists voice_waveform jsonb;

-- Sticker messages reference a client-known preset id (e.g. 'wave',
-- 'high-five') — the actual artwork ships with the frontend bundle,
-- same as how emoji don't get stored as images either.
alter table messages add column if not exists sticker_id text;

-- No RLS changes needed for any of the above: messages_insert_own
-- (v3_social_migration.sql) only inspects sender_id/conversation
-- membership, not which columns are set, so a voice/sticker insert
-- is already covered by the existing policy.

-- ------------------------------------------------------------
-- Message reactions — IG-style, one live reaction per user per
-- message, reusing the SAME reaction vocabulary as post reactions
-- (like/fire/cosign/yawa — see models/reaction.py) rather than
-- inventing a second emoji set for chat.
-- ------------------------------------------------------------
create table if not exists message_reactions (
  message_id  uuid not null references messages(id) on delete cascade,
  user_id     uuid not null references users(id) on delete cascade,
  emoji       text not null check (emoji in ('like', 'fire', 'cosign', 'yawa')),
  created_at  timestamptz not null default now(),
  primary key (message_id, user_id)
);

create index if not exists idx_message_reactions_message on message_reactions(message_id);

alter table message_reactions enable row level security;

-- Visible to anyone who's a participant in the conversation the
-- reacted-to message belongs to (mirrors messages_select_own exactly,
-- one hop further through messages -> conversations).
drop policy if exists message_reactions_select_participant on message_reactions;
create policy message_reactions_select_participant on message_reactions
  for select using (
    exists (
      select 1 from messages m
      join conversations c on c.id = m.conversation_id
      where m.id = message_reactions.message_id
        and auth.uid() in (c.user_a, c.user_b)
    )
  );

-- A participant may set/change their OWN reaction on any message in a
-- conversation they're part of (including their own messages — same
-- as IG letting you react to your own post).
drop policy if exists message_reactions_insert_own on message_reactions;
create policy message_reactions_insert_own on message_reactions
  for insert with check (
    user_id = auth.uid()
    and exists (
      select 1 from messages m
      join conversations c on c.id = m.conversation_id
      where m.id = message_reactions.message_id
        and auth.uid() in (c.user_a, c.user_b)
    )
  );

drop policy if exists message_reactions_update_own on message_reactions;
create policy message_reactions_update_own on message_reactions
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists message_reactions_delete_own on message_reactions;
create policy message_reactions_delete_own on message_reactions
  for delete using (user_id = auth.uid());

-- ------------------------------------------------------------
-- Last-seen presence — live "online now" already exists entirely
-- client-side via Supabase Presence (hooks/usePresence.js), no DB
-- involved. This column is only for the OFFLINE case: "Active 5m
-- ago" the way WhatsApp/Facebook show it once someone's live
-- presence channel disconnects. Updated by a lightweight heartbeat
-- (POST /api/profile/heartbeat) while the app is foregrounded, not
-- on every request — see routes/profile.py.
-- ------------------------------------------------------------
alter table users add column if not exists last_seen_at timestamptz;

-- No new RLS policy needed: users_update_own (rls_policies.sql)
-- already lets a user UPDATE any column on their own row, which is
-- exactly what the heartbeat endpoint does.
