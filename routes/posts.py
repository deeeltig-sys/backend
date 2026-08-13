import uuid

from flask import Blueprint, request, jsonify, g
from lib.supabase_client import rest_request, rpc, storage_upload
from lib.decorators import require_auth, optional_auth
from lib.watermark import apply_watermark
from lib.image_processing import normalize_image, UnsupportedImageError
from models.post import validate_post_content
from routes.friends import _friend_ids

bp = Blueprint("posts", __name__, url_prefix="/api/posts")

MAX_IMAGE_BYTES = 6 * 1024 * 1024  # 6MB


def _bearer_token_if_present():
    header = request.headers.get("Authorization", "")
    return header.split(" ", 1)[1] if header.startswith("Bearer ") else None


def _attach_user_reactions(posts, token, user_id):
    """Mutates `posts` in place, adding `user_reaction` (or None) to each
    row. Without this the frontend has no way to know "you already
    reacted to this one" -- the feed/search views themselves can't carry
    that, since it's specific to whoever is asking, not the post itself.
    Silently does nothing if there's no token (anonymous browsing) or no
    posts to annotate.

    Filters on user_id as well as post_id -- reactions_select is
    `using (true)` (see db/rls_policies.sql), open by design so
    ReactorsModal can show everyone's names, not just the caller's own.
    Without the user_id filter here, a post with more than one reactor
    returns every reactor's row for that post, and the old dict-comp
    below picked whichever one landed last -- effectively a stranger's
    reaction on any post that had picked up more than one, which is
    why it only ever looked right on lightly-reacted posts."""
    if not token or not posts or not user_id:
        return
    post_ids = ",".join(p["id"] for p in posts)
    reactions, status = rest_request(
        "GET", "reactions", token=token,
        params={"post_id": f"in.({post_ids})", "user_id": f"eq.{user_id}", "select": "post_id,type"},
    )
    if status != 200 or not isinstance(reactions, list):
        return
    by_post = {r["post_id"]: r["type"] for r in reactions}
    for p in posts:
        p["user_reaction"] = by_post.get(p["id"])


def _blocked_author_ids(token):
    """Posts from anyone the caller has blocked, filtered out of
    whatever feed/search they're looking at. Only reads the caller's
    own block rows (RLS only allows that anyway) — there's no way,
    by design, to discover who has blocked *you*."""
    if not token:
        return set()
    data, status = rest_request(
        "GET", "blocks", token=token,
        params={"select": "blocked_id"},
    )
    if status != 200 or not isinstance(data, list):
        return set()
    return {row["blocked_id"] for row in data}


def _filter_blocked(posts, token):
    blocked = _blocked_author_ids(token)
    if not blocked:
        return posts
    return [p for p in posts if p.get("author_id") not in blocked]


def _filter_by_audience(posts, viewer_id, token):
    """Removes 'friends'-only posts the viewer isn't allowed to see.
    A post's own author always sees it regardless of audience. Same
    app-level filtering model as _filter_blocked above — the `feed`
    view itself stays broad; this narrows what actually gets returned
    to this particular viewer, rather than enforcing it via RLS on
    the view (see db/audience_migration.sql for why)."""
    if not posts:
        return posts
    needs_check = [p for p in posts if p.get("audience") == "friends" and p.get("author_id") != viewer_id]
    if not needs_check:
        return posts
    friend_ids = set(_friend_ids(viewer_id, token)) if viewer_id else set()
    return [
        p for p in posts
        if p.get("audience") != "friends" or p.get("author_id") == viewer_id or p.get("author_id") in friend_ids
    ]


