-- Run these one at a time, not as a batch (per your own SQL Editor rule).

-- 1. Enable the extension (or do it via Database -> Extensions -> pg_cron in the UI)
create extension if not exists pg_cron;

-- 2. Schedule the badge evaluation job
select cron.schedule('evaluate-badges', '0 5 * * *', 'select evaluate_badges()');

-- 3. Backfill immediately so today's users don't wait for 5am
select evaluate_badges();

-- 4. Confirm it's actually scheduled now
select jobname, schedule, active from cron.job;
