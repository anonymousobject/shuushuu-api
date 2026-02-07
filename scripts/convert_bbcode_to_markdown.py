#!/usr/bin/env python3
"""
Convert legacy BBCode text to markdown format.

This script migrates comment text from BBCode format to markdown.

BBCode patterns to convert:
- [quote="username"]text[/quote] → Keep as-is (already markdown-compatible)
- [spoiler]text[/spoiler] → ||text|| or [spoiler]text[/spoiler]
- [spoiler="title"]text[/spoiler] → [spoiler: title]text[/spoiler]
- [url]http://example.com[/url] → [http://example.com](http://example.com)
- [url=http://example.com]text[/url] → [text](http://example.com)
- <br /> → newline

Usage:
    uv run python scripts/convert_bbcode_to_markdown.py [--dry-run] [--batch-size 1000]
"""

import argparse
import asyncio
import re


from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.models import Comments
from app.core.logging import get_logger

logger = get_logger(__name__)

# Pre-compile regex patterns for better performance
BR_TAG_PATTERN = re.compile(r"<br\s*/?>\s*", re.IGNORECASE)
URL_WITH_PARAM_PATTERN = re.compile(
    r"\[url=([^\]]+)\](.+?)\[/url\]", re.DOTALL | re.IGNORECASE
)
URL_PLAIN_PATTERN = re.compile(r"\[url\](.+?)\[/url\]", re.DOTALL | re.IGNORECASE)
SPOILER_TITLE_QUOTED_PATTERN = re.compile(
    r"\[spoiler=&quot;(.+?)&quot;\](.+?)\[/spoiler\]", re.DOTALL | re.IGNORECASE
)
SPOILER_TITLE_PATTERN = re.compile(
    r'\[spoiler="(.+?)"\](.+?)\[/spoiler\]', re.DOTALL | re.IGNORECASE
)
SPOILER_PLAIN_PATTERN = re.compile(
    r"\[spoiler\](.+?)\[/spoiler\]", re.DOTALL | re.IGNORECASE
)
# Cleanup: fix already-converted markdown links with quoted URLs
# e.g. [text]("http://...") → [text](http://...)
BROKEN_MD_LINK_PATTERN = re.compile(r'\[([^\]]+)\]\((["\'])(.+?)\2\)')


def convert_bbcode_to_markdown(text: str) -> tuple[str, bool]:
    """
    Convert BBCode to markdown format.

    Returns tuple of (converted_text, was_modified)
    """
    if not text:
        return text, False

    original = text
    modified = False

    # Convert <br /> to newlines
    if "<br />" in text or "<br>" in text:
        text = BR_TAG_PATTERN.sub("\n", text)
        modified = True

    # Convert [url=...]text[/url] to [text](url)
    # Match [url=http://...] or [url=/...]
    def convert_url_with_param(match: re.Match[str]) -> str:
        nonlocal modified
        modified = True
        url = match.group(1).replace("&quot;", '"').strip('"\'')
        link_text = match.group(2)
        # Handle relative URLs
        if url.startswith("/"):
            url = f"https://example.com{url}"  # Will need to be adjusted based on actual domain
        return f"[{link_text}]({url})"

    text = URL_WITH_PARAM_PATTERN.sub(convert_url_with_param, text)

    # Convert [url]http://...[/url] to [http://...](http://...)
    def convert_url_plain(match: re.Match[str]) -> str:
        nonlocal modified
        modified = True
        url = match.group(1)
        return f"[{url}]({url})"

    text = URL_PLAIN_PATTERN.sub(convert_url_plain, text)

    # Convert [spoiler="title"]text[/spoiler] to [spoiler: title]\ntext\n[/spoiler]
    def convert_spoiler_with_title(match: re.Match[str]) -> str:
        nonlocal modified
        modified = True
        title = match.group(1)
        content = match.group(2)
        # Keep as BBCode but normalize format
        return f"[spoiler: {title}]\n{content}\n[/spoiler]"

    text = SPOILER_TITLE_QUOTED_PATTERN.sub(convert_spoiler_with_title, text)

    # Also handle [spoiler="title"] with regular quotes
    text = SPOILER_TITLE_PATTERN.sub(convert_spoiler_with_title, text)

    # Convert [spoiler]text[/spoiler] to [spoiler]\ntext\n[/spoiler]
    def convert_spoiler_plain(match: re.Match[str]) -> str:
        nonlocal modified
        modified = True
        content = match.group(1)
        return f"[spoiler]\n{content}\n[/spoiler]"

    text = SPOILER_PLAIN_PATTERN.sub(convert_spoiler_plain, text)

    # Fix already-converted markdown links with quoted URLs: [text]("url") → [text](url)
    def fix_broken_md_link(match: re.Match[str]) -> str:
        nonlocal modified
        modified = True
        link_text = match.group(1)
        url = match.group(3)
        return f"[{link_text}]({url})"

    text = BROKEN_MD_LINK_PATTERN.sub(fix_broken_md_link, text)

    # Quote tags should already be in correct format, but normalize any HTML entities
    if "&quot;" in text:
        text = text.replace("&quot;", '"')
        modified = True

    return text, modified and (text != original)


