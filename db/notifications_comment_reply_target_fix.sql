-- ============================================================
-- NOTIFICATIONS FIX — comment_reply target
--
-- notifications_threaded_migration.sql introduced 'comment_reply'
-- with target_type='comment', target_id=<comment id>. That's a dead
-- end for the frontend: every comment endpoint is nested under a
-- post (GET /api/posts/:post_id/comments), so a bare comment id with
-- no post context can't be resolved to anywhere to navigate. The
-- 'comment' type right next to it already solved this correctly by
-- storing the POST id instead — this migration makes comment_reply
-- do the same, so the frontend can treat both types identically for
-- navigation (open the post) while still keeping the distinct
-- 'comment_reply' type for its own notification text ("X replied to
-- your comment" vs "X commented on your post").
--
-- Safe to run more than once (CREATE OR REPLACE + DROP/CREATE
-- TRIGGER).
-- ============================================================

CREATE OR REPLACE FUNCTION notify_on_comment()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_post_author uuid;
  v_parent_comment_author uuid;
BEGIN
  IF new.status = 'active' THEN
    -- Case 1: TOP-LEVEL COMMENT (no parent) → notify post author
    IF new.parent_comment_id IS NULL THEN
      SELECT author_id INTO v_post_author FROM posts WHERE id = new.post_id;
      IF v_post_author IS NOT NULL AND v_post_author <> new.author_id THEN
        INSERT INTO notifications (user_id, actor_id, type, target_type, target_id)
        VALUES (v_post_author, new.author_id, 'comment', 'post', new.post_id);
      END IF;
    -- Case 2: REPLY TO COMMENT (has parent) → notify parent comment
    -- author. target_type/target_id now point at the POST (new.post_id),
    -- same as the top-level case above — NOT the comment id (new.id)
    -- like before. The type stays 'comment_reply' so the notification
    -- text can still say "replied to your comment" specifically.
    ELSE
      SELECT author_id INTO v_parent_comment_author FROM comments WHERE id = new.parent_comment_id;
      IF v_parent_comment_author IS NOT NULL AND v_parent_comment_author <> new.author_id THEN
        INSERT INTO notifications (user_id, actor_id, type, target_type, target_id)
        VALUES (v_parent_comment_author, new.author_id, 'comment_reply', 'post', new.post_id);
      END IF;
    END IF;
  END IF;
  RETURN new;
END;
$$;

DROP TRIGGER IF EXISTS trg_notify_comment ON comments;
CREATE TRIGGER trg_notify_comment
AFTER INSERT ON comments
FOR EACH ROW EXECUTE FUNCTION notify_on_comment();

-- ============================================================
-- Verify it worked
-- ============================================================
-- Reply to any comment, then:
-- SELECT type, target_type, target_id FROM notifications
--   WHERE type = 'comment_reply' ORDER BY created_at DESC LIMIT 1;
-- target_type should read 'post', and target_id should match a real
-- posts.id (the post the comment/reply is on) — not a comments.id.
