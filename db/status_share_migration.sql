-- ============================================================
-- SHARE POST TO STORY — adds a third statuses.content_type ('post'),
-- carrying a reference to an existing post instead of its own
-- image/text. Viewing/expiry/deletion all reuse the exact same
-- statuses infrastructure already in place — no new table needed.
-- ============================================================

alter table statuses add column if not exists shared_post_id uuid references posts(id) on delete cascade;

-- Replace both check constraints to include the new type. Rather than
-- assuming Postgres's default auto-generated names (statuses_check,
-- statuses_content_type_check) — a guess that, if wrong, would leave
-- the OLD restrictive constraint silently active alongside a new one
-- and still block 'post' inserts — this looks up and drops whatever
-- check constraints actually exist on this table by inspecting
-- pg_constraint directly.
do $$
declare
  r record;
begin
  for r in
    select conname from pg_constraint
    where conrelid = 'statuses'::regclass and contype = 'c'
  loop
    execute format('alter table statuses drop constraint %I', r.conname);
  end loop;
end $$;

alter table statuses add constraint statuses_content_type_check
  check (content_type in ('image', 'text', 'post'));

alter table statuses add constraint statuses_content_check
  check (
    (content_type = 'image' and image_url is not null) or
    (content_type = 'text' and text_content is not null) or
    (content_type = 'post' and shared_post_id is not null)
  );

create index if not exists idx_statuses_shared_post on statuses(shared_post_id);
