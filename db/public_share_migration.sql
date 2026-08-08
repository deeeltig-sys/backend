-- ============================================================
-- SHARE-TO-SIGNUP: public post preview
--
-- New, isolated read path for unauthenticated visitors clicking a
-- shared post link. Deliberately NOT built on the `feed` view — that
-- view has been redefined by multiple later migrations with
-- different explicit column lists (see the separate note on the
-- `audience` column regression), so anything public-facing needs its
-- own explicit, minimal column list rather than inheriting whatever
-- `feed` happens to select today.
--
-- security definer + explicit column whitelist is intentional here:
-- posts/users have several columns that must never reach an anonymous
-- caller (student_id_number, student_email, report_count, etc.) — RLS
-- controls which ROWS are visible, not which COLUMNS, so table-level
-- grants alone would not protect those fields.
-- ============================================================

create or replace function get_public_post(p_post_id uuid)
returns table (
  id                uuid,
  content           text,
  image_url         text,
  image_urls        text[],
  reaction_count    int,
  comment_count     int,
  created_at        timestamptz,
  author_id         uuid,
  author_full_name  text,
  author_avatar_url text,
  author_verified   boolean
)
language sql
security definer
stable
set search_path = public
as $$
  select
    p.id,
    p.content,
    p.image_url,
    coalesce(
      (select array_agg(pi.image_url order by pi.order_index)
       from post_images pi where pi.post_id = p.id),
      case when p.image_url is not null then array[p.image_url] else null end
    ) as image_urls,
    p.reaction_count,
    p.comment_count,
    p.created_at,
    u.id,
    u.full_name,
    u.avatar_url,
    u.verified_at is not null
  from posts p
  join users u on u.id = p.author_id
  where p.id = p_post_id
    and p.status = 'active'
    and p.audience = 'public';
$$;

grant execute on function get_public_post(uuid) to anon, authenticated;
