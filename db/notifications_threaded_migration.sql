-- ============================================================
-- NOTIFICATIONS UPDATE FOR THREADED COMMENTS
-- Extends the existing notify_on_comment() trigger to handle
-- both top-level comments (notify post author) and replies
-- (notify parent comment author).
-- ============================================================

-- Step 1: Ensure notification_type enum has 'comment_reply'
ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'comment_reply';

-- Step 2: Create/replace the updated comment notification trigger
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
    -- Case 2: REPLY TO COMMENT (has parent) → notify parent comment author
    ELSE
      SELECT author_id INTO v_parent_comment_author FROM comments WHERE id = new.parent_comment_id;
      IF v_parent_comment_author IS NOT NULL AND v_parent_comment_author <> new.author_id THEN
        INSERT INTO notifications (user_id, actor_id, type, target_type, target_id)
        VALUES (v_parent_comment_author, new.author_id, 'comment_reply', 'comment', new.id);
      END IF;
    END IF;
  END IF;
  RETURN new;
END;
$$;

-- Step 3: Recreate the trigger
DROP TRIGGER IF EXISTS trg_notify_comment ON comments;
CREATE TRIGGER trg_notify_comment
AFTER INSERT ON comments
FOR EACH ROW EXECUTE FUNCTION notify_on_comment();

-- ============================================================
-- Verify it worked
-- ============================================================
-- SELECT enumlabel FROM pg_enum WHERE enumtypid = 'notification_type'::regtype;
-- SELECT trigger_name FROM information_schema.triggers WHERE event_object_table = 'comments';
