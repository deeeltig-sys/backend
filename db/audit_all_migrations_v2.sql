-- ============================================================
-- FULL MIGRATION AUDIT (v2) — run this in the Supabase SQL editor.
-- Prints PASS/MISSING for every table, column, trigger, function, and
-- policy across every migration file this project has, in one shot.
-- This checks the LIVE DATABASE, not the repo — a file existing in
-- db/ does not mean it was ever run, and vice versa (that mismatch
-- has been the root cause of more than one "this feature doesn't
-- work" bug already: notifications, follow counts, and bio/social
-- links all turned out to be unrun migrations, not code bugs).
-- ============================================================

select check_name, status from (
  values
    -- schema.sql
    ('schema.sql — universities table', (select exists (select 1 from information_schema.tables where table_name = 'universities'))),
    ('schema.sql — posts table', (select exists (select 1 from information_schema.tables where table_name = 'posts'))),
    ('rls_policies.sql — users_select_all policy', (select exists (select 1 from pg_policies where policyname = 'users_select_all'))),
    ('storage_policies.sql — post-images upload policy', (select exists (select 1 from pg_policies where policyname = 'students upload only into their own folder'))),
    ('avatar_storage_policies.sql — avatar upload policy', (select exists (select 1 from pg_policies where policyname = 'students upload only their own avatar'))),

    -- v2_migration.sql
    ('v2_migration.sql — posts.comment_count column', (select exists (select 1 from information_schema.columns where table_name = 'posts' and column_name = 'comment_count'))),
    ('v2_migration.sql — trg_comment_count trigger', (select exists (select 1 from pg_trigger where tgname = 'trg_comment_count'))),
    ('v2_migration.sql — feed_score(4-arg) function', (select exists (select 1 from pg_proc where proname = 'feed_score' and pronargs = 4))),

    -- fix_guard_exemption.sql / university_signup_migration.sql
    ('fix_guard_exemption.sql — guard_post_moderation_fields()', (select exists (select 1 from pg_proc where proname = 'guard_post_moderation_fields'))),
    ('university_signup_migration.sql — get_or_create_university()', (select exists (select 1 from pg_proc where proname = 'get_or_create_university'))),
    ('seed_universities.sql — universities seeded (68+)', (select count(*) >= 68 from universities)),

    -- v3_social_migration.sql
    ('v3_social_migration.sql — follows table', (select exists (select 1 from information_schema.tables where table_name = 'follows'))),
    ('v3_social_migration.sql — notifications table', (select exists (select 1 from information_schema.tables where table_name = 'notifications'))),
    ('v3_social_migration.sql — conversations table', (select exists (select 1 from information_schema.tables where table_name = 'conversations'))),
    ('v3_social_migration.sql — messages table', (select exists (select 1 from information_schema.tables where table_name = 'messages'))),
    ('v3_social_migration.sql — trg_follow_counts trigger', (select exists (select 1 from pg_trigger where tgname = 'trg_follow_counts'))),
    ('v3_social_migration.sql — trg_notify_follow trigger', (select exists (select 1 from pg_trigger where tgname = 'trg_notify_follow'))),
    ('v3_social_migration.sql — trg_notify_comment trigger', (select exists (select 1 from pg_trigger where tgname = 'trg_notify_comment'))),
    ('v3_social_migration.sql — trg_notify_reaction trigger', (select exists (select 1 from pg_trigger where tgname = 'trg_notify_reaction'))),
    ('v3_social_migration.sql — trg_message_notify trigger', (select exists (select 1 from pg_trigger where tgname = 'trg_message_notify'))),

    -- bio_and_social_links_migration.sql
    ('bio_and_social_links_migration.sql — users.social_links column', (select exists (select 1 from information_schema.columns where table_name = 'users' and column_name = 'social_links'))),
    ('bio_and_social_links_migration.sql — users.bio column', (select exists (select 1 from information_schema.columns where table_name = 'users' and column_name = 'bio'))),

    -- level_of_study_migration.sql
    ('level_of_study_migration.sql — users.level_of_study column', (select exists (select 1 from information_schema.columns where table_name = 'users' and column_name = 'level_of_study'))),

    -- feed_randomization_migration.sql
    ('feed_randomization_migration.sql — feed view exists', (select exists (select 1 from information_schema.views where table_name = 'feed'))),
    ('feed_randomization_migration.sql — feed.repost_of present (implies rebuilt post-reposts)', (select exists (select 1 from information_schema.columns where table_name = 'feed' and column_name = 'repost_of'))),

    -- chat_overhaul_migration.sql
    ('chat_overhaul_migration.sql — messages.read_at column', (select exists (select 1 from information_schema.columns where table_name = 'messages' and column_name = 'read_at'))),
    ('chat_overhaul_migration.sql — conversation_user_state table', (select exists (select 1 from information_schema.tables where table_name = 'conversation_user_state'))),
    ('chat_overhaul_migration.sql — set_conversation_state()', (select exists (select 1 from pg_proc where proname = 'set_conversation_state'))),
    ('chat_overhaul_migration.sql — purge_expired_deleted_conversations()', (select exists (select 1 from pg_proc where proname = 'purge_expired_deleted_conversations'))),
    ('chat_overhaul_migration.sql — messages_update_read_receipt policy', (select exists (select 1 from pg_policies where policyname = 'messages_update_read_receipt'))),

    -- status_and_settings_migration.sql
    ('status_and_settings_migration.sql — statuses table', (select exists (select 1 from information_schema.tables where table_name = 'statuses'))),
    ('status_and_settings_migration.sql — status_views table', (select exists (select 1 from information_schema.tables where table_name = 'status_views'))),
    ('status_and_settings_migration.sql — users.default_wallpaper column', (select exists (select 1 from information_schema.columns where table_name = 'users' and column_name = 'default_wallpaper'))),

    -- saved_posts_migration.sql
    ('saved_posts_migration.sql — saved_posts table', (select exists (select 1 from information_schema.tables where table_name = 'saved_posts'))),

    -- reposts_migration.sql
    ('reposts_migration.sql — posts.repost_of column', (select exists (select 1 from information_schema.columns where table_name = 'posts' and column_name = 'repost_of'))),

    -- friends_migration.sql
    ('friends_migration.sql — friend_requests table', (select exists (select 1 from information_schema.tables where table_name = 'friend_requests'))),
    ('friends_migration.sql — friendships table', (select exists (select 1 from information_schema.tables where table_name = 'friendships'))),
    ('friends_migration.sql — send_friend_request()', (select exists (select 1 from pg_proc where proname = 'send_friend_request'))),
    ('friends_migration.sql — respond_to_friend_request()', (select exists (select 1 from pg_proc where proname = 'respond_to_friend_request'))),

    -- notifications_fix_migration.sql
    ('notifications_fix_migration.sql — friend_request enum value', (select exists (select 1 from pg_enum e join pg_type t on e.enumtypid = t.oid where t.typname = 'notification_type' and e.enumlabel = 'friend_request'))),
    ('notifications_fix_migration.sql — trg_notify_friend_request trigger', (select exists (select 1 from pg_trigger where tgname = 'trg_notify_friend_request'))),
    ('notifications_fix_migration.sql — trg_notify_friend_accept trigger', (select exists (select 1 from pg_trigger where tgname = 'trg_notify_friend_accept')))
) as t(check_name, ok)
cross join lateral (select case when ok then 'PASS' else '!! MISSING — run the file above' end as status) s
order by check_name;
