-- ============================================================
-- CAMPMEET — voice notes storage migration
-- Creates the `voice-notes` bucket, PRIVATE (unlike post-images/
-- avatars, which are public) — a voice note is a DM, not a public
-- post, so it must never be reachable by a bare URL. Playback goes
-- through a short-lived signed URL generated per-request by
-- routes/messages.py, using the caller's own JWT so storage RLS
-- below is what actually enforces "only conversation participants
-- can hear this", not the signing call itself.
--
-- Path convention: "{conversation_id}/{uuid}.{ext}" — folder is the
-- CONVERSATION, not the uploader, because both participants (not
-- just the sender) need read access to play it back.
-- ============================================================

insert into storage.buckets (id, name, public)
values ('voice-notes', 'voice-notes', false)
on conflict (id) do nothing;

drop policy if exists "conversation participants read voice notes" on storage.objects;
create policy "conversation participants read voice notes"
  on storage.objects for select
  using (
    bucket_id = 'voice-notes'
    and exists (
      select 1 from conversations c
      where c.id::text = (storage.foldername(name))[1]
        and auth.uid() in (c.user_a, c.user_b)
    )
  );

-- Upload is gated the same way — must be a participant of the
-- conversation named in the path. The actual "only the sender should
-- be creating this specific message" rule is enforced separately by
-- messages_insert_own when the message row itself is created.
drop policy if exists "conversation participants upload voice notes" on storage.objects;
create policy "conversation participants upload voice notes"
  on storage.objects for insert
  with check (
    bucket_id = 'voice-notes'
    and auth.role() = 'authenticated'
    and exists (
      select 1 from conversations c
      where c.id::text = (storage.foldername(name))[1]
        and auth.uid() in (c.user_a, c.user_b)
    )
  );

-- A participant may delete a voice note from their conversation (e.g.
-- future "delete for everyone") — staff can remove anything, for
-- moderation/reports, same pattern as storage_policies.sql.
drop policy if exists "conversation participants delete voice notes" on storage.objects;
create policy "conversation participants delete voice notes"
  on storage.objects for delete
  using (
    bucket_id = 'voice-notes'
    and (
      exists (
        select 1 from conversations c
        where c.id::text = (storage.foldername(name))[1]
          and auth.uid() in (c.user_a, c.user_b)
      )
      or exists (select 1 from public.users where id = auth.uid() and role = 'admin')
    )
  );

-- ============================================================
-- End voice notes storage migration
-- ============================================================
