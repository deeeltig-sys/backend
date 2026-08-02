# CampusMEET — Groups, Events, Highlights, Polls, Insights (BACKEND)

This is the backend half of the 5-feature sweep. Frontend is being built
now and will follow as a second zip. Everything here has been verified
with a real Flask `create_app()` import test — all ~20 new routes
confirmed registering and resolving correctly (including a routing-order
check to make sure `/api/groups/mine` doesn't get swallowed by
`/api/groups/<group_id>`).

## Apply in this order

### 1. Database — run these four in Supabase SQL editor, in this order:

1. `db/groups_migration.sql`
2. `db/events_migration.sql`
3. `db/highlights_migration.sql`
4. `db/polls_migration.sql`

All additive — no existing table's behavior changes. One important
detail: **`groups_migration.sql` redefines the `feed` view** to add
`group_id` to it. Your `feed` view lists explicit columns rather than
`posts.*` (same as when `repost_of` was added), so without this a group
post would silently never show up anywhere. This is intentional and
necessary — not a stray change.

### 2. Backend — overwrite these files at the matching path in your project:

- `routes/groups.py` → **NEW** — create/discover/join/leave groups, member list, group-scoped feed
- `routes/events.py` → **NEW** — create events, upcoming list, RSVP (interested/going), attendees
- `routes/highlights.py` → **NEW** — create highlight collections, add a status into one, view, delete
- `routes/posts.py` → **OVERWRITE** — adds poll voting endpoints, poll creation, optional `group_id` on posts, and fixes `GET /api/posts/<id>` to go through the `feed` view with full enrichment (it previously read the raw table with no author info, reactions, or repost data attached — a pre-existing gap this sweep needed fixed since a poll now needs to render correctly there too)
- `routes/stats.py` → **OVERWRITE** — adds `GET /api/stats/insights`, a private performance dashboard for your own posts
- `routes/hashtags.py` → **OVERWRITE** — trivial addition, now also attaches poll data to hashtag-feed results for consistency
- `app.py` → **OVERWRITE** — registers the three new blueprints

## What each feature does, mechanically

**Groups** — `groups` + `group_members` tables. Creating a group makes
you its first admin automatically (trigger, not a second client call
that could be skipped). Public groups: self-join. Private groups: only
an existing admin can add someone (no invite-request flow yet — noted
below). A post can now optionally carry a `group_id`; posting into a
group is membership-checked server-side, not just hidden in the UI.

**Events** — `events` + `event_rsvps`. Two RSVP states (`interested`,
`going`), each with its own live count via triggers, including switching
between them. Can optionally hang off a group.

**Highlights** — `status_highlights` + `status_highlight_items`. Adding
a status to a highlight **copies** its content rather than referencing
the original row — your Status/Story feature already lets rows expire
and eventually get purged (see `status_and_settings_migration.sql`), so
a highlight has to be an independent, permanent copy to actually survive
that.

**Polls** — `poll_options` + `poll_votes`. A poll is just a normal post
(the question is `posts.content`, same as always) with 2-4 attached
options. One vote per person, switchable — trigger tracks both the
initial vote and a later change. Individual ballots are private
(`poll_votes` RLS: select own only); running totals on `poll_options`
are public. Wired into every place a post can appear — main feed,
search, profile grid, saved posts, hashtag feeds, single post view.

**Insights** — no new tables. `GET /api/stats/insights` sums up
`view_count`/`reaction_count`/`comment_count` (all already existing
columns) across your own active posts and ranks your top 5. Private to
the caller only — there's no way to hit this for anyone but yourself.

## Known scope trims (deliberate, not oversights)

- **Private groups have no invite/request flow yet** — an admin can add
  someone via the members table directly, but there's no UI/API for "request
  to join" or "here's an invite link." Worth a follow-up round.
- **No feed-mixing** — group posts only show inside that group's own
  page, not blended into your main feed even for members. Simpler and
  safer to ship first; FB does eventually blend these but it's a
  ranking-complexity question worth deciding deliberately later.

## Frontend

Coming next as a second zip — pages, components, nav entry points, and
`api/client.js` additions for all five features.
