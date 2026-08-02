-- ============================================================
-- POLLS — a poll is an ordinary post (the question lives in
-- posts.content, same 2000-char field every post already uses) with
-- 2-4 attached options. Presence of rows in poll_options is what
-- makes a post "a poll" — no new column needed on posts itself.
-- ============================================================

create table poll_options (
  id          uuid primary key default uuid_generate_v4(),
  post_id     uuid not null references posts(id) on delete cascade,
  option_text text not null check (char_length(option_text) between 1 and 80),
  order_index int not null default 0,
  vote_count  int not null default 0,
  created_at  timestamptz not null default now()
);
create index idx_poll_options_post on poll_options(post_id);

create table poll_votes (
  post_id    uuid not null references posts(id) on delete cascade,
  user_id    uuid not null references users(id) on delete cascade,
  option_id  uuid not null references poll_options(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (post_id, user_id)
);
create index idx_poll_votes_option on poll_votes(option_id);

-- ============================================================
-- Keeps poll_options.vote_count correct through every case: a first
-- vote, a full retraction, and — since a person can change their
-- mind — switching from one option to another on the same post
-- (UPDATE, not a fresh INSERT, thanks to the primary key on
-- (post_id, user_id) forcing an upsert-by-replace pattern from the
-- API layer).
-- ============================================================
create or replace function bump_poll_vote_count()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if TG_OP = 'INSERT' then
    update poll_options set vote_count = vote_count + 1 where id = new.option_id;
  elsif TG_OP = 'DELETE' then
    update poll_options set vote_count = greatest(vote_count - 1, 0) where id = old.option_id;
  elsif TG_OP = 'UPDATE' and new.option_id <> old.option_id then
    update poll_options set vote_count = greatest(vote_count - 1, 0) where id = old.option_id;
    update poll_options set vote_count = vote_count + 1 where id = new.option_id;
  end if;
  return null;
end;
$$;

create trigger trg_bump_poll_vote_count
after insert or update of option_id or delete on poll_votes
for each row execute function bump_poll_vote_count();

-- ============================================================
-- RLS — option text and running totals are public (that's the
-- whole point of a poll: everyone sees the results). Individual
-- ballots are not: a poll_votes row is only ever visible to the
-- person who cast it, same "totals public, ballot private" contract
-- as every mainstream poll feature. The post author does not get a
-- special exception to see who voted for what.
-- ============================================================
alter table poll_options enable row level security;
alter table poll_votes enable row level security;

create policy poll_options_select_all on poll_options for select using (true);

-- Options are only ever created by the backend at post-creation
-- time, as the same author who is allowed to create the post itself.
create policy poll_options_insert_own_post on poll_options
  for insert with check (
    exists (select 1 from posts p where p.id = post_id and p.author_id = auth.uid())
  );

create policy poll_votes_select_own on poll_votes for select using (user_id = auth.uid());
create policy poll_votes_insert_own on poll_votes for insert with check (user_id = auth.uid());
create policy poll_votes_update_own on poll_votes for update using (user_id = auth.uid());
create policy poll_votes_delete_own on poll_votes for delete using (user_id = auth.uid());

-- ============================================================
-- End polls_migration
-- ============================================================
