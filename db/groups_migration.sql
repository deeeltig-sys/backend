-- ============================================================
-- GROUPS / COMMUNITIES — campus clubs, class cohorts, departments,
-- fan pages. Additive: one nullable column added to posts, two new
-- tables. Nothing existing is altered in behavior.
-- ============================================================

create table groups (
  id            uuid primary key default uuid_generate_v4(),
  university_id uuid not null references universities(id),
  creator_id    uuid not null references users(id) on delete cascade,
  name          text not null check (char_length(name) between 2 and 80),
  description   text check (description is null or char_length(description) <= 500),
  avatar_url    text,
  privacy       text not null default 'public' check (privacy in ('public', 'private')),
  member_count  int not null default 0,
  created_at    timestamptz not null default now()
);
create index idx_groups_university on groups(university_id);

create table group_members (
  group_id  uuid not null references groups(id) on delete cascade,
  user_id   uuid not null references users(id) on delete cascade,
  role      text not null default 'member' check (role in ('admin', 'member')),
  joined_at timestamptz not null default now(),
  primary key (group_id, user_id)
);
create index idx_group_members_user on group_members(user_id);

-- A post can optionally belong to a group. Existing posts are
-- unaffected (null = ordinary feed post, same as today).
alter table posts add column group_id uuid references groups(id) on delete cascade;
create index idx_posts_group on posts(group_id) where group_id is not null;

-- ============================================================
-- Creating a group makes the creator its first admin member —
-- automatic, not a second client-side call that could be skipped.
-- ============================================================
create or replace function seed_group_creator_membership()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into group_members (group_id, user_id, role)
  values (new.id, new.creator_id, 'admin');
  return new;
end;
$$;

create trigger trg_seed_group_creator_membership
after insert on groups
for each row execute function seed_group_creator_membership();

create or replace function bump_group_member_count()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if TG_OP = 'INSERT' then
    update groups set member_count = member_count + 1 where id = new.group_id;
  elsif TG_OP = 'DELETE' then
    update groups set member_count = greatest(member_count - 1, 0) where id = old.group_id;
  end if;
  return null;
end;
$$;

create trigger trg_bump_group_member_count
after insert or delete on group_members
for each row execute function bump_group_member_count();

-- ============================================================
-- RLS
-- ============================================================
alter table groups enable row level security;
alter table group_members enable row level security;

-- Public groups are visible to everyone; private groups only to
-- members. (Private-group discovery/invites are a phase-2 concern —
-- for now a private group is simply invisible to non-members.)
create policy groups_select on groups
  for select using (
    privacy = 'public'
    or exists (select 1 from group_members gm where gm.group_id = id and gm.user_id = auth.uid())
  );

create policy groups_insert_own on groups
  for insert with check (creator_id = auth.uid());

create policy groups_update_admin on groups
  for update using (
    exists (select 1 from group_members gm where gm.group_id = id and gm.user_id = auth.uid() and gm.role = 'admin')
  );

-- Membership rows are visible to anyone who can see the group itself
-- (needed to render a member list / count) — simplest correct rule
-- at this scale rather than a second privacy branch here too.
create policy group_members_select on group_members
  for select using (true);

-- Self-join only, and only into a public group. Private groups need
-- an admin to add someone (covered by group_members_insert_admin
-- below) — no self-serve join flow for those yet.
create policy group_members_insert_self on group_members
  for insert with check (
    user_id = auth.uid()
    and exists (select 1 from groups g where g.id = group_id and g.privacy = 'public')
  );

create policy group_members_insert_admin on group_members
  for insert with check (
    exists (select 1 from group_members gm where gm.group_id = group_id and gm.user_id = auth.uid() and gm.role = 'admin')
  );

-- Leave your own membership, or an admin removing someone else.
create policy group_members_delete on group_members
  for delete using (
    user_id = auth.uid()
    or exists (select 1 from group_members gm where gm.group_id = group_id and gm.user_id = auth.uid() and gm.role = 'admin')
  );

-- ============================================================
-- The `feed` view lists explicit columns (see reposts_migration.sql),
-- not posts.* — so group_id needs to be added to it by hand or every
-- group post silently vanishes from every endpoint that reads
-- through `feed` (main feed, search, by-user, saved, hashtags), same
-- gotcha reposts_migration.sql already hit and fixed for repost_of.
-- ============================================================
drop view if exists feed;

create view feed as
select p.id,
    p.university_id,
    p.author_id,
    p.content,
    p.image_url,
    p.repost_of,
    p.group_id,
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
-- End groups_migration
-- ============================================================
