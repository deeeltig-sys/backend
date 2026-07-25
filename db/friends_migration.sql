-- ============================================================
-- FRIENDS — a genuinely separate relationship from the existing
-- `follows` table. Follow stays a lightweight one-way "keep up with
-- their posts" (feeds this drives the connect-hub/suggested-people
-- carousel). Friends is the deeper, mutual, Facebook-style layer:
-- both people have to agree, and it's what powers browsing someone's
-- friend list and friends-of-friends discovery. Deliberately NOT
-- merged into `follows` — they're different relationships with
-- different UX, forcing them into one table would mean overloading
-- one column to mean two different things.
-- ============================================================

create table if not exists friend_requests (
  id           uuid primary key default uuid_generate_v4(),
  sender_id    uuid not null references users(id) on delete cascade,
  receiver_id  uuid not null references users(id) on delete cascade,
  status       text not null default 'pending' check (status in ('pending', 'accepted', 'declined')),
  created_at   timestamptz not null default now(),
  responded_at timestamptz,
  unique (sender_id, receiver_id),
  check (sender_id <> receiver_id)
);

create index if not exists idx_friend_requests_receiver on friend_requests(receiver_id, status);
create index if not exists idx_friend_requests_sender on friend_requests(sender_id, status);

-- Normalized so each pair exists as exactly one row regardless of who
-- sent the original request — user_a is always the smaller uuid.
create table if not exists friendships (
  user_a     uuid not null references users(id) on delete cascade,
  user_b     uuid not null references users(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_a, user_b),
  check (user_a < user_b)
);

create index if not exists idx_friendships_user_b on friendships(user_b);

alter table friend_requests enable row level security;
alter table friendships enable row level security;

drop policy if exists friend_requests_select on friend_requests;
create policy friend_requests_select on friend_requests
  for select using (sender_id = auth.uid() or receiver_id = auth.uid());

drop policy if exists friend_requests_insert on friend_requests;
create policy friend_requests_insert on friend_requests
  for insert with check (sender_id = auth.uid());

-- Only the receiver can change status (accept/decline) — the sender
-- can still "update" their own row to re-open a declined request via
-- the respond/send RPCs below, both security definer.
drop policy if exists friend_requests_update_receiver on friend_requests;
create policy friend_requests_update_receiver on friend_requests
  for update using (receiver_id = auth.uid()) with check (receiver_id = auth.uid());

-- Sender can cancel their own pending request.
drop policy if exists friend_requests_delete_sender on friend_requests;
create policy friend_requests_delete_sender on friend_requests
  for delete using (sender_id = auth.uid() and status = 'pending');

drop policy if exists friendships_select on friendships;
create policy friendships_select on friendships
  for select using (user_a = auth.uid() or user_b = auth.uid());
-- No direct insert/update/delete policy on friendships — every write
-- goes through the security-definer functions below, since the
-- user_a < user_b normalization has to be enforced consistently.

create or replace function send_friend_request(p_receiver_id uuid) returns void
language plpgsql security definer as $$
begin
  if p_receiver_id = auth.uid() then
    raise exception 'cannot friend yourself';
  end if;
  if exists (
    select 1 from friendships
    where (user_a = least(auth.uid(), p_receiver_id) and user_b = greatest(auth.uid(), p_receiver_id))
  ) then
    raise exception 'already friends';
  end if;

  insert into friend_requests (sender_id, receiver_id, status)
  values (auth.uid(), p_receiver_id, 'pending')
  on conflict (sender_id, receiver_id) do update
    set status = 'pending', responded_at = null, created_at = now()
    where friend_requests.status = 'declined';
end;
$$;

create or replace function respond_to_friend_request(p_request_id uuid, p_accept boolean) returns void
language plpgsql security definer as $$
declare
  v_request friend_requests;
begin
  select * into v_request from friend_requests where id = p_request_id and receiver_id = auth.uid();
  if v_request is null then
    raise exception 'request not found';
  end if;

  update friend_requests
  set status = case when p_accept then 'accepted' else 'declined' end, responded_at = now()
  where id = p_request_id;

  if p_accept then
    insert into friendships (user_a, user_b)
    values (least(v_request.sender_id, v_request.receiver_id), greatest(v_request.sender_id, v_request.receiver_id))
    on conflict do nothing;
  end if;
end;
$$;

create or replace function remove_friendship(p_other_user_id uuid) returns void
language plpgsql security definer as $$
begin
  delete from friendships
  where user_a = least(auth.uid(), p_other_user_id) and user_b = greatest(auth.uid(), p_other_user_id);
  -- Also clear any old request row between the two so a fresh request
  -- can be sent later instead of hitting the unique-constraint ghost
  -- of a long-accepted one.
  delete from friend_requests
  where (sender_id = auth.uid() and receiver_id = p_other_user_id)
     or (sender_id = p_other_user_id and receiver_id = auth.uid());
end;
$$;
