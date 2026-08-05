-- ============================================================
-- POST AUDIENCE (Public / Friends) — same "app-level filtering, not
-- DB-level RLS" model this codebase already uses for blocking (see
-- _filter_blocked in routes/posts.py): the `feed` view itself stays
-- broad, and Flask narrows what actually gets returned to a given
-- viewer. That's a deliberate, pre-existing trade-off here, not
-- something new introduced by this feature — worth knowing, not
-- worth re-architecting for this pass.
-- ============================================================

alter table posts add column audience text not null default 'public'
  check (audience in ('public', 'friends'));

-- `feed` lists explicit columns rather than posts.* (same gotcha
-- group_id and repost_of both already hit) — audience has to be
-- added here by hand too, or every audience-filtering check in
-- posts.py would silently have nothing to check against.
drop view if exists feed;

create view feed as
select p.id,
    p.university_id,
    p.author_id,
    p.content,
    p.image_url,
    p.repost_of,
    p.group_id,
    p.audience,
    p.view_count,
    p.search_hit_count,
    p.reaction_count,
    p.report_count,
    p.status,
    p.created_at,
    feed_score(p.view_count, p.reaction_count, p.search_hit_count, p.created_at) as score,
    u.full_name as author_full_name,
    u.avatar_url as author_avatar_url,
    u.verified_at is not null as author_verified,
    p.comment_count
   from posts p
     left join users u on u.id = p.author_id
  where p.status = 'active'::post_status
  order by power(
    random(),
    1.0 / (feed_score(p.view_count, p.reaction_count, p.search_hit_count, p.created_at) + 1)
  ) desc;

-- ============================================================
-- End audience_migration
-- ============================================================
