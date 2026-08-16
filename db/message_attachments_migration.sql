-- ============================================================
-- CAMPMEET — chat image/document/audio-file attachments migration
-- Extends messages.type ('text','voice','sticker') with 'image' and
-- 'file' — 'file' covers documents AND audio files sent as an
-- attachment (as opposed to 'voice', which stays the hold-to-record
-- note with its own waveform/duration columns). Storage follows the
-- exact private-bucket-plus-signed-URL shape voice_notes_storage_
-- migration.sql already established: a new `message-attachments`
-- bucket, private, path "{conversation_id}/{uuid}.{ext}", RLS scoped
-- to conversation participants via storage.foldername(name)[1].
-- ============================================================

alter table messages drop constraint if exists messages_type_check;
alter table messages add constraint messages_type_check
  check (type in ('text', 'voice', 'sticker', 'image', 'file'));

alter table messages add column if not exists attachment_path text;
alter table messages add column if not exists attachment_name text;
alter table messages add column if not exists attachment_mime text;
alter table messages add column if not exists attachment_size int;

insert into storage.buckets (id, name, public)
values ('message-attachments', 'message-attachments', false)
on conflict (id) do nothing;

drop policy if exists "conversation participants read attachments" on storage.objects;
create policy "conversation participants read attachments"
  on storage.objects for select
  using (
    bucket_id = 'message-attachments'
    and exists (
      select 1 from conversations c
      where c.id::text = (storage.foldername(name))[1]
        and auth.uid() in (c.user_a, c.user_b)
    )
  );

drop policy if exists "conversation participants upload attachments" on storage.objects;
create policy "conversation participants upload attachments"
  on storage.objects for insert
  with check (
    bucket_id = 'message-attachments'
    and auth.role() = 'authenticated'
    and exists (
      select 1 from conversations c
      where c.id::text = (storage.foldername(name))[1]
        and auth.uid() in (c.user_a, c.user_b)
    )
  );

drop policy if exists "conversation participants delete attachments" on storage.objects;
create policy "conversation participants delete attachments"
  on storage.objects for delete
  using (
    bucket_id = 'message-attachments'
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
-- End message attachments migration
-- ============================================================
