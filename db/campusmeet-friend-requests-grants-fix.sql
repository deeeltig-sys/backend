-- ============================================================
-- FRIEND REQUEST RPC GRANTS FIX — every other security-definer RPC in
-- this codebase (verify_student, unverify_student, increment_view,
-- increment_search_hit, set_typing, get_other_typing) explicitly runs
--
--   revoke all on function X from public;
--   grant execute on function X to authenticated;
--
-- right after creating it. friends_migration.sql defines
-- send_friend_request, respond_to_friend_request, and
-- remove_friendship the same security-definer way, but never grants
-- execute to authenticated. Without that grant, PostgREST returns a
-- permission-denied error the moment a signed-in student's JWT tries
-- to call rpc/send_friend_request — which is the actual cause of
-- "tapping Add Friend does nothing, the button just goes back to
-- untapped": the frontend's optimistic-free FriendButton correctly
-- waits for the RPC to succeed before changing state, the RPC call
-- fails on a permissions error every single time, and the button
-- never had anything to revert from — it just never changed.
-- ============================================================

revoke all on function send_friend_request(uuid) from public;
grant execute on function send_friend_request(uuid) to authenticated;

revoke all on function respond_to_friend_request(uuid, boolean) from public;
grant execute on function respond_to_friend_request(uuid, boolean) to authenticated;

revoke all on function remove_friendship(uuid) from public;
grant execute on function remove_friendship(uuid) to authenticated;
