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

from app.core.database import get_async_session
from app.models.tag import Tags
from app.models.tag_mapping import TagMapping

DEFAULT_CSV = Path("data/tag_mappings.csv")


async def import_mappings(csv_path: Path) -> None:
    """Import tag mappings from CSV file."""
    async with get_async_session() as db:
        # Pre-load all internal tags for lookup
        result = await db.execute(select(Tags.tag_id, Tags.title).where(Tags.type == 1))
        internal_tags = {title.lower(): tag_id for tag_id, title in result.all()}
        print(f"Loaded {len(internal_tags)} internal theme tags")

        # Read CSV
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        created = 0
        skipped = 0
        errors = []

        for row in rows:
            danbooru_tag = row["danbooru_tag"].strip()
            internal_title = row.get("internal_tag_title", "").strip()
            action = row.get("action", "map").strip()

            # Check if mapping already exists
            result = await db.execute(
                select(TagMapping).where(
                    TagMapping.external_tag == danbooru_tag,
                    TagMapping.external_source == "danbooru",
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                skipped += 1
                continue

            # Determine internal_tag_id
            internal_tag_id = None
            if action == "map" and internal_title:
                # Look up by title
                internal_tag_id = internal_tags.get(internal_title.lower())
                if internal_tag_id is None:
                    errors.append(f"Internal tag not found: '{internal_title}' for {danbooru_tag}")
                    continue
            elif action == "ignore":
                # NULL internal_tag_id means ignore
                internal_tag_id = None
            else:
                continue  # Skip unknown actions

            # Create mapping
            mapping = TagMapping(
                external_tag=danbooru_tag,
                external_source="danbooru",
                internal_tag_id=internal_tag_id,
                confidence=1.0,
            )
            db.add(mapping)
            created += 1

        await db.commit()

        print(f"\nResults:")
        print(f"  Created: {created}")
        print(f"  Skipped (already exist): {skipped}")
        print(f"  Errors: {len(errors)}")

        if errors:
            print("\nErrors:")
            for err in errors[:20]:
                print(f"  {err}")
            if len(errors) > 20:
                print(f"  ... and {len(errors) - 20} more")


async def main() -> None:
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV

    if not csv_path.exists():
        print(f"Error: {csv_path} not found")
        print(f"Usage: uv run python scripts/import_tag_mappings.py [csv_file]")
        sys.exit(1)

    print(f"Importing from: {csv_path}")
    await import_mappings(csv_path)
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
