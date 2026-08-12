# AI Assistant Rules — CampusMEET

## Mission

CampusMEET is not a class project or a portfolio piece. It is being
built to become **the official social media platform of Africa** —
the platform African students and, eventually, all of Africa use the
way the rest of the world uses Facebook or Instagram, built by and
for the continent instead of adapted for it after the fact.

Every rule below exists in service of that. Reliability, scope
discipline, and not reintroducing old bugs are not bureaucracy — they
are what separates a platform that can actually carry millions of
people for years from one that quietly collapses under its own
technical debt the first time real usage arrives. A feature that
works in a demo but breaks under real load, or a fix that solves
today's bug while quietly planting tomorrow's, is not progress toward
that goal — it's a withdrawal against it. Build every change as if
the platform has to survive contact with the audience it's actually
meant for.

These rules apply to every AI coding assistant working in this repository
(Claude Code, Claude in chat, or any other tool). They are not suggestions.
A change that violates these rules is a failed change, even if the
requested feature technically works.

## 1. Scope discipline

- Touch only the files and lines required to satisfy the specific request.
  If a task can be done by editing one function, do not refactor the file
  around it "while you're in there."
- Do not rename variables, reformat untouched code, reorder imports, or
  change indentation/style in files or sections you were not asked to
  change — even if you'd write it differently.
- If finishing the task properly requires touching something outside the
  stated scope (e.g., a shared component used in 10 places), stop and say
  so before editing. Do not silently expand scope.
- "While I'm here" is not a justification for any edit. Every changed line
  must trace back to the actual request.

## 2. Never break what already works

- Before editing a file, read enough of it (and its call sites, if it's a
  shared component/util/API route) to understand what currently depends
  on it. Do not guess at behavior.
- Prefer additive changes (new class, new prop, new function) over
  modifying existing logic in place, when both achieve the goal.
- If a change could affect more than one page/component (shared CSS
  classes, shared utils, shared API contracts), explicitly list every
  place it will touch before making the change, and confirm that list is
  complete — don't discover a fourth caller after the fact.
- Never delete or rewrite a working migration, RLS policy, or DB function
  to "clean it up" unless that is the explicit task. This project already
  has a history of needing follow-up `*_fix.sql` migrations — every one of
  those was an unnecessary bug. Don't add another.
- If you are not certain a change is safe, say what you're unsure about
  instead of proceeding on a guess.

## 3. Fixing one thing must not mean breaking another

- When fixing bug A, do not "improve" adjacent code B, C, or D in the same
  pass, even if you notice something that looks wrong. Flag it separately
  instead of fixing it inline.
- After a fix, mentally (or actually) trace every other place that calls
  the changed function/component/endpoint. A fix that works for the
  reported case but silently changes behavior for other callers is not a
  fix — it's a new bug.
- Test/verify the specific broken behavior is now correct. Do not assume
  a fix worked because the code "looks right."

## 4. When in doubt, ask or report — don't improvise

- If a request is ambiguous about how far a change should reach (e.g.,
  "make the names gold" — one page, one component, or app-wide?), state
  the assumed scope explicitly and give the option to widen or narrow it,
  rather than guessing silently in either direction.
- If you find something broken that wasn't part of the current task, name
  it in your response — do not fix it without being asked, and do not
  silently ignore it either.

## 5. Every response involving a code change must state

- Exactly which files were changed and why, in one line each.
- What was deliberately *not* touched, if it was adjacent to the change
  and someone might reasonably wonder why it wasn't included.
- Any place the same pattern exists but wasn't updated (so nothing is
  silently left inconsistent).

## 6. Known bug patterns — do not reintroduce these

Every item below was a real bug shipped at some point in this project,
found by actually running the code (not just reading it), and fixed.
Several happened more than once, in different files, months apart —
that repetition is exactly why they're written down here instead of
trusted to memory.

- **Ambiguous PostgREST embed.** If a table has two foreign keys into
  the same target table (e.g. `follows.follower_id` AND
  `follows.followed_id` both → `users`), an embed like
  `follower:users(...)` with no constraint name is ambiguous and
  fails silently from the caller's point of view (PostgREST errors,
  the route returns a non-200, the frontend just shows "could not
  load"). Always write `alias:table!constraint_name(...)` the moment
  a table has more than one FK to the same target. This broke
  followers/following once already.

- **`notifications.user_id`, never `recipient_id`.** The
  `notifications` table has never had a `recipient_id` column — only
  `user_id`. This exact wrong assumption caused two separate real
  bugs (the mentions trigger, and the original Chats-badge unread
  count), independently, months apart. Grep the actual table
  definition before writing an insert/query against it — don't
  pattern-match off a similarly-named table (`messages` genuinely
  doesn't have a recipient column at all; the recipient of a DM is
  "whoever didn't send it").

- **Postgres never treats two NULLs as equal** — not in a `UNIQUE`
  constraint, not in `ON CONFLICT` matching. A nullable column used as
  part of a uniqueness guarantee provides no real protection when its
  value is NULL; duplicate rows insert silently forever. Use a
  non-null sentinel value (e.g. `'once'`) instead of NULL whenever a
  column is part of what makes a row unique. This caused the badge
  system to duplicate "earn-once" badges on every cron run before it
  was caught.

- **`round(double precision, integer)` does not exist in Postgres** —
  only `round(numeric, integer)` does. `percent_rank()` and similar
  window functions return `double precision`; cast to `::numeric`
  before rounding with a precision argument, or the function throws
  the moment it actually runs (CREATE FUNCTION never validates this,
  so it looks fine until the exact moment someone hits the code path).

