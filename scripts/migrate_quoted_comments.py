"""
Migrate legacy quoted comments to new parent_comment_id format.

Old format: Comment text includes quoted reply like:
> Original comment text
> (possibly multiple lines)
My response to that

Or BBCode format:
[quote="username"]Original text[/quote]
My response to that

New format: parent_comment_id points to the original comment

This script:
1. Finds comments with quote patterns (> or [quote] tags)
2. Extracts the quoted text
3. Finds exact text matches in existing comments
4. Updates parent_comment_id if a match is found
5. Removes the quote from the comment text
"""

import asyncio
import re
from typing import Optional

from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.models import Comments
from app.core.logging import get_logger

logger = get_logger(__name__)


def extract_quoted_text(comment_text: str) -> Optional[str]:
    """
    Extract quoted text from comment.
    Supports two formats:
    1. Markdown-style: lines starting with >
    2. BBCode-style: [quote="username"]text[/quote] (including nested quotes)

    For nested quotes like [quote="A"][quote="B"]text[/quote]...[/quote],
    extracts the innermost quote (text).

    Returns the quoted text without markup, or None if no quotes found.
    """
    import re

    # Try BBCode format: extract innermost quote recursively
    def extract_innermost_quote(text: str) -> Optional[str]:
        # Find the first opening quote tag (handles both " and &quot;)
        start_match = re.search(r'\[quote=(?:"[^"]*"|&quot;[^&]*&quot;)\]', text)
        if not start_match:
            return None

        # Find matching closing quote tag by counting quote nesting
        start_pos = start_match.end()
        depth = 1
        pos = start_pos

        while pos < len(text) and depth > 0:
            # Look for next quote tag (both quote styles)
            next_open_dq = text.find('[quote="', pos)
            next_open_eq = text.find('[quote=&quot;', pos)
            next_close = text.find('[/quote]', pos)

            # Determine which comes first
            next_opens = [x for x in [next_open_dq, next_open_eq] if x != -1]
            next_open = min(next_opens) if next_opens else -1

            if next_close == -1:
                # No closing quote found, malformed
                return None

            if next_open != -1 and next_open < next_close:
                # Found opening quote before closing quote
                depth += 1
                pos = next_open + 1
            else:
                # Found closing quote
                depth -= 1
                if depth == 0:
                    content = text[start_pos:next_close]
                    # Check if there are nested quotes inside
                    if '[quote=' in content:
                        # Recursively extract the innermost quote
                        inner = extract_innermost_quote(content)
                        return inner if inner else content.strip()
                    else:
                        # No nested quotes, this is the innermost
                        return content.strip()
                pos = next_close + 1

        return None

    innermost = extract_innermost_quote(comment_text)
    if innermost:
        return innermost

    # Try Markdown format: lines starting with > or >>
    lines = comment_text.split('\n')
    quoted_lines = []

    for line in lines:
        stripped = line.strip()
        # Handle both > and >> prefixes
        if stripped.startswith('>>'):
            quoted = stripped[2:].lstrip()
            quoted_lines.append(quoted)
        elif stripped.startswith('>'):
            quoted = stripped[1:].lstrip()
            quoted_lines.append(quoted)
        elif quoted_lines:
            # Stop at first non-quoted line after we've found quotes
            break

    if quoted_lines:
        return '\n'.join(quoted_lines)

    return None


def remove_quoted_text(comment_text: str) -> str:
    """Remove quoted text from comment (both formats)."""
    import re

    # Remove BBCode format: [quote="..."]...[/quote] or [quote=&quot;...&quot;]...[/quote]
    text = re.sub(r'\[quote=(?:"[^"]*"|&quot;[^&]*&quot;)\].*?\[/quote\]', '', comment_text, flags=re.DOTALL)

    # Remove Markdown format: lines starting with > or >>
    lines = text.split('\n')
    result_lines = []
    skipping_quotes = True

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('>>') or stripped.startswith('>'):
            skipping_quotes = True
            continue
        elif skipping_quotes and not stripped:
            # Skip empty lines after quotes
            continue
        else:
            skipping_quotes = False
            result_lines.append(line)

    # Join and strip leading/trailing whitespace
    return '\n'.join(result_lines).strip()