@bp.post("/upload-image")
@require_auth
def upload_image():
    """Powers image posts. The file goes straight to the `post-images`
    Supabase Storage bucket under the caller's own user id as a path
    prefix, using the caller's own JWT — same no-service-role rule as
    every other write in this backend. Storage RLS (db/storage_policies.sql)
    is what actually enforces that a student can only upload into their
    own folder; this route just shapes the path and forwards the bytes."""
    if "image" not in request.files:
        return jsonify({"error": "attach an image file under the 'image' field"}), 400

    file = request.files["image"]
    file_bytes = file.read()
    if len(file_bytes) > MAX_IMAGE_BYTES:
        return jsonify({"error": "image must be under 6MB"}), 400

    try:
        file_bytes, content_type, extension = normalize_image(file_bytes)
    except UnsupportedImageError as exc:
        return jsonify({"error": str(exc)}), 400

    # Stamp before upload — every post image gets the mark, not just an
    # opt-in, so it's consistent across the whole platform. Uses the
    # original bytes as a fallback if watermarking hits any error.
    file_bytes = apply_watermark(file_bytes, content_type)

    path = f"{g.user_id}/{uuid.uuid4().hex}.{extension}"

    data, status = storage_upload("post-images", path, file_bytes, content_type, g.token)
    if status >= 400:
        return jsonify({"error": "image upload failed, try again"}), status

    return jsonify({"url": data["url"]}), 201


def _attach_original_posts(posts, token):
    """For any post with repost_of set, fetch the original (through the
    `feed` view so author info/score come along for free) and attach
    it as `original_post`. A repost of something since deleted or
    unreactive gets `original_post: None` — the frontend shows "this
    post is no longer available" rather than silently rendering
    nothing, so a repost never just looks broken with no explanation."""
    if not posts:
        return
    repost_ids = list({p["repost_of"] for p in posts if p.get("repost_of")})
    if not repost_ids:
        return
    originals, status = rest_request(
        "GET", "feed", token=token, params={"id": f"in.({','.join(repost_ids)})", "select": "*"},
    )
    by_id = {o["id"]: o for o in (originals or [])} if status == 200 else {}
    for p in posts:
        if p.get("repost_of"):
            p["original_post"] = by_id.get(p["repost_of"])


def _attach_polls(posts, token):
    """Mutates `posts` in place, adding a `poll` key to any post that
    has options attached (`{options: [...], user_vote: option_id|None}`).
    Posts with no poll_options rows are left untouched — the frontend
    treats "no poll key" as "not a poll", same as how `original_post`
    only shows up on actual reposts."""
    if not posts:
        return
    post_ids = [p["id"] for p in posts]
    options, status = rest_request(
        "GET", "poll_options", token=token,
        params={
            "post_id": f"in.({','.join(post_ids)})",
            "select": "id,post_id,option_text,order_index,vote_count",
            "order": "order_index.asc",
        },
    )
    if status != 200 or not options:
        return

    by_post = {}
    for opt in options:
        by_post.setdefault(opt["post_id"], []).append(opt)

    user_vote_by_post = {}
    if token:
        votes, vstatus = rest_request(
            "GET", "poll_votes", token=token,
            params={"post_id": f"in.({','.join(by_post.keys())})", "select": "post_id,option_id"},
        )
        if vstatus == 200 and isinstance(votes, list):
            user_vote_by_post = {v["post_id"]: v["option_id"] for v in votes}

    total_by_post = {pid: sum(o["vote_count"] for o in opts) for pid, opts in by_post.items()}

    for p in posts:
        opts = by_post.get(p["id"])
        if opts:
            p["poll"] = {
                "options": opts,
                "total_votes": total_by_post.get(p["id"], 0),
                "user_vote": user_vote_by_post.get(p["id"]),
            }


def _attach_mentions(posts, token):
    """Mutates `posts` in place, adding a `mentions` key
    (`[{id, full_name}, ...]`) to any post that tags other users.
    Same "absent key means none" contract as `poll`/`original_post`."""
    if not posts:
        return
    post_ids = [p["id"] for p in posts]
    rows, status = rest_request(
        "GET", "post_mentions", token=token,
        params={
            "post_id": f"in.({','.join(post_ids)})",
            "select": "post_id,mentioned_user_id,user:users(id,full_name)",
        },
    )
    if status != 200 or not rows:
        return

    by_post = {}
    for row in rows:
        u = row.get("user") or {}
        if u.get("id"):
            by_post.setdefault(row["post_id"], []).append({"id": u["id"], "full_name": u.get("full_name")})

    for p in posts:
        mentions = by_post.get(p["id"])
        if mentions:
            p["mentions"] = mentions