- **A new enum value (notification type, etc.) needs every consumer
  updated, not just the database.** `comment_reply`, `friend_request`,
  `friend_accept`, and `mention` were each added to
  `notification_type` at some point without the frontend's
  text/tap-through map being updated to match — the notification
  would render as generic "New activity" and do nothing when tapped.
  Whenever a new value is added to any enum/type the frontend
  branches on, grep every place that already switches on the existing
  values and add the new one to all of them in the same change.

- **A new route file needs `app.py` registration; a new page needs an
  `App.jsx` route entry.** A perfectly correct blueprint that's never
  passed to `app.register_blueprint()` 404s everything in it. A
  perfectly built page component that's never added to the router is
  unreachable. Confirm both explicitly, don't assume writing the file
  was the whole task.

- **A component that's imported nowhere is not a feature, it's dead
  code.** `RepostModal.jsx` existed, fully built and correct, for a
  long time before anything actually rendered it — reposting was
  completely unreachable the whole time. Confirm a new component
  appears in an actual render tree, not just that the file compiles
  and the import path resolves.

- **`.app-shell { min-height: 100vh }` vs `height: 100vh`.** `min-height`
  lets the whole page grow past the viewport and scroll as one long
  page instead of confining scroll to `.screen` the way every other
  layout assumption in this codebase depends on. Every normal content
  page looks fine either way — only a screen built as a fixed-header/
  scrolling-middle/fixed-footer layout (like the chat thread) actually
  breaks, and it breaks by silently pushing its input box off-screen,
  not by throwing an error.

- **This repo has CRLF line endings in most frontend files.** A plain
  text-based find/replace against a CRLF file can produce a
  mixed-line-ending file (or fail to match at all) without any error
  raised. Check line-ending style before editing a file directly, and
  preserve it — don't silently convert a file to LF as a side effect
  of an unrelated change.

- **`notifications.user_id`, never `recipient_id` — THIRD occurrence,
  same mistake.** Beyond the mentions trigger and the original Chats
  badge (already noted above), `send_push_for_notification()` had this
  exact bug too — `where user_id = new.recipient_id` inside a trigger
  that fires on every single notification insert, for every type. One
  wrong column reference there rolled back the ENTIRE transaction it
  fired inside, which is what made a completely unrelated feature
  (reactions) look broken for weeks — the reaction insert itself was
  fine; it got rolled back by a trigger three tables away. Given this
  is now a proven 3x-recurring mistake, grep every function in
  `pg_proc` for `recipient_id` (`select proname, prosrc from pg_proc
  where prosrc ilike '%recipient_id%'`) before trusting that it's
  fixed everywhere — don't assume fixing the one you found is fixing
  all of them.

- **PostgREST upsert (`Prefer: resolution=merge-duplicates`) needs an
  explicit `on_conflict` query param whenever a table has more than
  one unique-able target** (a primary key plus a separate composite
  `unique(...)`). Without it, PostgREST can't resolve which constraint
  to treat as the conflict target and rejects the request outright —
  not only on an actual duplicate, on the very first insert too. This
  silently broke reactions from the first tap, not just when switching
  reaction types.

- **A guard trigger that reverts "protected" columns needs every
  legitimate writer to explicitly opt out, not just the guard itself
  updated once.** `guard_post_moderation_fields()` was correctly given
  a `campmeet.system_update` bypass flag, but only `bump_comment_count()`
  was updated to actually set that flag — `bump_reaction_count()` and
  `bump_report_count()` were missed, so they kept getting silently
  reverted for months after the "fix" shipped. When adding a bypass
  flag to a guard trigger, grep every function that writes to the
  guarded columns and confirm each one sets the flag — a partial fix
  looks identical to a complete one until someone actually tests the
  specific column that was missed.

- **Never assume Postgres's default auto-generated constraint names**
  (`table_column_check`, `table_check`) when altering CHECK
  constraints — a wrong guess means `drop constraint if exists
  <guessed name>` silently no-ops, the old constraint stays active
  alongside the new one, and both get enforced together (ANDed), so
  the change appears to do nothing. Introspect the real names first:
  `select conname from pg_constraint where conrelid = 'table'::regclass
  and contype = 'c'`.

- **A trigger on table A can cascade through B into C, and the error
  you see may come from a table you never directly touched.** When
  debugging a mysterious error inside one transaction, list every
  trigger on every table actually written to — directly or via
  cascade — not just the table the original request targeted:
  `select tgrelid::regclass, tgname, pg_get_triggerdef(oid) from
  pg_trigger where tgrelid in (...) and not tgisinternal`. The
  `send_push_for_notification` bug above was found exactly this way,
  three cascade-hops from where the actual symptom appeared.

- **Reading code is not verification.** Every bug above was confirmed
  by actually executing the SQL/route/component against real or
  realistic data, not by reading the change and judging it correct.
  Prefer running a migration against a throwaway database, executing
  a route handler with fake request data, or building the frontend
  for real over asserting a change is safe from inspection alone —
  several of the bugs above were syntactically valid, imported
  correctly, and "looked right" right up until they ran.


This platform is being built to become the official social media platform of
Africa — purpose-built for the continent, not adapted for it after the fact.
Everything we think first of be of how it will benefit Africa in terms of social and online connectivity
This file exists so that goal survives every individual coding session: so
a fix made today doesn't quietly introduce the mistake someone finds next
month, and so the platform is always being built forward on top of what
already exists, not around it or over it.

Never repeat a known mistake. Always build forward on what already works.

Thank you for helping me build this — my CO-DEVELOPER.