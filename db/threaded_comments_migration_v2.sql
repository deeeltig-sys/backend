-- ============================================================
-- CampMEET THREADED COMMENTS MIGRATION (V2)
-- For deployments that already have comment_count on posts
-- ============================================================

-- Step 1: Add threading columns to comments
-- (Skip if you already have these)
ALTER TABLE comments 
ADD COLUMN parent_comment_id uuid REFERENCES comments(id) ON DELETE CASCADE;

ALTER TABLE comments 
ADD COLUMN reply_count int NOT NULL DEFAULT 0;

-- Step 2: Index for efficient thread queries
CREATE INDEX idx_comments_parent ON comments(parent_comment_id);

-- Step 3: Backfill reply_count on existing comments
-- Count replies (comments where this comment is the parent)
UPDATE comments c
SET reply_count = (
  SELECT COUNT(*) FROM comments replies
  WHERE replies.parent_comment_id = c.id 
  AND replies.status = 'active'
);

-- Step 4: Create/replace triggers to keep counts in sync
CREATE OR REPLACE FUNCTION bump_comment_counts()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF (TG_OP = 'INSERT') THEN
    -- If this is a top-level comment (no parent), increment post's comment_count
    IF new.parent_comment_id IS NULL THEN
      UPDATE posts SET comment_count = comment_count + 1 WHERE id = new.post_id;
    ELSE
      -- If this is a reply, increment the parent comment's reply_count
      UPDATE comments SET reply_count = reply_count + 1 WHERE id = new.parent_comment_id;
    END IF;
  ELSIF (TG_OP = 'DELETE') THEN
    -- Decrement the appropriate count
    IF old.parent_comment_id IS NULL THEN
      UPDATE posts SET comment_count = GREATEST(comment_count - 1, 0) WHERE id = old.post_id;
    ELSE
      UPDATE comments SET reply_count = GREATEST(reply_count - 1, 0) WHERE id = old.parent_comment_id;
    END IF;
  ELSIF (TG_OP = 'UPDATE') THEN
    -- If status changes to/from 'removed', adjust counts
    IF old.status <> new.status THEN
      IF new.status = 'removed' AND old.status = 'active' THEN
        -- Transitioning to removed (soft delete) — decrement count
        IF new.parent_comment_id IS NULL THEN
          UPDATE posts SET comment_count = GREATEST(comment_count - 1, 0) WHERE id = new.post_id;
        ELSE
          UPDATE comments SET reply_count = GREATEST(reply_count - 1, 0) WHERE id = new.parent_comment_id;
        END IF;
      ELSIF new.status = 'active' AND old.status = 'removed' THEN
        -- Transitioning back to active (restore) — increment count
        IF new.parent_comment_id IS NULL THEN
          UPDATE posts SET comment_count = comment_count + 1 WHERE id = new.post_id;
        ELSE
          UPDATE comments SET reply_count = reply_count + 1 WHERE id = new.parent_comment_id;
        END IF;
      END IF;
    END IF;
  END IF;
  RETURN NULL;
END; $$;

-- Drop old trigger if it exists, create fresh one
DROP TRIGGER IF EXISTS trg_comment_counts ON comments;
CREATE TRIGGER trg_comment_counts
AFTER INSERT OR DELETE OR UPDATE ON comments
FOR EACH ROW EXECUTE FUNCTION bump_comment_counts();

-- ============================================================
-- Verification
-- ============================================================
-- Run these queries to confirm everything is in place:

-- SELECT column_name FROM information_schema.columns 
-- WHERE table_name = 'comments' 
-- ORDER BY ordinal_position;

-- SELECT indexname FROM pg_indexes 
-- WHERE tablename = 'comments';

-- SELECT trigger_name FROM information_schema.triggers 
-- WHERE event_object_table = 'comments';
