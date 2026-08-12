-- ============================================================
-- FIX — announce_as_okyeame() never set posts.university_id, which
-- is NOT NULL. The campus-scope BYPASS for official posts happens in
-- routes/posts.py's feed query filter (author_is_official.eq.true),
-- not by leaving the column empty — the column itself still needs a
-- real value to satisfy the constraint, it's just not what the feed
-- filters on for this account. Uses Okyeame's own university_id from
-- her signup (whichever one was picked doesn't matter for reach).
-- ============================================================

create or replace function announce_as_okyeame(p_content text, p_image_url text default null)
returns posts
language plpgsql
security definer
set search_path = public
as $$
declare
  v_okyeame_id uuid;
  v_university_id uuid;
  v_post posts;
begin
  if not is_owner() then
    raise exception 'not authorized';
  end if;

  select id, university_id into v_okyeame_id, v_university_id
  from users where is_official = true limit 1;

  if v_okyeame_id is null then
    raise exception 'okyeame account not configured — set is_official = true on its users row first';
  end if;

  if p_content is null or length(trim(p_content)) = 0 then
    raise exception 'content cannot be empty';
  end if;

  insert into posts (author_id, university_id, content, image_url, status, audience)
  values (v_okyeame_id, v_university_id, trim(p_content), p_image_url, 'active', 'public')
  returning * into v_post;

  return v_post;
end;
$$;

-- ============================================================
-- VERIFY: re-run the same announcement in the Okyeame panel — it
-- should post successfully this time, and still show up in every
-- university's feed (not just the one v_university_id happens to be),
-- since that reach comes from routes/posts.py's author_is_official
-- bypass, unaffected by this fix.
-- ============================================================
