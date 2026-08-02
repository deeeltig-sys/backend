-- ============================================================
-- EVENTS — campus events with RSVP (interested/going), FB Events
-- style. Optionally tied to a group (e.g. an SRC group posting its
-- own rally), but standalone events work fine too.
-- ============================================================

create table events (
  id               uuid primary key default uuid_generate_v4(),
  university_id    uuid not null references universities(id),
  creator_id       uuid not null references users(id) on delete cascade,
  group_id         uuid references groups(id) on delete set null,
  title            text not null check (char_length(title) between 2 and 120),
  description      text check (description is null or char_length(description) <= 2000),
  location         text check (location is null or char_length(location) <= 200),
  cover_url        text,
  start_at         timestamptz not null,
  end_at           timestamptz,
  interested_count int not null default 0,
  going_count      int not null default 0,
  created_at       timestamptz not null default now(),
  check (end_at is null or end_at > start_at)
);
create index idx_events_university on events(university_id);
create index idx_events_start_at on events(start_at);
create index idx_events_group on events(group_id) where group_id is not null;

create table event_rsvps (
  event_id   uuid not null references events(id) on delete cascade,
  user_id    uuid not null references users(id) on delete cascade,
  status     text not null check (status in ('interested', 'going')),
  created_at timestamptz not null default now(),
  primary key (event_id, user_id)
);
create index idx_event_rsvps_user on event_rsvps(user_id);

create or replace function bump_event_rsvp_counts()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if TG_OP = 'INSERT' then
    if new.status = 'interested' then
      update events set interested_count = interested_count + 1 where id = new.event_id;
    else
      update events set going_count = going_count + 1 where id = new.event_id;
    end if;
  elsif TG_OP = 'DELETE' then
    if old.status = 'interested' then
      update events set interested_count = greatest(interested_count - 1, 0) where id = old.event_id;
    else
      update events set going_count = greatest(going_count - 1, 0) where id = old.event_id;
    end if;
  elsif TG_OP = 'UPDATE' and new.status <> old.status then
    -- Switching between interested <-> going, not just re-inserting.
    if old.status = 'interested' then
      update events set interested_count = greatest(interested_count - 1, 0) where id = old.event_id;
    else
      update events set going_count = greatest(going_count - 1, 0) where id = old.event_id;
    end if;
    if new.status = 'interested' then
      update events set interested_count = interested_count + 1 where id = new.event_id;
    else
      update events set going_count = going_count + 1 where id = new.event_id;
    end if;
  end if;
  return null;
end;
$$;

create trigger trg_bump_event_rsvp_counts
after insert or update of status or delete on event_rsvps
for each row execute function bump_event_rsvp_counts();

-- ============================================================
-- RLS — public read, same openness as posts/statuses; only the
-- creator can edit their own event; RSVPs are self-managed.
-- ============================================================
alter table events enable row level security;
alter table event_rsvps enable row level security;

create policy events_select_all on events for select using (true);
create policy events_insert_own on events for insert with check (creator_id = auth.uid());
create policy events_update_own on events for update using (creator_id = auth.uid());
create policy events_delete_own on events for delete using (creator_id = auth.uid());

create policy event_rsvps_select_all on event_rsvps for select using (true);
create policy event_rsvps_insert_own on event_rsvps for insert with check (user_id = auth.uid());
create policy event_rsvps_update_own on event_rsvps for update using (user_id = auth.uid());
create policy event_rsvps_delete_own on event_rsvps for delete using (user_id = auth.uid());

-- ============================================================
-- End events_migration
-- ============================================================
