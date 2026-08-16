-- ============================================================
-- ADMIN AUDIT LOG — every privileged action taken by staff, with who
-- did it and when. Read access is any staff member (transparency
-- within the team, not just owner-only surveillance); write access
-- is locked to inserting your own actions while authenticated as
-- staff — nothing lets anyone log an action as someone else, and
-- there's no UPDATE/DELETE policy at all, so entries can't be edited
-- or removed once written, by anyone, including the owner.
-- ============================================================

create table if not exists admin_actions (
  id          uuid primary key default uuid_generate_v4(),
  actor_id    uuid not null references users(id) on delete cascade,
  action_type text not null check (action_type in (
    'role_change', 'report_resolved', 'student_verified', 'student_unverified'
  )),
  target_type text,        -- 'user', 'report', etc.
  target_id   uuid,
  detail      jsonb,        -- e.g. {"from_role": "student", "to_role": "moderator"}
  created_at  timestamptz not null default now()
);

create index if not exists idx_admin_actions_created_at on admin_actions(created_at desc);
create index if not exists idx_admin_actions_actor on admin_actions(actor_id);

alter table admin_actions enable row level security;

create policy admin_actions_select_staff on admin_actions for select using (is_staff());
create policy admin_actions_insert_own on admin_actions for insert
  with check (actor_id = auth.uid() and is_staff());
-- Deliberately no update/delete policy — an audit log that can be
-- edited after the fact isn't an audit log.
