#!/usr/bin/env python3
"""
Seed favorites/ratings activity for an e2e test account (dev/test only).

After a DB restore, the taste-profile feature has nothing to show for the
test accounts because favorites/ratings are wiped along with everything
else. This script seeds a small, deterministic slice of activity for a
given user so the feature is exercisable again.

Post-restore ordering:
    1. restore the dump
    2. alembic upgrade head
    3. scripts/db_utils.py create_test_user   (recreates testtagger, etc.)
    4. THIS script                            (seeds favorites + ratings)
    5. scripts/refresh_user_tag_affinity.py   (or wait for the 05:00 UTC
       nightly job) -- this is what turns the seeded activity into real
       user_tag_affinity rows.

This script intentionally does NOT write to user_tag_affinity directly.
Profiles must be produced by the real refresh so they reflect the actual
affinity algorithm rather than a hand-picked fake.

Usage:
    uv run python scripts/seed_test_account_activity.py
    uv run python scripts/seed_test_account_activity.py --username testtagger --tag "Code Geass" --favorites 12
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings


async def seed_activity(db: AsyncSession, username: str, tag_title: str, favorites: int) -> None:
    user_row = (
        await db.execute(text("SELECT user_id FROM users WHERE username = :username"), {"username": username})
    ).first()
    if user_row is None:
        print(f"Error: no user found with username '{username}'")
        sys.exit(1)
    user_id = user_row.user_id

    # Pick the N most recent visible images carrying the given tag.
    image_rows = (
        await db.execute(
            text(
                """
                SELECT i.image_id
                FROM images i
                JOIN tag_links tl ON tl.image_id = i.image_id
                JOIN tags t ON t.tag_id = tl.tag_id
                WHERE i.status = 1 AND t.title = :tag_title
                ORDER BY i.image_id DESC
                LIMIT :favorites
                """
            ),
            {"tag_title": tag_title, "favorites": favorites},
        )
    ).all()
    image_ids = [row.image_id for row in image_rows]

    if not image_ids:
        print(f"Error: no visible images found tagged '{tag_title}'")
        sys.exit(1)

    # NOTE: favorites has an AFTER INSERT trigger that updates the images
    # table. MariaDB forbids updating a table that a statement is also
    # selecting from (ERROR 1442), so `INSERT INTO favorites ... SELECT
    # ... FROM images` fails. We must select the image ids first (above)
    # and then insert them as plain VALUES.
    fav_result = await db.execute(
        text("INSERT IGNORE INTO favorites (user_id, image_id) VALUES (:user_id, :image_id)"),
        [{"user_id": user_id, "image_id": image_id} for image_id in image_ids],
    )

    # Rate half of the favorited images.
    rated_ids = image_ids[: len(image_ids) // 2]
    rating_result = None
    if rated_ids:
        rating_result = await db.execute(
            text("INSERT IGNORE INTO image_ratings (user_id, image_id, rating) VALUES (:user_id, :image_id, 9)"),
            [{"user_id": user_id, "image_id": image_id} for image_id in rated_ids],
        )

    await db.commit()

    fav_inserted = fav_result.rowcount or 0
    fav_skipped = len(image_ids) - fav_inserted
    rating_inserted = rating_result.rowcount if rating_result is not None else 0
    rating_skipped = len(rated_ids) - rating_inserted

    print(
        f"favorites: {fav_inserted} inserted, {fav_skipped} already present "
        f"(of {len(image_ids)} tagged '{tag_title}')"
    )
    print(f"ratings: {rating_inserted} inserted, {rating_skipped} already present (of {len(rated_ids)} attempted)")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="testtagger", help="Account to seed activity for")
    parser.add_argument("--tag", default="Code Geass", help="Tag title to source images from")
    parser.add_argument("--favorites", type=int, default=12, help="Number of images to favorite")
    args = parser.parse_args()

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        await seed_activity(db, args.username, args.tag, args.favorites)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
