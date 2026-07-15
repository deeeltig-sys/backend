-- ============================================================
-- CAMPMEET — Storage migration
-- Creates the post-images bucket and locks it down so a student
-- can only upload into their own folder (path prefix = their own
-- auth uid), while anyone can read — public bucket, public feed.
-- Run this in the Supabase SQL editor.
-- ============================================================

insert into storage.buckets (id, name, public)
values ('post-images', 'post-images', true)
on conflict (id) do nothing;

-- Public read — post images are visible to anyone on the feed,
-- verified or not, signed in or not (matches /api/posts/feed
-- being a public, unauthenticated route).
drop policy if exists "post images are publicly readable" on storage.objects;
create policy "post images are publicly readable"
  on storage.objects for select
  using (bucket_id = 'post-images');

-- A signed-in student may only upload into a path that starts with
-- their own user id (backend writes to "{user_id}/{uuid}.{ext}" —
-- see routes/posts.py:upload_image). storage.foldername(name)[1] is
-- the first path segment, i.e. the folder the object lives in.
drop policy if exists "students upload only into their own folder" on storage.objects;
create policy "students upload only into their own folder"
  on storage.objects for insert
  with check (
    bucket_id = 'post-images'
    and auth.role() = 'authenticated'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

-- A student may remove their own uploaded images (e.g. if a post is
-- deleted) — staff can remove anything, for moderation cleanup.
drop policy if exists "students delete only their own images" on storage.objects;
create policy "students delete only their own images"
  on storage.objects for delete
  using (
    bucket_id = 'post-images'
    and (
      (storage.foldername(name))[1] = auth.uid()::text
      or exists (select 1 from public.users where id = auth.uid() and role = 'admin')
    )
  );

-- ============================================================
-- End storage migration
-- ============================================================