async def find_parent_comment(
    quoted_text: str,
    image_id: int,
    current_comment_id: int,
    db: AsyncSession,
) -> Optional[int]:
    """
    Find a parent comment by exact text match.

    Args:
        quoted_text: The extracted quoted text
        image_id: Image ID to search within (quoted comment should be on same image)
        current_comment_id: Don't match against self
        db: Database session

    Returns:
        post_id of matching comment, or None if no exact match found
    """
    # Find exact text match on this image
    result = await db.execute(
        select(Comments).where(
            (Comments.image_id == image_id) &
            (Comments.post_id != current_comment_id) &
            (Comments.post_text == quoted_text)
        )
    )
    comment = result.scalars().first()

    if comment:
        logger.info(
            "exact_match_found",
            quoted_len=len(quoted_text),
            candidate_id=comment.post_id,
        )
        return comment.post_id

    return None


async def migrate_quoted_comments(dry_run: bool = True, batch_size: int = 5000, auto_confirm: bool = False):
    """
    Main migration function (two-pass approach for correctness).

    Pass 1: Build mapping of quoted text -> parent comment IDs without modifying text
    Pass 2: Apply updates (set parent_id and strip quotes)

    This ensures that comments migrated in earlier runs don't break future matching,
    because we see their ORIGINAL text in pass 1 before any modifications.

    Args:
        dry_run: If True, log matches without updating. If False, update database.
        batch_size: Number of updates to batch before committing (default: 5000)
    """
    import time

    async with get_async_session() as db:
        start_time = time.time()

        # STEP 1: Load only comments with quotes
        logger.info("migration_step", step="1_load_quoted_comments")
        result = await db.execute(
            select(Comments).where(
                (Comments.post_text.like('%[quote=%')) |
                (Comments.post_text.like('>%'))
            )
        )
        quoted_comments = result.scalars().all()
        total_comments = len(quoted_comments)

        logger.info("migration_step", step="2_loaded_quoted_comments", count=total_comments)

        comments_with_quotes = 0
        comments_matched = 0
        comments_updated = 0

        # Build index of all comments by image_id for fast lookups
        logger.info("migration_step", step="3_indexing_comments")
        image_ids = set(c.image_id for c in quoted_comments)

        # Fetch only necessary fields for matching (reduces memory)
        result = await db.execute(
            select(Comments.post_id, Comments.image_id, Comments.post_text)
            .where(Comments.image_id.in_(image_ids))
        )
        comments_by_image: dict[int, list] = {}
        for row in result.all():
            if row.image_id not in comments_by_image:
                comments_by_image[row.image_id] = []
            comments_by_image[row.image_id].append((row.post_id, row.post_text))

        logger.info(
            "migration_step",
            step="4_indexed_images",
            image_count=len(image_ids),
        )

        # PASS 1: Build mapping without modifying anything
        logger.info("migration_step", step="5_pass1_finding_matches")
        updates_batch = []  # Will contain {post_id, parent_id, new_text}

        for i, comment in enumerate(quoted_comments):
            # Extract quoted text
            quoted_text = extract_quoted_text(comment.post_text)

            if not quoted_text:
                continue

            comments_with_quotes += 1

            # Skip if already has parent_comment_id
            if comment.parent_comment_id is not None:
                continue

            # Try to find matching parent comment in memory using ORIGINAL text
            parent_id = None
            if comment.image_id in comments_by_image:
                for candidate_id, candidate_text in comments_by_image[comment.image_id]:
                    if candidate_id != comment.post_id and candidate_text == quoted_text:
                        parent_id = candidate_id
                        break

            if parent_id:
                comments_matched += 1

                # Remove quotes from comment text
                new_text = remove_quoted_text(comment.post_text)

                # Add to batch updates
                updates_batch.append({
                    'post_id': comment.post_id,
                    'parent_id': parent_id,
                    'new_text': new_text,
                })

            # Progress indicator every 5000 comments
            if (i + 1) % 5000 == 0:
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed
                remaining = (total_comments - i - 1) / rate if rate > 0 else 0
                logger.info(
                    "progress",
                    processed=i + 1,
                    total=total_comments,
                    elapsed_s=int(elapsed),
                    remaining_s=int(remaining),
                )

        # PASS 2: Apply all updates in batches
        logger.info("migration_step", step="6_pass2_applying_updates", total_updates=comments_matched)

        if not dry_run and updates_batch:
            for batch_start in range(0, len(updates_batch), batch_size):
                batch = updates_batch[batch_start:batch_start + batch_size]

                # Use bulk UPDATE with CASE statements for maximum performance
                # Build parameterized CASE expressions and IN clause to avoid SQL injection
                parent_case_fragments = []
                text_case_fragments = []
                in_clause_fragments = []
                params = {}
                for u in batch:
                    suffix = u["post_id"]
                    parent_case_fragments.append(
                        f"WHEN :post_id_{suffix} THEN :parent_id_{suffix}"
                    )
                    text_case_fragments.append(
                        f"WHEN :post_id_{suffix} THEN :text_{suffix}"
                    )
                    in_clause_fragments.append(f":post_id_{suffix}")
                    params[f"post_id_{suffix}"] = u["post_id"]
                    params[f"parent_id_{suffix}"] = u["parent_id"]
                    params[f"text_{suffix}"] = u["new_text"]

                query = text(
                    """
                        UPDATE posts
                        SET parent_comment_id = CASE post_id
                            """
                    + "\n".join(parent_case_fragments)
                    + """
                            ELSE parent_comment_id
                        END,
                        post_text = CASE post_id
                            """
                    + "\n".join(text_case_fragments)
                    + """
                            ELSE post_text
                        END
                        WHERE post_id IN ("""
                    + ",".join(in_clause_fragments)
                    + ")"
                )

                await db.execute(query, params)
                await db.commit()

                comments_updated += len(batch)
                logger.info(
                    "batch_committed",
                    batch_size=len(batch),
                    total_updated=comments_updated,
                )
        elif dry_run:
            comments_updated = comments_matched

        elapsed_total = time.time() - start_time

        logger.info(
            "migration_complete",
            total_comments=total_comments,
            comments_with_quotes=comments_with_quotes,
            comments_matched=comments_matched,
            comments_updated=comments_updated,
            dry_run=dry_run,
            elapsed_seconds=int(elapsed_total),
        )

        print("\n" + "=" * 60)
        print("MIGRATION RESULTS")
        print("=" * 60)
        print(f"Total quoted comments:       {total_comments:,}")
        print(f"Comments with extractable quotes: {comments_with_quotes:,}")
        print(f"Parent comments found:       {comments_matched:,}")
        print(f"Comments updated:            {comments_updated:,}")
        print(f"Dry run mode:                {dry_run}")
        print(f"Total time:                  {int(elapsed_total)}s ({elapsed_total/60:.1f}m)")
        print("=" * 60 + "\n")


if __name__ == "__main__":
    import argparse
    import sys
    import warnings

    # Suppress aiomysql cleanup warnings (harmless)
    warnings.filterwarnings("ignore", message=".*Event loop is closed.*")

    parser = argparse.ArgumentParser(description="Migrate quoted comments to parent_comment_id relationships")
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
        default=5000,
        help="Number of updates to batch before committing (default: 5000)",
    )
    parser.add_argument(
        "--auto-confirm",
        action="store_true",
        help="Skip confirmation prompts (useful for CI/CD)",
    )

    args = parser.parse_args()

    if args.dry_run:
        print("Running in DRY RUN mode (no changes will be made)")
        print("Pass --no-dry-run to actually update the database\n")
    else:
        print("⚠️  RUNNING IN UPDATE MODE - CHANGES WILL BE MADE\n")
        if not args.auto_confirm:
            response = input("Continue? (yes/no): ")
            if response.lower() != "yes":
                print("Aborted.")
                sys.exit(0)
        print()

    # Run with proper cleanup
    try:
        asyncio.run(migrate_quoted_comments(dry_run=args.dry_run, batch_size=args.batch_size, auto_confirm=args.auto_confirm))
    finally:
        # Ensure all async tasks are cleaned up
        pass
