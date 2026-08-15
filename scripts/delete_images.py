#!/usr/bin/env python3
"""Bulk-delete images by ID by calling the API's delete_image handler directly.

This invokes the exact same logic as ``DELETE /api/v1/images/{image_id}``:
permission check, IQDB removal, file deletion, CASCADE DB delete, an
``admin_actions`` audit entry crediting the acting user, and (if enabled) R2
cleanup. It is DESTRUCTIVE and cannot be undone.

The acting user must have the IMAGE_DELETE permission; if they don't, nothing
is deleted (the handler raises 403). Missing image IDs are skipped (404).

Usage:
    uv run python scripts/delete_images.py --user <username> --reason "<why>" 12345 12346 ...
    uv run python scripts/delete_images.py --user admin --reason "DMCA takedown" --ids-file ids.txt
"""

import argparse
import asyncio
import sys

import redis.asyncio as redis
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1.images import delete_image
from app.config import settings
from app.core.database import AsyncSessionLocal
from app.models.user import Users


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Permanently delete images by ID.")
    parser.add_argument("image_ids", nargs="*", type=int, help="Image IDs to delete")
    parser.add_argument(
        "--ids-file",
        help="Optional file with whitespace/newline-separated image IDs to delete",
    )
    parser.add_argument(
        "--user", required=True, help="Username performing the deletion (needs IMAGE_DELETE)"
    )
    parser.add_argument(
        "--reason", required=True, help="Reason recorded in the audit log (1-500 chars)"
    )
    return parser.parse_args()


def collect_ids(args: argparse.Namespace) -> list[int]:
    ids = list(args.image_ids)
    if args.ids_file:
        with open(args.ids_file) as f:
            ids.extend(int(token) for token in f.read().split())
    # De-dupe while preserving order
    return list(dict.fromkeys(ids))


async def main() -> int:
    args = parse_args()
    image_ids = collect_ids(args)
    if not image_ids:
        print("No image IDs provided.", file=sys.stderr)
        return 1

    redis_client = redis.from_url(str(settings.REDIS_URL), encoding="utf-8", decode_responses=True)
    deleted, missing, failed = 0, 0, 0

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Users).where(Users.username == args.user))
            acting_user = result.scalar_one_or_none()
            if acting_user is None:
                print(f"User {args.user!r} not found.", file=sys.stderr)
                return 1

            print(f"Deleting {len(image_ids)} image(s) as {args.user!r}...")
            for image_id in image_ids:
                try:
                    # delete_image commits its own session on success.
                    await delete_image(image_id, args.reason, acting_user, db, redis_client)
                    deleted += 1
                    print(f"  deleted {image_id}")
                except HTTPException as exc:
                    await db.rollback()
                    if exc.status_code == 404:
                        missing += 1
                        print(f"  skip {image_id}: not found")
                    elif exc.status_code == 403:
                        # Permission failure is fatal: it will fail for every ID.
                        print(
                            f"  {args.user!r} lacks IMAGE_DELETE permission; aborting.",
                            file=sys.stderr,
                        )
                        return 1
                    else:
                        failed += 1
                        print(f"  fail {image_id}: {exc.status_code} {exc.detail}", file=sys.stderr)
                except Exception as exc:  # noqa: BLE001 - report and continue the batch
                    await db.rollback()
                    failed += 1
                    print(f"  fail {image_id}: {exc!r}", file=sys.stderr)
    finally:
        await redis_client.close()

    print(f"Done. deleted={deleted} missing={missing} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