def _attach_images(posts, token):
    """Mutates `posts` in place, adding an `images` key
    (`[{url, order_index}, ...]`) to any post created with more than
    one photo. `posts.image_url` itself is untouched and still holds
    the first image — this is purely additive so anything reading
    `image_url` directly (older code, other integrations) keeps
    working without ever knowing this table exists."""
    if not posts:
        return
    post_ids = [p["id"] for p in posts]
    rows, status = rest_request(
        "GET", "post_images", token=token,
        params={
            "post_id": f"in.({','.join(post_ids)})",
            "select": "post_id,image_url,order_index",
            "order": "order_index.asc",
        },
    )
    if status != 200 or not rows:
        return

    by_post = {}
    for row in rows:
        by_post.setdefault(row["post_id"], []).append({"url": row["image_url"], "order_index": row["order_index"]})

    for p in posts:
        images = by_post.get(p["id"])
        if images:
            p["images"] = images


@bp.get("/feed")
@optional_auth
def feed():
    """Reads from `feed_seeded_for_viewer()` — same weighted-random
    sampling as before (see db/feed_randomization_migration.sql +
    FIX_feed_score_and_view.sql for feed_score's recency decay), now
    with two additions layered on top (db/feed_affinity_migration.sql):

    1. Affinity multiplier — your own posts, then mutual friends, then
       one-way follows, get their score multiplied up before the
       weighted-random draw, so they're heavily favored to surface
       near the top without turning this into a strict follows-only
       feed. Campus-wide discovery stays mixed in on purpose — with a
       still-growing user base, a follows-only feed would look dead
       for most students. This is the deliberate middle ground
       between "pure FB/IG follow graph" and "pure TikTok For You".
    2. A real seed, finally passed through — this route previously
       queried the `feed` view directly and never called the seeded
       function that already existed for exactly this, so every
       request (including paginated ones) got its own independent
       random draw. That's what made posts appear to jump/duplicate/
       vanish on scroll or after any incidental re-fetch. One seed is
       now generated per feed "session" here and handed back in the
       response; the frontend already reuses it across pagination
       (see feedSeedRef in Feed.jsx) — that plumbing was sitting there
       unused because the backend never sent a seed to reuse.

    `scope` param: 'campus' (default) shows only posts from the
    caller's own university — this is the actual multi-university
    architecture decision, not just cosmetic: without it, a new
    university's students see themselves drowned out in a single
    global feed dominated by whichever campus onboarded first. 'campus'
    keeps each university feeling alive on its own from day one.
    'national' shows everyone, unscoped — an explicit opt-in via the
    toggle in Feed.jsx. Unauthenticated requests can't be scoped to a
    campus we don't know, so they always get 'national'."""
    limit = request.args.get("limit", 30)
    offset = request.args.get("offset", 0)
    scope = request.args.get("scope", "campus")
    # A fresh seed if the frontend didn't send one back (first load of
    # a session) — reused as-is on every subsequent paginated request
    # for that same session so page 2 doesn't reshuffle page 1.
    seed = request.args.get("seed") or uuid.uuid4().hex

    params = {"select": "*", "limit": limit, "offset": offset}

    if scope == "campus" and g.user_id:
        me, me_status = rest_request(
            "GET", "users", token=g.token,
            params={"select": "university_id", "id": f"eq.{g.user_id}"},
        )
        university_id = (me or [{}])[0].get("university_id") if me_status == 200 else None
        if university_id:
            # An Okyeame announcement has to reach every campus, not
            # just whichever university that account happens to be
            # registered under — so campus-scoping is "my university,
            # OR it's an official post" rather than a plain equality
            # filter. See db/okyeame_migration.sql for author_is_official.
            params["or"] = f"(university_id.eq.{university_id},author_is_official.eq.true)"

    data, status = rpc(
        "feed_seeded_for_viewer",
        token=g.token,
        payload={"p_seed": seed, "p_viewer_id": g.user_id},
        params=params,
    )
    if status != 200:
        return jsonify({"error": "could not load feed"}), status

    data = _filter_blocked(data, g.token)
    data = _filter_by_audience(data, g.user_id, g.token)
    _attach_user_reactions(data, g.token, g.user_id)
    _attach_original_posts(data, g.token)
    _attach_polls(data, g.token)
    _attach_mentions(data, g.token)
    _attach_images(data, g.token)
    return jsonify({"posts": data, "seed": seed}), 200


