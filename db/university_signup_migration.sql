-- ============================================================
-- SIGNUP MIGRATION — student_id_number is no longer required or
-- format-checked; university becomes the required field instead.
-- Safe to run more than once.
-- ============================================================

-- Drop whatever check constraint currently enforces the '52########'
-- format — found dynamically rather than by a guessed name, so this
-- doesn't break if the constraint was ever renamed.
do $$
declare
  con record;
begin
  for con in
    select conname from pg_constraint
    where conrelid = 'users'::regclass
      and contype = 'c'
      and pg_get_constraintdef(oid) ilike '%student_id_number%'
  loop
    execute format('alter table users drop constraint %I', con.conname);
  end loop;
end $$;

alter table users alter column student_id_number drop not null;

-- Resolves a university by name — case-insensitive match against an
-- existing row, or creates one on the fly if it's genuinely new. This
-- is what lets someone type their own school at signup instead of
-- being limited to whatever's already been manually seeded.
create or replace function get_or_create_university(p_name text)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_id uuid;
  v_clean text := trim(p_name);
begin
  if v_clean = '' then
    raise exception 'university name cannot be empty';
  end if;

  select id into v_id from universities where lower(name) = lower(v_clean);
  if v_id is not null then
    return v_id;
  end if;

  insert into universities (name, short_code)
  values (
    v_clean,
    upper(substr(regexp_replace(v_clean, '[^a-zA-Z0-9]+', '', 'g'), 1, 12)) || '-' || substr(md5(random()::text), 1, 6)
  )
  returning id into v_id;

  return v_id;
end;
$$;

-- Signup trigger now resolves university from metadata instead of
-- hardcoding everyone to USTED. Accepts either a real university_id
-- (picked from the dropdown) or a university_name (the "Other" free
-- text path), and no longer requires or validates student_id_number.
create or replace function handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  resolved_university_id uuid;
  raw_university_id text;
  raw_university_name text;
begin
  raw_university_id := new.raw_user_meta_data ->> 'university_id';
  raw_university_name := new.raw_user_meta_data ->> 'university_name';

  if raw_university_id is not null and raw_university_id <> '' then
    resolved_university_id := raw_university_id::uuid;
  elsif raw_university_name is not null and trim(raw_university_name) <> '' then
    resolved_university_id := get_or_create_university(raw_university_name);
  else
    raise exception 'university is required';
  end if;

  insert into public.users (id, university_id, full_name, student_id_number, verified_at)
  values (
    new.id,
    resolved_university_id,
    new.raw_user_meta_data ->> 'full_name',
    nullif(new.raw_user_meta_data ->> 'student_id_number', ''),
    null
  );

  return new;
end;
$$;
