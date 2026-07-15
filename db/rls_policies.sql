-- ============================================================
-- REFERENCE ONLY — these policies are already included inside
-- schema.sql and get created when schema.sql runs. This file is
-- not meant to be run separately; it exists so the RLS ruleset
-- has one obvious place to read, matching the original repo tree.
-- If you ever do need to reapply just the policies on their own,
-- drop each one first (they are not idempotent otherwise).
-- ============================================================

-- USERS: profiles are public; you can only edit your own; staff can moderate.
create policy users_select_all   on users for select using (true);
create policy users_update_own   on users for update using (id = auth.uid());
create policy users_staff_update on users for update using (is_staff());

-- POSTS: active posts are public; author sees their own regardless of status.
-- Self-delete is soft (UPDATE -> status = 'removed'); hard DELETE is staff-only.
create policy posts_select       on posts for select using (status = 'active' or author_id = auth.uid() or is_staff());
create policy posts_insert       on posts for insert with check (author_id = auth.uid());
create policy posts_update_own   on posts for update using (author_id = auth.uid() or is_staff());
create policy posts_delete_staff on posts for delete using (is_staff());

-- REACTIONS: visible to all, only the reactor can create/remove their own.
create policy reactions_select     on reactions for select using (true);
create policy reactions_insert     on reactions for insert with check (user_id = auth.uid());
create policy reactions_delete_own on reactions for delete using (user_id = auth.uid());

-- COMMENTS: same shape as posts.
create policy comments_select     on comments for select using (status = 'active' or author_id = auth.uid() or is_staff());
create policy comments_insert     on comments for insert with check (author_id = auth.uid());
create policy comments_update_own on comments for update using (author_id = auth.uid() or is_staff());
create policy comments_delete_own on comments for delete using (author_id = auth.uid() or is_staff());

-- REPORTS: reporter sees only their own; staff see all; only staff can update status.
create policy reports_insert           on reports for insert with check (reporter_id = auth.uid());
create policy reports_select_own_staff on reports for select using (reporter_id = auth.uid() or is_staff());
create policy reports_staff_update     on reports for update using (is_staff());

-- HIDDEN POSTS: fully private to the user who hid the post.
create policy hidden_posts_all_own on hidden_posts for all
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- BLOCKS: fully private to the user who created the block.
create policy blocks_all_own on blocks for all
  using (blocker_id = auth.uid()) with check (blocker_id = auth.uid());
