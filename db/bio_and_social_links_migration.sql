-- ============================================================
-- FIX + ADD — social_links was never actually created as a column.
-- routes/profile.py, routes/auth.py, and models/user.py have all
-- been reading/writing `social_links` since the social-hub feature
-- was built, but no migration ever added it to `users`, so every
-- save has been failing against Postgres (column does not exist).
-- This migration also adds the `bio` column requested for profile
-- pages.
--
-- Safe to run more than once — `add column if not exists`.
-- ============================================================

alter table users add column if not exists social_links jsonb not null default '{}'::jsonb;
alter table users add column if not exists bio text;

-- Matches MAX_BIO_LENGTH in models/user.py — keeps bad data out even
-- if something bypasses the app layer (e.g. a manual SQL edit).
-- Postgres has no `add constraint if not exists`, so this is wrapped
-- to stay safe on a second run.
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'users_bio_length'
  ) then
    alter table users add constraint users_bio_length
      check (bio is null or char_length(bio) <= 280);
  end if;
end $$;
