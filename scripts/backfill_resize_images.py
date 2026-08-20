"""
scripts/backfill_resize_images.py

One-off maintenance script. NOT part of the Flask app, NOT imported by
anything else — run it directly, once (or occasionally), from your own
machine or a Render shell.

Why this exists: lib/image_processing.py now caps every *new* upload
(1600px for post/status images, 400px for avatars/group-avatars — see
AVATAR_MAX_DIMENSION), but everything already sitting in storage from
before those fixes is still full phone-camera resolution. Cached
egress (bandwidth spent re-serving images to every viewer) is what's
over quota on Supabase right now, and old oversized images are still
the ones being served on every repeat view. This walks every object
already in each bucket, re-runs it through the same normalize_image
used for new uploads (with the same per-bucket size cap new uploads
get), and overwrites it in place ONLY if the result is smaller than
what's there now — so this directly shrinks future egress without
touching anything that's already appropriately sized.

WHY THIS NEEDS THE SERVICE ROLE KEY, NOT THE APP'S NORMAL CREDENTIALS:
the Flask app deliberately never holds elevated access — every request
runs under RLS as either the anon key or the calling user's own JWT
(see the docstring at the top of lib/supabase_client.py). RLS on
storage.objects restricts each user's JWT to their own folder only
(`(storage.foldername(name))[1] = auth.uid()::text`). This script has
to touch every user's folder, so it's the one deliberate exception —
run manually, once, with a key that's never wired into the app itself.

USAGE:
  export SUPABASE_URL="https://xxxx.supabase.co"
  export SUPABASE_SERVICE_ROLE_KEY="..."     # Dashboard -> Settings -> API -> service_role secret
  python3 scripts/backfill_resize_images.py --dry-run     # see what WOULD change, touches nothing
  python3 scripts/backfill_resize_images.py                # actually resize + overwrite in place

The service role key is read from an environment variable only. Never
paste it into a chat, a commit, or hardcode it here.
"""

import argparse
import os
import sys
import time

import requests

# Reuses the exact same resize/re-encode logic new uploads already go
# through, so backfilled images end up identical in treatment to a
# fresh upload — same 1600px cap, same EXIF-orientation fix, same
# JPEG/PNG choice.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.image_processing import normalize_image, UnsupportedImageError, AVATAR_MAX_DIMENSION, MAX_DIMENSION  # noqa: E402

