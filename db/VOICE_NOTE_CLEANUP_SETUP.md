# One-time setup — voice note cleanup (do this once, in the Supabase SQL editor)

This is deliberately NOT part of the migration file. A real service-role
key should never sit in a `.sql` file that lives in your repo, even a
private one — paste it directly into the Supabase SQL editor yourself,
run it once, and it's encrypted at rest in Vault from that point on.

## Steps

1. **Enable Vault**, if it isn't already: Supabase Dashboard → Database →
   Vault. (Most projects have it on by default — if you see a "Secrets"
   list with nothing in it, it's already enabled, skip to step 2.)

2. **Get your service-role key**: Dashboard → Project Settings → API →
   `service_role` `secret` key. This is the same key you'd use for any
   admin-level Supabase operation — treat it like a root password.

3. **Run this in the SQL editor** (Database → SQL Editor → New query),
   replacing the placeholder with your actual key:

   ```sql
   select vault.create_secret(
     'PASTE_YOUR_SERVICE_ROLE_KEY_HERE',
     'voice_notes_service_role_key',
     'Used only by cleanup_expired_voice_notes() to delete expired voice note files from Storage.'
   );
   ```

4. **Edit `voice_note_lifecycle_scale_migration.sql` before running it** —
   replace `https://YOUR-PROJECT-REF.supabase.co` near the top of
   `cleanup_expired_voice_notes()` with your actual project URL (the
   same value as your `SUPABASE_URL` env var on Render). This part
   isn't secret, just needs to be correct.

5. **Run `voice_note_lifecycle_scale_migration.sql`** (the index, log
   table, function, and cron schedule).

6. **Verify it actually works** before trusting it unattended:

   ```sql
   select cleanup_expired_voice_notes();
   select * from voice_note_cleanup_log order by run_at desc limit 5;
   ```

   You should see one row with `queued_count` >= 0 and `error` null.
   If `error` says the secret wasn't found, re-check step 3 — the
   `name` argument must be exactly `voice_notes_service_role_key`,
   that's what the function looks up.

7. **Confirm the schedule is live**:

   ```sql
   select jobname, schedule, active from cron.job where jobname = 'cleanup-expired-voice-notes';
   ```

## If you ever need to rotate the key

Service-role keys can be rotated from Project Settings → API. If you
do, just re-run step 3 with the new value — `vault.create_secret` on
the same `name` updates it in place, no need to touch the function.

## Ongoing health check

Once a week or so (or whenever you're already in the Supabase
dashboard for something else):

```sql
select * from voice_note_cleanup_log order by run_at desc limit 20;
```

If `queued_count` is consistently 1500 (the batch cap) for many runs
in a row, the backlog is growing faster than cleanup can drain it —
that's the signal to raise `batch_size` in the function, not a sign
something's broken.