@bp.get("/explore")
@optional_auth
def explore():
    """Discovery feed — the Explore/Discover role IG's search tab plays:
    content from OUTSIDE the caller's own network, ranked by raw
    engagement rather than personal affinity (we don't know this
    audience yet), and diversified round-robin by author so the grid
    doesn't read as one person's or one campus's feed on repeat."""
    limit = min(int(request.args.get("limit", 30)), 60)

    data, status = rest_request(
        "GET", "feed",
        params={
            "select": "*",
            "order": "reaction_count.desc,view_count.desc,created_at.desc",
            "limit": limit * 3,  # over-fetch so the diversity pass has room to work with
        },
    )
    if status != 200:
        return jsonify({"error": "could not load explore"}), status

    posts = _filter_blocked(data or [], g.token)
    posts = _filter_by_audience(posts, g.user_id, g.token)

    if g.user_id:
        following, fstatus = rest_request(
            "GET", "follows", token=g.token,
            params={"follower_id": f"eq.{g.user_id}", "select": "following_id"},
        )
        friends, frstatus = rest_request(
            "GET", "friendships", token=g.token,
            params={"or": f"(user_a.eq.{g.user_id},user_b.eq.{g.user_id})", "select": "user_a,user_b"},
        )
        known_ids = {g.user_id}
        if fstatus == 200:
            known_ids.update(r["following_id"] for r in (following or []))
        if frstatus == 200:
            for r in (friends or []):
                known_ids.add(r.get("user_a"))
                known_ids.add(r.get("user_b"))
        posts = [p for p in posts if p.get("author_id") not in known_ids]

    # Round-robin by author so no single voice dominates the grid.
    buckets = {}
    order_seen = []
    for p in posts:
        aid = p.get("author_id")
        if aid not in buckets:
            buckets[aid] = []
            order_seen.append(aid)
        buckets[aid].append(p)

    diversified = []
    while len(diversified) < limit and order_seen:
        for aid in list(order_seen):
            bucket = buckets[aid]
            if bucket:
                diversified.append(bucket.pop(0))
            if not bucket:
                order_seen.remove(aid)
            if len(diversified) >= limit:
                break

    _attach_user_reactions(diversified, g.token, g.user_id)
    _attach_original_posts(diversified, g.token)
    _attach_polls(diversified, g.token)
    _attach_mentions(diversified, g.token)
    _attach_images(diversified, g.token)
    return jsonify(diversified), 200


@bp.get("/search")
@optional_auth
def search_posts():
    """Simple ILIKE search over post content, scoped to the same
    active-only `feed` view so results carry author info and
    reaction/view counts identically to the main feed. Not a
    full-text-search ranking yet -- fine at current volume, backed
    by a trigram index (db/avatar_and_search_migration.sql) so it
    stays fast as content grows."""
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify([]), 200
    if len(query) < 2:
        return jsonify({"error": "type at least 2 characters to search"}), 400

    limit = request.args.get("limit", 30)
    data, status = rest_request(
        "GET", "feed",
        params={"select": "*", "content": f"ilike.*{query}*", "limit": limit},
    )
    if status != 200:
        return jsonify({"error": "search failed"}), status

    data = _filter_blocked(data, g.token)
    data = _filter_by_audience(data, g.user_id, g.token)
    _attach_user_reactions(data, g.token, g.user_id)
    _attach_original_posts(data, g.token)
    _attach_polls(data, g.token)
    _attach_mentions(data, g.token)
    _attach_images(data, g.token)
    return jsonify(data), 200


MAX_CAROUSEL_IMAGES = 5
MAX_MENTIONS_PER_POST = 20