# Each bucket gets the same size cap its live upload route now uses —
# post/status images can be full feed-width, avatars/group-avatars
# never render above 88px anywhere in the app.
BUCKETS = [
    ("post-images", MAX_DIMENSION),
    ("avatars", AVATAR_MAX_DIMENSION),
    ("group-avatars", AVATAR_MAX_DIMENSION),
]
REQUEST_TIMEOUT = 30


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def list_all_objects(base_url: str, headers: dict, bucket: str, prefix: str = "") -> list[str]:
    """Recursively lists every file path in the bucket. Supabase's list
    endpoint only returns one folder level at a time — subfolders come
    back as entries with id=None — so this walks into each one."""
    paths = []
    offset = 0
    page_size = 100
    while True:
        resp = requests.post(
            f"{base_url}/storage/v1/object/list/{bucket}",
            headers=headers,
            json={"prefix": prefix, "limit": page_size, "offset": offset, "sortBy": {"column": "name", "order": "asc"}},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        entries = resp.json()
        if not entries:
            break
        for entry in entries:
            full_path = f"{prefix}{entry['name']}"
            if entry.get("id") is None:
                # A folder, not a file — recurse into it.
                paths.extend(list_all_objects(base_url, headers, bucket, prefix=f"{full_path}/"))
            else:
                paths.append(full_path)
        if len(entries) < page_size:
            break
        offset += page_size
    return paths


def download_object(base_url: str, headers: dict, bucket: str, path: str) -> bytes:
    resp = requests.get(f"{base_url}/storage/v1/object/{bucket}/{path}", headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.content


def overwrite_object(base_url: str, headers: dict, bucket: str, path: str, file_bytes: bytes, content_type: str) -> None:
    upload_headers = dict(headers)
    upload_headers["Content-Type"] = content_type
    upload_headers["x-upsert"] = "true"
    # Same reasoning as lib/supabase_client.py's storage_upload: these
    # are content-addressed paths, safe to cache for a year. Applying
    # it here too means this one backfill run fixes both problems at
    # once — shrinks oversized existing images AND gives them the
    # long cache lifetime new uploads now get by default, instead of
    # leaving backfilled images on Supabase's 1-hour default.
    upload_headers["cache-control"] = "public, max-age=31536000, immutable"
    resp = requests.post(f"{base_url}/storage/v1/object/{bucket}/{path}", headers=upload_headers, data=file_bytes, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()


def process_bucket(base_url: str, headers: dict, bucket: str, max_dimension: int, dry_run: bool) -> dict:
    print(f"\n=== {bucket} (cap: {max_dimension}px) ===")
    print(f"Listing every object in '{bucket}'...")
    paths = list_all_objects(base_url, headers, bucket)
    print(f"Found {len(paths)} objects.\n")

    stats = {"total": len(paths), "resized": 0, "skipped": 0, "failed": 0, "before": 0, "after": 0}

    for i, path in enumerate(paths, 1):
        try:
            original_bytes = download_object(base_url, headers, bucket, path)
        except requests.HTTPError as exc:
            print(f"[{i}/{len(paths)}] FAILED to download {path}: {exc}")
            stats["failed"] += 1
            continue

        original_size = len(original_bytes)
        stats["before"] += original_size

        try:
            new_bytes, content_type, _ext = normalize_image(original_bytes, max_dimension=max_dimension)
        except UnsupportedImageError as exc:
            print(f"[{i}/{len(paths)}] SKIPPED (unreadable) {path}: {exc}")
            stats["skipped"] += 1
            stats["after"] += original_size
            continue

        new_size = len(new_bytes)

        # Only overwrite if it's actually smaller — never re-upload a
        # file that's already at or under the cap, and never risk
        # replacing something with a same-or-larger result.
        if new_size >= original_size:
            stats["skipped"] += 1
            stats["after"] += original_size
            print(f"[{i}/{len(paths)}] already optimal, skipping: {path} ({original_size // 1024} KB)")
            continue

        saved_kb = (original_size - new_size) // 1024
        if dry_run:
            print(f"[{i}/{len(paths)}] WOULD resize {path}: {original_size // 1024} KB -> {new_size // 1024} KB (saves {saved_kb} KB)")
        else:
            try:
                overwrite_object(base_url, headers, bucket, path, new_bytes, content_type)
                print(f"[{i}/{len(paths)}] resized {path}: {original_size // 1024} KB -> {new_size // 1024} KB (saved {saved_kb} KB)")
            except requests.HTTPError as exc:
                print(f"[{i}/{len(paths)}] FAILED to upload {path}: {exc}")
                stats["failed"] += 1
                stats["after"] += original_size
                continue

        stats["resized"] += 1
        stats["after"] += new_size

        # Gentle pacing — this is a one-off maintenance run, not a
        # race, and there's no reason to hammer Storage's API.
        time.sleep(0.05)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill-resize existing oversized images across post-images, avatars, and group-avatars.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without uploading anything.")
    args = parser.parse_args()

    base_url = _require_env("SUPABASE_URL").rstrip("/")
    service_key = _require_env("SUPABASE_SERVICE_ROLE_KEY")
    headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}

    totals = {"total": 0, "resized": 0, "skipped": 0, "failed": 0, "before": 0, "after": 0}
    for bucket, max_dimension in BUCKETS:
        stats = process_bucket(base_url, headers, bucket, max_dimension, args.dry_run)
        for key in totals:
            totals[key] += stats[key]

    print("\n=== Overall Summary (all buckets) ===")
    print(f"Total objects:     {totals['total']}")
    print(f"Resized:           {totals['resized']}{' (dry run — nothing actually uploaded)' if args.dry_run else ''}")
    print(f"Skipped (optimal): {totals['skipped']}")
    print(f"Failed:            {totals['failed']}")
    print(f"Total size before: {totals['before'] / (1024*1024):.2f} MB")
    print(f"Total size after:  {totals['after'] / (1024*1024):.2f} MB")
    print(f"Reduction:         {(totals['before'] - totals['after']) / (1024*1024):.2f} MB")


if __name__ == "__main__":
    main()
