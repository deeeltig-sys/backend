-- ============================================================
-- FRIENDSHIPS READ FIX — friends_migration.sql's own code comment in
-- routes/friends.py claims "RLS on friendships only restricts
-- writes, not reads... works for browsing anyone (matches how
-- Facebook's public friend lists behave by default)." The actual
-- policy that shipped says otherwise:
--
--   create policy friendships_select on friendships
--     for select using (user_a = auth.uid() or user_b = auth.uid());
--
-- That restricts every read to rows involving the CALLER, not the
-- profile being viewed. So GET /api/friends/<user_id> for anyone
-- else's profile only ever returns friendships that happen to also
-- involve you — for every other case it silently comes back empty or
-- wrong, which is the actual cause of "friends fails to load" on
-- someone else's profile. Writes are already correctly locked down
-- through the security-definer functions (send_friend_request,
-- accept_friend_request, etc.), so relaxing SELECT to public doesn't
-- open up anything writable — only what the app already intended:
-- friend lists are public, same as users_select_all and
-- follows_select_all already are.
-- ============================================================

drop policy if exists friendships_select on friendships;
create policy friendships_select on friendships for select using (true);
