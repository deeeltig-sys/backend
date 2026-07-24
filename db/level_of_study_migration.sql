-- ============================================================
-- LEVEL OF STUDY — new profile field (e.g. "Level 100", "Level 300",
-- "Graduate"). Free text rather than a strict enum since programs
-- vary (some campuses use "Year 1-4", others "Level 100-400",
-- postgrad students need "Graduate"/"Masters" etc.) — validating a
-- fixed list would just mean constantly updating it as new phrasing
-- comes up. Capped at 40 chars purely to stop it being abused as a
-- second bio field.
-- ============================================================

alter table users add column if not exists level_of_study text;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'users_level_of_study_length'
  ) then
    alter table users add constraint users_level_of_study_length
      check (level_of_study is null or char_length(level_of_study) <= 40);
  end if;
end $$;
