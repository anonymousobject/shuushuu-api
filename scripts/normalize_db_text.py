#!/usr/bin/env python3
"""
Normalize HTML-encoded text in database to plain text.

This script migrates legacy data that was stored with HTML entity encoding
(e.g., &quot;, &amp;, &#039;) to plain text format. This ensures:
- Consistent data storage (no mixed encoding)
- Prevents double-encoding issues
- Enables proper full-text search and sorting
- Maintains data portability

Tables/fields affected:
- users: user_title, location, website, interests
- privmsgs: subject, text
- posts (comments): post_text
- tags: title, desc

Usage:
    uv run python scripts/normalize_db_text.py [--dry-run] [--batch-size 1000]
"""

import argparse
import asyncio
from html import unescape as html_unescape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.models import Comments, Privmsgs, Tags, Users


def normalize_text(text: str | None) -> str | None:
    """Normalize text by unescaping HTML entities and trimming whitespace."""
    if not text:
        return text
    normalized = html_unescape(text).strip()

    # Warn if still contains HTML entities (potential double-encoding)
    if "&" in normalized and any(entity in normalized for entity in ["&quot;", "&amp;", "&#", "&lt;", "&gt;"]):
        print(f"    ⚠️  WARNING: Still contains HTML entities after normalization: {normalized[:100]}")

    return normalized


async def normalize_users(db: AsyncSession, batch_size: int, dry_run: bool) -> dict[str, int]:
    """
    Normalize user free-form text fields.

    Returns dict with counts of normalized fields.
    """
    fields_to_normalize = ["user_title", "location", "website", "interests"]
    counts = {field: 0 for field in fields_to_normalize}

    # Fetch all users
    result = await db.execute(
        select(Users).where(
            Users.active == 1  # Only normalize active users
        )
    )
    users = result.scalars().all()

    print(f"Found {len(users)} active users to check")

    batch_count = 0
    total_updated = 0

    for user in users:
        needs_update = False

        for field in fields_to_normalize:
            original = getattr(user, field)
            if original and isinstance(original, str):
                normalized = normalize_text(original)
                if normalized != original:
                    if not dry_run:
                        setattr(user, field, normalized)
                    counts[field] += 1
                    needs_update = True
                    if total_updated < 10:  # Only show first 10
                        print(f"  User {user.user_id} ({user.username}) - {field}:")
                        print(f"    Before: {original[:100]}")
                        print(f"    After:  {normalized[:100]}")

        if needs_update:
            total_updated += 1
            batch_count += 1

            # Commit in batches
            if not dry_run and batch_count >= batch_size:
                await db.commit()
                print(f"  Committed batch of {batch_count} users...")
                batch_count = 0

    # Commit remaining
    if not dry_run and batch_count > 0:
        await db.commit()
        print(f"  Committed final batch of {batch_count} users")

    return counts


async def normalize_privmsgs(db: AsyncSession, batch_size: int, dry_run: bool) -> dict[str, int]:
    """
    Normalize private message subjects and text.

    Returns dict with counts of normalized fields.
    """
    counts = {"subject": 0, "text": 0}

    # Fetch all messages
    result = await db.execute(select(Privmsgs))
    messages = result.scalars().all()

    print(f"Found {len(messages)} private messages to check")

    batch_count = 0
    total_updated = 0

    for msg in messages:
        needs_update = False

        # Normalize subject
        if msg.subject:
            normalized_subject = normalize_text(msg.subject)
            if normalized_subject != msg.subject:
                if not dry_run:
                    msg.subject = normalized_subject
                counts["subject"] += 1
                needs_update = True
                if total_updated < 10:  # Only show first 10
                    print(f"  PM {msg.privmsg_id} - subject:")
                    print(f"    Before: {msg.subject[:100]}")
                    print(f"    After:  {normalized_subject[:100]}")

        # Normalize text
        if msg.text:
            normalized_text = normalize_text(msg.text)
            if normalized_text != msg.text:
                if not dry_run:
                    msg.text = normalized_text
                counts["text"] += 1
                needs_update = True
                if total_updated < 10:  # Only show first 10
                    print(f"  PM {msg.privmsg_id} - text:")
                    print(f"    Before: {msg.text[:100]}")
                    print(f"    After:  {normalized_text[:100]}")

        if needs_update:
            total_updated += 1
            batch_count += 1

            # Commit in batches
            if not dry_run and batch_count >= batch_size:
                await db.commit()
                print(f"  Committed batch of {batch_count} messages...")
                batch_count = 0

    # Commit remaining
    if not dry_run and batch_count > 0:
        await db.commit()
        print(f"  Committed final batch of {batch_count} messages")

    return counts


