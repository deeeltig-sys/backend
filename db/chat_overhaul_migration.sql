-- ============================================================
-- CHAT OVERHAUL — read receipts + per-user conversation state
-- (hidden / blocked / deleted / cleared / wallpaper).
--
-- Key design point: a conversation has TWO participants, but "hide",
-- "delete", "clear chat" and "wallpaper" are all things ONE person
-- does to THEIR OWN view of the chat — the other person's copy must
-- stay untouched. So none of this lives on the shared `conversations`
-- or `messages` rows; it's all in a new per-(conversation, user) table.
-- Blocking is the one exception — that already exists as a USER-level
-- table (`blocks`), which is correct: blocking someone blocks them
-- everywhere, not just in one chat thread.
-- ============================================================

-- Read receipts — null until the recipient opens the conversation and
-- GET /messages (or a dedicated /read call) marks it.
alter table messages add column if not exists read_at timestamptz;

-- No UPDATE policy existed on `messages` at all before this — meaning
-- the read-receipt PATCH in list_messages() would have been silently
-- blocked by RLS. This allows a participant to update read_at only on
-- messages in a conversation they're part of, and only when they're
-- NOT the sender (marking your own message "read" makes no sense).
drop policy if exists messages_update_read_receipt on messages;
create policy messages_update_read_receipt on messages
  for update using (
    sender_id <> auth.uid()
    and exists (
      select 1 from conversations c
      where c.id = messages.conversation_id
        and (c.user_a = auth.uid() or c.user_b = auth.uid())
    )
  )
  with check (sender_id <> auth.uid());

create table if not exists conversation_user_state (
  conversation_id   uuid not null references conversations(id) on delete cascade,
  user_id           uuid not null references users(id) on delete cascade,
  hidden_at         timestamptz,
  deleted_at        timestamptz,          -- soft delete; row (and thus recoverability) purged after 60 days
  cleared_before    timestamptz,          -- messages created before this are hidden from view for this user only, permanently
  wallpaper         text check (wallpaper in ('black','white','system','cream','green','custom')),
  custom_wallpaper_url text,
  updated_at        timestamptz not null default now(),
  primary key (conversation_id, user_id)
);

create index if not exists idx_conv_user_state_user on conversation_user_state(user_id);

alter table conversation_user_state enable row level security;

drop policy if exists conv_user_state_own on conversation_user_state;
create policy conv_user_state_own on conversation_user_state
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

-- Upsert helper — every action (hide/delete/clear/wallpaper) is "set
-- this field on my state row, creating it if it doesn't exist yet",
-- so one function covers all of them instead of separate insert-or-
-- update logic scattered across the Flask routes.
create or replace function set_conversation_state(
  p_conversation_id uuid,
  p_hidden_at timestamptz default null,
  p_clear_hidden boolean default false,
  p_deleted_at timestamptz default null,
  p_clear_deleted boolean default false,
  p_cleared_before timestamptz default null,
  p_wallpaper text default null,
  p_custom_wallpaper_url text default null
) returns void language plpgsql security definer as $$
begin
  insert into conversation_user_state (conversation_id, user_id)
  values (p_conversation_id, auth.uid())
  on conflict (conversation_id, user_id) do nothing;

  update conversation_user_state set
    hidden_at = case when p_clear_hidden then null when p_hidden_at is not null then p_hidden_at else hidden_at end,
    deleted_at = case when p_clear_deleted then null when p_deleted_at is not null then p_deleted_at else deleted_at end,
    cleared_before = coalesce(p_cleared_before, cleared_before),
    wallpaper = coalesce(p_wallpaper, wallpaper),
    custom_wallpaper_url = case when p_wallpaper = 'custom' then coalesce(p_custom_wallpaper_url, custom_wallpaper_url) else custom_wallpaper_url end,
    updated_at = now()
  where conversation_id = p_conversation_id and user_id = auth.uid();
end;
$$;

-- Auto-purge for "Recent Deletes" — 60 days after deletion, the state
-- row (and therefore the recovery option) disappears for good. This
-- does NOT touch the actual messages table or the other participant's
-- copy — it only clears this one user's deleted-marker, same as the
-- "photos trash" pattern it's modeled on.
create or replace function purge_expired_deleted_conversations() returns void
language sql as $$
  delete from conversation_user_state
  where deleted_at is not null and deleted_at < now() - interval '60 days';
$$;

-- NOTE: this only defines the function — it does not run on a
-- schedule by itself. If your Supabase project has the pg_cron
-- extension enabled (Database -> Extensions -> pg_cron), schedule it
-- with:
--   select cron.schedule('purge-deleted-chats', '0 3 * * *', 'select purge_expired_deleted_conversations()');
-- Otherwise, this needs to be triggered some other way (an admin
-- endpoint hit manually, or an external scheduled job) — it will not
-- run on its own without one of those.
