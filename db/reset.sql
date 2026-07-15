-- ============================================================
-- CAMPUS PLATFORM — RESET (run this FIRST, then run
-- campus_platform_schema_current.sql right after)
-- ============================================================
-- Drops only Campus Platform objects, by name. Does not touch
-- auth.users, storage, or anything else in the Supabase project.
-- Run this ONLY if there is no real data you need to keep.
-- ============================================================

drop view if exists feed;

drop table if exists blocks cascade;
drop table if exists hidden_posts cascade;
drop table if exists reports cascade;
drop table if exists comments cascade;
drop table if exists reactions cascade;
drop table if exists posts cascade;
drop table if exists users cascade;
drop table if exists universities cascade;

drop function if exists handle_new_auth_user() cascade;
drop function if exists is_staff() cascade;
drop function if exists verify_student(uuid) cascade;
drop function if exists unverify_student(uuid) cascade;
drop function if exists guard_post_moderation_fields() cascade;
drop function if exists bump_reaction_count() cascade;
drop function if exists bump_report_count() cascade;
drop function if exists bump_standing_count() cascade;
drop function if exists increment_view(uuid) cascade;
drop function if exists increment_search_hit(uuid) cascade;
drop function if exists feed_score(int, int, int) cascade;

drop type if exists report_status;
drop type if exists report_reason;
drop type if exists report_target;
drop type if exists reaction_type;
drop type if exists post_status;
drop type if exists user_status;
drop type if exists user_role;

-- ============================================================
-- Done. Now run campus_platform_schema_current.sql.
-- ============================================================