async def normalize_comments(db: AsyncSession, batch_size: int, dry_run: bool) -> dict[str, int]:
    """
    Normalize comment text.

    Returns dict with counts of normalized fields.
    """
    counts = {"post_text": 0}

    # Fetch all comments
    result = await db.execute(select(Comments))
    comments = result.scalars().all()

    print(f"Found {len(comments)} comments to check")

    batch_count = 0
    total_updated = 0

    for comment in comments:
        needs_update = False

        # Normalize post_text
        if comment.post_text:
            normalized_text = normalize_text(comment.post_text)
            if normalized_text != comment.post_text:
                if not dry_run:
                    comment.post_text = normalized_text
                counts["post_text"] += 1
                needs_update = True
                if total_updated < 10:  # Only show first 10
                    print(f"  Comment {comment.post_id} - post_text:")
                    print(f"    Before: {comment.post_text[:100]}")
                    print(f"    After:  {normalized_text[:100]}")

        if needs_update:
            total_updated += 1
            batch_count += 1

            # Commit in batches
            if not dry_run and batch_count >= batch_size:
                await db.commit()
                print(f"  Committed batch of {batch_count} comments...")
                batch_count = 0

    # Commit remaining
    if not dry_run and batch_count > 0:
        await db.commit()
        print(f"  Committed final batch of {batch_count} comments")

    return counts


async def normalize_tags(db: AsyncSession, batch_size: int, dry_run: bool) -> dict[str, int]:
    """
    Normalize tag titles and descriptions.

    Returns dict with counts of normalized fields.
    """
    counts = {"title": 0, "desc": 0}

    # Fetch all tags
    result = await db.execute(select(Tags))
    tags = result.scalars().all()

    print(f"Found {len(tags)} tags to check")

    batch_count = 0
    total_updated = 0

    for tag in tags:
        needs_update = False

        # Normalize title
        if tag.title:
            normalized_title = normalize_text(tag.title)
            if normalized_title != tag.title:
                if not dry_run:
                    tag.title = normalized_title
                counts["title"] += 1
                needs_update = True
                if total_updated < 10:  # Only show first 10
                    print(f"  Tag {tag.tag_id} - title:")
                    print(f"    Before: {tag.title[:100]}")
                    print(f"    After:  {normalized_title[:100]}")

        # Normalize desc
        if tag.desc:
            normalized_desc = normalize_text(tag.desc)
            if normalized_desc != tag.desc:
                if not dry_run:
                    tag.desc = normalized_desc
                counts["desc"] += 1
                needs_update = True
                if total_updated < 10:  # Only show first 10
                    print(f"  Tag {tag.tag_id} - desc:")
                    print(f"    Before: {tag.desc[:100]}")
                    print(f"    After:  {normalized_desc[:100]}")

        if needs_update:
            total_updated += 1
            batch_count += 1

            # Commit in batches
            if not dry_run and batch_count >= batch_size:
                await db.commit()
                print(f"  Committed batch of {batch_count} tags...")
                batch_count = 0

    # Commit remaining
    if not dry_run and batch_count > 0:
        await db.commit()
        print(f"  Committed final batch of {batch_count} tags")

    return counts


async def main(dry_run: bool = False, batch_size: int = 1000, auto_confirm: bool = False):
    """Run normalization on all relevant tables."""
    print("=" * 80)
    print("Database Text Normalization Script")
    print("=" * 80)

    if dry_run:
        print("\n⚠️  DRY RUN MODE - No changes will be committed\n")
    else:
        print("\n⚠️  LIVE MODE - Changes will be committed to database\n")
        if not auto_confirm:
            response = input("Continue? (yes/no): ")
            if response.lower() != "yes":
                print("Aborted.")
                return

    async with get_async_session() as db:
        # Normalize users table
        print("\n" + "=" * 80)
        print("Normalizing Users table")
        print("=" * 80)
        user_counts = await normalize_users(db, batch_size, dry_run)

        # Normalize privmsgs table
        print("\n" + "=" * 80)
        print("Normalizing Private Messages table")
        print("=" * 80)
        privmsg_counts = await normalize_privmsgs(db, batch_size, dry_run)

        # Normalize comments table
        print("\n" + "=" * 80)
        print("Normalizing Comments table")
        print("=" * 80)
        comment_counts = await normalize_comments(db, batch_size, dry_run)

        # Normalize tags table
        print("\n" + "=" * 80)
        print("Normalizing Tags table")
        print("=" * 80)
        tag_counts = await normalize_tags(db, batch_size, dry_run)

        # Summary
        print("\n" + "=" * 80)
        print("Summary")
        print("=" * 80)
        print("\nUsers table:")
        for field, count in user_counts.items():
            print(f"  {field}: {count} records normalized")

        print("\nPrivate Messages table:")
        for field, count in privmsg_counts.items():
            print(f"  {field}: {count} records normalized")

        print("\nComments table:")
        for field, count in comment_counts.items():
            print(f"  {field}: {count} records normalized")

        print("\nTags table:")
        for field, count in tag_counts.items():
            print(f"  {field}: {count} records normalized")

        total = sum(user_counts.values()) + sum(privmsg_counts.values()) + sum(comment_counts.values()) + sum(tag_counts.values())
        print(f"\nTotal fields normalized: {total}")

        if dry_run:
            print("\n⚠️  This was a DRY RUN - no changes were made")
            print("Run with --no-dry-run to apply changes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Normalize HTML-encoded text in database")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview changes without committing to database (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Apply changes to database",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of records to process per batch (default: 1000)",
    )
    parser.add_argument(
        "--auto-confirm",
        action="store_true",
        help="Skip confirmation prompts (useful for CI/CD)",
    )

    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run, batch_size=args.batch_size, auto_confirm=args.auto_confirm))
