#!/usr/bin/env python3
"""
Prune inactive users from the database.

This script identifies and optionally deletes users who are:
- Never posted any images (image_posts = 0)
- Never posted any comments
- Have no favorites
- Have no image ratings
- Have no tag history
- Have no tag links
- Have not been active since a specified threshold (optional)

Usage:
    # Dry run (shows what would be deleted)
    uv run python scripts/prune_inactive_users.py --dry-run

    # Delete inactive users
    uv run python scripts/prune_inactive_users.py --confirm

    # Delete inactive users not logged in for 6 months
    uv run python scripts/prune_inactive_users.py --days-inactive 180 --confirm

    # Delete very specific: never logged in, created over 1 year ago
    uv run python scripts/prune_inactive_users.py --days-inactive 999999 --confirm
"""

import asyncio
import argparse
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy import select, func, and_, case, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.user import Users
from app.models.image import Images
from app.models.comment import Comments
from app.models.favorite import Favorites
from app.models.image_rating import ImageRatings
from app.models.tag_link import TagLinks
from app.models.tag_history import TagHistory
from app.models.user_session import UserSessions
from app.config import settings


async def find_inactive_users(
    db: AsyncSession,
    days_inactive: Optional[int] = None,
) -> list[int]:
    """
    Find users that should be pruned using efficient subqueries.

    A user is considered inactive/pruneable if:
    - They have image_posts = 0 (never posted an image)
    - They have no comments
    - They have no favorites
    - They have no image ratings
    - They have no tag history
    - They have no tag links
    - If days_inactive specified: last_login_new is older than threshold
      (or NULL if never logged in)

    This uses a UNION-based approach to check for content across tables,
    which is much faster than LEFT JOINs for this use case.

    Args:
        db: AsyncSession for database queries
        days_inactive: Optional threshold for last login (in days)
                       If None, doesn't check login recency

    Returns:
        List of user_ids that can be safely deleted
    """
    print("\n" + "=" * 70)
    print("SCANNING FOR INACTIVE USERS")
    print("=" * 70)

    # Build WHERE conditions
    where_clauses = "u.image_posts = 0"

    if days_inactive is not None:
        cutoff_date = datetime.now(UTC) - timedelta(days=days_inactive)
        print(f"\nInactivity threshold: {days_inactive} days (before {cutoff_date.date()})")
        cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")
        where_clauses += f" AND (u.last_login_new IS NULL OR u.last_login_new < '{cutoff_str}')"

    # Use raw SQL with UNION to find users with content in ANY table
    # Much faster than LEFT JOINs with GROUP BY
    # IMPORTANT: Filter out NULL user_ids - if any subquery returns NULL,
    # the NOT IN clause will make the entire result NULL
    sql_query = text(f"""
        SELECT DISTINCT u.user_id
        FROM users u
        WHERE u.image_posts = 0
          {f"AND (u.last_login_new IS NULL OR u.last_login_new < :cutoff)" if days_inactive else ""}
          AND u.user_id NOT IN (
              SELECT DISTINCT user_id FROM posts WHERE user_id IS NOT NULL
              UNION
              SELECT DISTINCT user_id FROM favorites WHERE user_id IS NOT NULL
              UNION
              SELECT DISTINCT user_id FROM image_ratings WHERE user_id IS NOT NULL
              UNION
              SELECT DISTINCT user_id FROM tag_links WHERE user_id IS NOT NULL
              UNION
              SELECT DISTINCT user_id FROM tag_history WHERE user_id IS NOT NULL
              UNION
              SELECT DISTINCT user_id FROM user_sessions WHERE user_id IS NOT NULL
          )
        ORDER BY u.user_id
    """)

    params = {}
    if days_inactive is not None:
        cutoff_date = datetime.now(UTC) - timedelta(days=days_inactive)
        params["cutoff"] = cutoff_date

    result = await db.execute(sql_query, params)
    pruneable_users = [row[0] for row in result.all()]

    print(f"Found {len(pruneable_users)} users with image_posts=0", end="")
    if days_inactive:
        print(f" and inactive for {days_inactive}+ days")
    else:
        print()

    print(f"Verified {len(pruneable_users)} users with NO content in any table")

    return pruneable_users


async def get_user_details(db: AsyncSession, user_ids: list[int]) -> list[tuple]:
    """Fetch user details for display."""
    if not user_ids:
        return []

    stmt = (
        select(Users.user_id, Users.username, Users.date_joined, Users.last_login_new)
        .where(Users.user_id.in_(user_ids))
        .order_by(Users.date_joined)
    )
    result = await db.execute(stmt)
    return result.all()


async def delete_users(db: AsyncSession, user_ids: list[int]) -> int:
    """
    Delete specified users and cascade delete their related records.

    SQLAlchemy/SQLModel handles cascade deletes via foreign key constraints
    defined in the models with ondelete="CASCADE".

    Args:
        db: AsyncSession for database operations
        user_ids: List of user_ids to delete

    Returns:
        Number of users deleted
    """
    if not user_ids:
        return 0

    # Delete users (cascade deletes handle related records)
    stmt = select(Users).where(Users.user_id.in_(user_ids))
    result = await db.execute(stmt)
    users_to_delete = result.scalars().all()

    for user in users_to_delete:
        await db.delete(user)

    await db.commit()
    return len(users_to_delete)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prune inactive users from the database"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete the users (required to make changes)",
    )
    parser.add_argument(
        "--days-inactive",
        type=int,
        default=None,
        help="Only delete users inactive for N+ days (optional)",
    )

    args = parser.parse_args()

    if not args.dry_run and not args.confirm:
        print("\nERROR: Must specify --dry-run or --confirm")
        print("       Use --dry-run to preview what would be deleted")
        print("       Use --confirm to actually delete users")
        return

    # Connect to database
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, future=True
    )

    async with async_session() as db:
        # Find inactive users
        user_ids = await find_inactive_users(db, days_inactive=args.days_inactive)

        if not user_ids:
            print("\n✓ No inactive users found to prune")
            return

        # Get details for display
        user_details = await get_user_details(db, user_ids)

        # Display users that will be deleted
        print("\n" + "=" * 70)
        print("USERS TO BE DELETED")
        print("=" * 70)
        print(f"\n{'ID':<8} {'Username':<30} {'Joined':<12} {'Last Login':<12}")
        print("-" * 70)
        for user_id, username, date_joined, last_login in user_details:
            joined_str = date_joined.strftime("%Y-%m-%d") if date_joined else "Unknown"
            login_str = (
                last_login.strftime("%Y-%m-%d") if last_login else "Never"
            )
            print(f"{user_id:<8} {username:<30} {joined_str:<12} {login_str:<12}")

        print(f"\nTotal users to delete: {len(user_ids)}")

        if args.dry_run:
            print("\n[DRY RUN] No changes made")
        elif args.confirm:
            print("\n⚠️  DELETING USERS...")
            deleted_count = await delete_users(db, user_ids)
            print(f"✓ Successfully deleted {deleted_count} users")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
