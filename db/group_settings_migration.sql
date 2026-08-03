-- ============================================================
-- GROUP SETTINGS — group avatars + the ability to actually delete a
-- group. groups_migration.sql covered select/insert/update but never
-- added a delete policy, so a DELETE on groups has been silently
-- denied by RLS this whole time (not a bug surfaced yet, just a gap
-- being closed now that Settings needs it).
-- ============================================================

create policy groups_delete_creator on groups
  for delete using (creator_id = auth.uid());

-- ============================================================
-- Group avatar storage — same folder-per-owner RLS shape as
-- avatar_storage_policies.sql, except the "owner" here is checked
-- against group_members.role = 'admin' rather than auth.uid()
-- matching the folder directly, since any admin (not just whoever
-- created the group) should be able to update the group photo.
-- Path convention: group-avatars/{group_id}/avatar.{ext}
-- ============================================================

insert into storage.buckets (id, name, public)
values ('group-avatars', 'group-avatars', true)
on conflict (id) do nothing;

drop policy if exists "group avatars are publicly readable" on storage.objects;
create policy "group avatars are publicly readable"
  on storage.objects for select
  using (bucket_id = 'group-avatars');

drop policy if exists "group admins upload group avatar" on storage.objects;
create policy "group admins upload group avatar"
  on storage.objects for insert
  with check (
    bucket_id = 'group-avatars'
    and auth.role() = 'authenticated'
    and exists (
      select 1 from group_members gm
      where gm.group_id = (storage.foldername(name))[1]::uuid
        and gm.user_id = auth.uid()
        and gm.role = 'admin'
    )
  );

drop policy if exists "group admins manage group avatar" on storage.objects;
create policy "group admins manage group avatar"
  on storage.objects for update
  using (
    bucket_id = 'group-avatars'
    and exists (
      select 1 from group_members gm
      where gm.group_id = (storage.foldername(name))[1]::uuid
        and gm.user_id = auth.uid()
        and gm.role = 'admin'
    )
  );

drop policy if exists "group admins delete group avatar" on storage.objects;
create policy "group admins delete group avatar"
  on storage.objects for delete
  using (
    bucket_id = 'group-avatars'
    and exists (
      select 1 from group_members gm
      where gm.group_id = (storage.foldername(name))[1]::uuid
        and gm.user_id = auth.uid()
        and gm.role = 'admin'
    )
  );

-- ============================================================
-- End group_settings_migration
-- ============================================================