@bp.post("")
@require_auth
def create_post():
    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    image_url = body.get("image_url")
    image_urls = body.get("image_urls")  # optional carousel — list of already-uploaded URLs
    repost_of = body.get("repost_of")
    group_id = body.get("group_id")
    poll_options = body.get("poll_options")
    mentioned_user_ids = body.get("mentioned_user_ids")
    audience = body.get("audience", "public")

    if audience not in ("public", "friends"):
        return jsonify({"error": "audience must be 'public' or 'friends'"}), 400

    if image_urls is not None:
        if not isinstance(image_urls, list) or not image_urls:
            return jsonify({"error": "image_urls must be a non-empty list"}), 400
        if len(image_urls) > MAX_CAROUSEL_IMAGES:
            return jsonify({"error": f"a post can carry at most {MAX_CAROUSEL_IMAGES} photos"}), 400
        # image_url stays the first photo, purely for backward
        # compatibility with anything reading that single field
        # directly — see db/post_images_migration.sql.
        image_url = image_urls[0]

    if repost_of:
        original, ostatus = rest_request(
            "GET", "posts", token=g.token,
            params={"id": f"eq.{repost_of}", "status": "eq.active", "select": "id"},
        )
        if ostatus != 200 or not original:
            return jsonify({"error": "the post you're reposting is no longer available"}), 404
        # A pure repost carries no content/image of its own — the
        # original's is what displays. Commentary (a "quote repost")
        # is still optional on top of that.
        if content and not validate_post_content(content):
            return jsonify({"error": "your added comment must be under 2000 characters"}), 400
    else:
        if not content and not image_url:
            return jsonify({"error": "write something or attach a photo before posting"}), 400
        if content and not validate_post_content(content):
            return jsonify({"error": "post must be 1-2000 characters"}), 400

    if poll_options is not None:
        if repost_of or image_url:
            return jsonify({"error": "a poll can't also be a repost or carry an image"}), 400
        if not isinstance(poll_options, list) or not (2 <= len(poll_options) <= 4):
            return jsonify({"error": "a poll needs 2 to 4 options"}), 400
        cleaned_options = [(o or "").strip() for o in poll_options]
        if any(not (1 <= len(o) <= 80) for o in cleaned_options):
            return jsonify({"error": "each poll option must be 1-80 characters"}), 400
        if not content:
            return jsonify({"error": "write a question for your poll"}), 400

    cleaned_mention_ids = []
    if mentioned_user_ids is not None:
        if not isinstance(mentioned_user_ids, list):
            return jsonify({"error": "mentioned_user_ids must be a list"}), 400
        # De-dup and cap, then verify each id is real before ever
        # attempting the insert — silently drop bad ones rather than
        # fail the whole post over a stale/mistyped id.
        candidate_ids = list(dict.fromkeys(mentioned_user_ids))[:MAX_MENTIONS_PER_POST]
        if candidate_ids:
            existing, estatus = rest_request(
                "GET", "users", token=g.token,
                params={"id": f"in.({','.join(candidate_ids)})", "select": "id"},
            )
            if estatus == 200 and existing:
                cleaned_mention_ids = [u["id"] for u in existing]

    if group_id:
        membership, mstatus = rest_request(
            "GET", "group_members", token=g.token,
            params={"group_id": f"eq.{group_id}", "user_id": f"eq.{g.user_id}", "select": "user_id"},
        )
        if mstatus != 200 or not membership:
            return jsonify({"error": "join the group before posting in it"}), 403

    profile, pstatus = rest_request(
        "GET", "users", token=g.token,
        params={"id": f"eq.{g.user_id}", "select": "university_id"},
    )
    if pstatus != 200 or not profile:
        return jsonify({"error": "could not resolve university"}), 400

    payload = {
        "author_id": g.user_id,
        "university_id": profile[0]["university_id"],
        "content": content or None,
        "image_url": image_url,
        "repost_of": repost_of,
        "group_id": group_id,
        "audience": audience,
    }
    data, status = rest_request(
        "POST", "posts", token=g.token, json_body=payload, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not create post"}), status
    created = data[0] if isinstance(data, list) else data

    if poll_options is not None:
        options_payload = [
            {"post_id": created["id"], "option_text": text, "order_index": i}
            for i, text in enumerate(cleaned_options)
        ]
        opt_data, opt_status = rest_request(
            "POST", "poll_options", token=g.token, json_body=options_payload, prefer="return=representation",
        )
        if opt_status >= 400:
            # Post already exists at this point; the poll attachment failed
            # but the text post itself is fine — surface it as a poll-
            # specific error rather than pretending the whole post failed.
            return jsonify({"error": "post created, but poll options could not be saved", "post": created}), 207
        created["poll"] = {"options": opt_data, "total_votes": 0, "user_vote": None}

    if image_urls is not None:
        images_payload = [
            {"post_id": created["id"], "image_url": url, "order_index": i}
            for i, url in enumerate(image_urls)
        ]
        img_data, img_status = rest_request(
            "POST", "post_images", token=g.token, json_body=images_payload, prefer="return=representation",
        )
        if img_status < 400:
            created["images"] = [{"url": r["image_url"], "order_index": r["order_index"]} for r in img_data]

    if cleaned_mention_ids:
        mentions_payload = [{"post_id": created["id"], "mentioned_user_id": uid} for uid in cleaned_mention_ids]
        rest_request(
            "POST", "post_mentions", token=g.token, json_body=mentions_payload,
            prefer="resolution=ignore-duplicates",
        )
        # Names for the immediate response — cheap since we already
        # fetched these exact user rows above during validation.
        created["mentions"] = [{"id": u["id"], "full_name": u.get("full_name")} for u in existing if u["id"] in cleaned_mention_ids]

    return jsonify(created), 201


@bp.get("/<post_id>")
@optional_auth
def get_post(post_id):
    data, status = rest_request("GET", "feed", params={"id": f"eq.{post_id}", "select": "*"})
    if status != 200 or not data:
        return jsonify({"error": "post not found"}), 404

    data = _filter_blocked(data, g.token)
    data = _filter_by_audience(data, g.user_id, g.token)
    if not data:
        return jsonify({"error": "post not found"}), 404

    _attach_user_reactions(data, g.token, g.user_id)
    _attach_original_posts(data, g.token)
    _attach_polls(data, g.token)
    _attach_mentions(data, g.token)
    _attach_images(data, g.token)
    return jsonify(data[0]), 200


@bp.patch("/<post_id>")
@require_auth
def update_post(post_id):
    """Editing content or self-deleting (soft delete only — see
    migration v1.1. Hard delete is staff-only via posts_delete_staff)."""
    body = request.get_json(silent=True) or {}
    updates = {}

    if "content" in body:
        if not validate_post_content(body["content"]):
            return jsonify({"error": "post must be 1-2000 characters"}), 400
        updates["content"] = body["content"].strip()

    if body.get("delete") is True:
        updates["status"] = "removed"

    if not updates:
        return jsonify({"error": "nothing to update"}), 400

    data, status = rest_request(
        "PATCH", "posts", token=g.token,
        params={"id": f"eq.{post_id}"}, json_body=updates, prefer="return=representation",
    )
    if status >= 400:
        return jsonify({"error": "update failed or not permitted"}), status
    if not data:
        return jsonify({"error": "post not found or not yours"}), 404
    return jsonify(data[0]), 200


@bp.post("/<post_id>/vote")
@require_auth
def vote_poll(post_id):
    body = request.get_json(silent=True) or {}
    option_id = body.get("option_id")
    if not option_id:
        return jsonify({"error": "option_id required"}), 400

    option, ostatus = rest_request(
        "GET", "poll_options", token=g.token,
        params={"id": f"eq.{option_id}", "post_id": f"eq.{post_id}", "select": "id"},
    )
    if ostatus != 200 or not option:
        return jsonify({"error": "that option doesn't belong to this poll"}), 404

    # merge-duplicates on the (post_id, user_id) primary key is what
    # makes "vote" and "change your vote" the same call — the
    # bump_poll_vote_count trigger tells INSERT and UPDATE apart itself.
    data, status = rest_request(
        "POST", "poll_votes", token=g.token,
        json_body={"post_id": post_id, "user_id": g.user_id, "option_id": option_id},
        prefer="resolution=merge-duplicates,return=representation",
    )
    if status >= 400:
        return jsonify({"error": "could not register your vote"}), status
    return jsonify({"voted": True, "option_id": option_id}), 200


@bp.delete("/<post_id>/vote")
@require_auth
def retract_vote(post_id):
    data, status = rest_request(
        "DELETE", "poll_votes", token=g.token,
        params={"post_id": f"eq.{post_id}", "user_id": f"eq.{g.user_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not retract your vote"}), status
    return jsonify({"voted": False}), 200


@bp.post("/<post_id>/view")
def register_view(post_id):
    """No auth required — views come from anyone browsing, verified or
    not. Goes through the increment_view RPC because RLS wouldn't
    otherwise let a non-author touch this column (see db/schema.sql)."""
    data, status = rpc("increment_view", token=_bearer_token_if_present(),
                        payload={"p_post_id": post_id})
    if status >= 400:
        return jsonify({"error": "could not register view"}), status
    return jsonify({"ok": True}), 200


@bp.post("/<post_id>/search-hit")
def register_search_hit(post_id):
    data, status = rpc("increment_search_hit", token=_bearer_token_if_present(),
                        payload={"p_post_id": post_id})
    if status >= 400:
        return jsonify({"error": "could not register search hit"}), status
    return jsonify({"ok": True}), 200


@bp.get("/by-user/<user_id>")
@optional_auth
def posts_by_user(user_id):
    """Powers the profile grid — every active post from one author,
    newest first. Reuses the same `feed` view as the main feed (so
    blocking rules and author info stay consistent), just filtered
    to one author_id instead of ranked across everyone."""
    limit = request.args.get("limit", 60)
    offset = request.args.get("offset", 0)
    data, status = rest_request(
        "GET", "feed",
        params={
            "select": "*", "author_id": f"eq.{user_id}",
            "order": "created_at.desc", "limit": limit, "offset": offset,
        },
    )
    if status != 200:
        return jsonify({"error": "could not load posts"}), status

    data = _filter_blocked(data, g.token)
    data = _filter_by_audience(data, g.user_id, g.token)
    _attach_user_reactions(data, g.token, g.user_id)
    _attach_original_posts(data, g.token)
    _attach_polls(data, g.token)
    _attach_mentions(data, g.token)
    _attach_images(data, g.token)
    return jsonify(data), 200


@bp.post("/<post_id>/save")
@require_auth
def save_post(post_id):
    body = request.get_json(silent=True) or {}
    collection_id = body.get("collection_id")  # optional — null means uncategorized
    data, status = rest_request(
        "POST", "saved_posts", token=g.token,
        json_body={"user_id": g.user_id, "post_id": post_id, "collection_id": collection_id},
        prefer="resolution=merge-duplicates",
    )
    if status >= 400:
        return jsonify({"error": "could not save post"}), status
    return jsonify({"saved": True}), 201


@bp.delete("/<post_id>/save")
@require_auth
def unsave_post(post_id):
    data, status = rest_request(
        "DELETE", "saved_posts", token=g.token,
        params={"post_id": f"eq.{post_id}", "user_id": f"eq.{g.user_id}"},
    )
    if status >= 400:
        return jsonify({"error": "could not unsave post"}), status
    return jsonify({"saved": False}), 200


@bp.get("/saved")
@require_auth
def list_saved():
    """Powers the Saved tab on Profile — every post the caller has
    bookmarked, most recent save first. Optional ?collection_id=
    narrows to one folder; omit it (or pass ?collection_id=none) to
    mean 'everything', matching how save_post treats a null
    collection_id as uncategorized rather than invalid. Joins back
    through `feed` so the shape matches everywhere else a post gets
    rendered (author info, score, comment_count all included)."""
    collection_id = request.args.get("collection_id")
    params = {"user_id": f"eq.{g.user_id}", "select": "post_id,created_at", "order": "created_at.desc"}
    if collection_id == "none":
        params["collection_id"] = "is.null"
    elif collection_id:
        params["collection_id"] = f"eq.{collection_id}"

    saved, status = rest_request("GET", "saved_posts", token=g.token, params=params)
    if status != 200:
        return jsonify({"error": "could not load saved posts"}), status
    if not saved:
        return jsonify([]), 200

    post_ids = [s["post_id"] for s in saved]
    posts_data, pstatus = rest_request(
        "GET", "feed", token=g.token, params={"id": f"in.({','.join(post_ids)})", "select": "*"},
    )
    if pstatus != 200:
        return jsonify({"error": "could not load saved posts"}), pstatus

    by_id = {p["id"]: p for p in (posts_data or [])}
    ordered = [by_id[pid] for pid in post_ids if pid in by_id]  # preserves save-order, not feed's random order
    ordered = _filter_blocked(ordered, g.token)
    ordered = _filter_by_audience(ordered, g.user_id, g.token)
    _attach_user_reactions(ordered, g.token, g.user_id)
    _attach_original_posts(ordered, g.token)
    _attach_polls(ordered, g.token)
    _attach_mentions(ordered, g.token)
    _attach_images(ordered, g.token)
    for p in ordered:
        p["saved"] = True
    return jsonify(ordered), 200
