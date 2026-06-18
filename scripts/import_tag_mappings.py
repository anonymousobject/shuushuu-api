#!/usr/bin/env python3
"""
Import Danbooru tag mappings into the database.

Usage:
    uv run python scripts/import_tag_mappings.py [csv_file]

    Default CSV: data/tag_mappings.csv
"""

import asyncio
import csv
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.models.tag import Tags
from app.models.tag_mapping import TagMappings

DEFAULT_CSV = Path("data/tag_mappings.csv")

# Internal tag types that Danbooru tags can be mapped to. ML predictions only
# ever resolve to theme (general predictions) or character tags, so those are
# the types we load for the title -> tag_id lookup.
MAPPABLE_TAG_TYPES = (1, 4)  # 1=Theme, 4=Character


async def import_mappings(db: AsyncSession, csv_path: Path) -> dict[str, object]:
    """Import tag mappings from a CSV into the DB. Insert-only (existing
    external_tag rows are skipped). Returns a summary dict of counts."""
    # Pre-load mappable internal tags for case-insensitive title lookup.
    result = await db.execute(
        select(Tags.tag_id, Tags.title).where(Tags.type.in_(MAPPABLE_TAG_TYPES))  # type: ignore[attr-defined]
    )
    internal_tags: dict[str, int] = {}
    ambiguous_titles: set[str] = set()
    for tag_id, title in result.all():
        if title is None:
            continue
        key = title.lower()
        if key in internal_tags:
            ambiguous_titles.add(key)
        else:
            internal_tags[key] = tag_id

    # Read CSV
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    created = 0
    skipped = 0
    errors: list[str] = []

    for row in rows:
        danbooru_tag = row["danbooru_tag"].strip()
        internal_title = row.get("internal_tag_title", "").strip()
        action = row.get("action", "map").strip()

        # Check if mapping already exists
        existing_result = await db.execute(
            select(TagMappings).where(TagMappings.external_tag == danbooru_tag)  # type: ignore[arg-type]
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            skipped += 1
            continue

        # Determine internal_tag_id
        internal_tag_id: int | None = None
        if action == "map" and internal_title:
            key = internal_title.lower()
            if key in ambiguous_titles:
                errors.append(
                    f"Ambiguous internal title '{internal_title}' "
                    f"(multiple tags share it) for {danbooru_tag}"
                )
                continue
            internal_tag_id = internal_tags.get(key)
            if internal_tag_id is None:
                errors.append(
                    f"Internal tag not found: '{internal_title}' for {danbooru_tag}"
                )
                continue
        elif action == "ignore":
            # NULL internal_tag_id means "known but ignored"
            internal_tag_id = None
        else:
            continue  # Skip unknown actions

        # Create mapping
        mapping = TagMappings(
            external_tag=danbooru_tag,
            internal_tag_id=internal_tag_id,
            confidence=1.0,
        )
        db.add(mapping)
        created += 1

    await db.commit()

    return {
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "internal_tags_loaded": len(internal_tags),
    }


async def main() -> None:
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV

    if not csv_path.exists():
        print(f"Error: {csv_path} not found")
        print("Usage: uv run python scripts/import_tag_mappings.py [csv_file]")
        sys.exit(1)

    print(f"Importing from: {csv_path}")
    async with get_async_session() as db:
        summary = await import_mappings(db, csv_path)

    print(f"Loaded {summary['internal_tags_loaded']} mappable internal tags")
    print("\nResults:")
    print(f"  Created: {summary['created']}")
    print(f"  Skipped (already exist): {summary['skipped']}")
    errors = summary["errors"]
    assert isinstance(errors, list)
    print(f"  Errors: {len(errors)}")

    if errors:
        print("\nErrors:")
        for err in errors[:20]:
            print(f"  {err}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
