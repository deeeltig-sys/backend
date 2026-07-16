-- ============================================================
-- CAMPMEET — Avatar storage migration
-- Creates the avatars bucket, same folder-per-user RLS pattern as
-- post-images. Run this in the Supabase SQL editor.
-- ============================================================

insert into storage.buckets (id, name, public)
values ('avatars', 'avatars', true)
on conflict (id) do nothing;

drop policy if exists "avatars are publicly readable" on storage.objects;
create policy "avatars are publicly readable"
  on storage.objects for select
  using (bucket_id = 'avatars');

drop policy if exists "students upload only their own avatar" on storage.objects;
create policy "students upload only their own avatar"
  on storage.objects for insert
  with check (
    bucket_id = 'avatars'
    and auth.role() = 'authenticated'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

-- Avatars get replaced, not just added — a student re-uploading a new
-- photo needs to be able to overwrite/remove their previous one.
drop policy if exists "students manage only their own avatar" on storage.objects;
create policy "students manage only their own avatar"
  on storage.objects for update
  using (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists "students delete only their own avatar" on storage.objects;
create policy "students delete only their own avatar"
  on storage.objects for delete
  using (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

-- ============================================================
-- End avatar storage migration
-- ============================================================