async def migrate_comments(db: AsyncSession, batch_size: int, dry_run: bool) -> dict[str, int]:
    """
    Convert BBCode to markdown in comments.

    Returns dict with conversion statistics.
    """
    counts = {"total": 0, "modified": 0, "errors": 0}

    # Get total count first
    count_result = await db.execute(select(func.count()).select_from(Comments))
    total_comments = count_result.scalar_one()

    print(f"Found {total_comments:,} comments to check")
    print(f"Processing in batches of {batch_size:,}...")

    offset = 0
    batch_num = 0

    while offset < total_comments:
        batch_num += 1

        # Fetch one batch at a time
        result = await db.execute(
            select(Comments)
            .order_by(Comments.post_id)
            .limit(batch_size)
            .offset(offset)
        )
        comments = result.scalars().all()

        if not comments:
            break

        batch_modified = 0

        # Process all comments in the batch
        for comment in comments:
            counts["total"] += 1

            if not comment.post_text:
                continue

            try:
                converted_text, was_modified = convert_bbcode_to_markdown(comment.post_text)

                if was_modified:
                    counts["modified"] += 1
                    batch_modified += 1

                    if not dry_run:
                        comment.post_text = converted_text

            except Exception as e:
                counts["errors"] += 1
                logger.error("conversion_error", comment_id=comment.post_id, error=str(e))
                print(f"Error converting comment {comment.post_id}: {e}")

        # Commit once per batch instead of per comment
        if not dry_run and batch_modified > 0:
            await db.commit()

        # Progress reporting
        progress_pct = min(100, (offset + len(comments)) / total_comments * 100)
        print(
            f"Batch {batch_num}: Processed {offset + len(comments):,}/{total_comments:,} "
            f"({progress_pct:.1f}%) - {batch_modified:,} modified in this batch"
        )

        offset += batch_size

    return counts


async def main(dry_run: bool = False, batch_size: int = 1000, auto_confirm: bool = False):
    """Run BBCode to Markdown conversion."""
    print("=" * 80)
    print("BBCode to Markdown Conversion Script")
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
        print("\n" + "=" * 80)
        print("Converting BBCode to Markdown")
        print("=" * 80)
        counts = await migrate_comments(db, batch_size, dry_run)

        # Summary
        print("\n" + "=" * 80)
        print("Summary")
        print("=" * 80)
        print(f"Total comments checked: {counts['total']}")
        print(f"Comments modified: {counts['modified']}")
        print(f"Errors: {counts['errors']}")

        if dry_run:
            print("\n⚠️  This was a DRY RUN - no changes were made")
            print("Run with --no-dry-run to apply changes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert BBCode to Markdown in database")
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
