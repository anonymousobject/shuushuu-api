#!/usr/bin/env python3
"""
Import Danbooru tag mappings into the database.

Usage:
    uv run python scripts/import_tag_mappings.py [csv_file]

    Default CSV: data/tag_mappings.csv

CSV columns (header row required):
    danbooru_tag       external (Danbooru) tag name — the mapping's unique key
    internal_tag_title human-readable internal tag title (resolved case-insensitively)
    internal_tag_id    OPTIONAL authoritative internal tag id; when set it wins over
                       the title (and resolves titles shared by multiple tags). If a
                       title is shared across tags (e.g. a theme AND a character named
                       "snake") and no id is given, the row is reported as ambiguous
                       and skipped rather than guessed.
    action             "map" (default) or "ignore" (store NULL = known-but-ignored)

This is an UPSERT keyed by danbooru_tag: new rows are created, rows whose target
changed are updated in place, matching rows are left unchanged. Rows removed from
the CSV are NOT deleted from the DB.
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

    # Validate any explicit internal_tag_id values up front (single query). An
    # explicit id is authoritative and bypasses the title lookup, so it resolves
    # otherwise-ambiguous titles. Allow any tag id (not just MAPPABLE_TAG_TYPES) —
    # if a curator names an id explicitly, trust it.
    explicit_ids: set[int] = set()
    for row in rows:
        raw_id = (row.get("internal_tag_id") or "").strip()
        if raw_id:
            try:
                explicit_ids.add(int(raw_id))
            except ValueError:
                continue  # malformed values are reported per-row below
    id_to_title: dict[int, str | None] = {}
    if explicit_ids:
        res = await db.execute(
            select(Tags.tag_id, Tags.title).where(Tags.tag_id.in_(explicit_ids))  # type: ignore[attr-defined]
        )
        id_to_title = {tag_id: title for tag_id, title in res.all()}

    created = 0
    updated = 0
    unchanged = 0
    errors: list[str] = []
    warnings: list[str] = []

    for row in rows:
        danbooru_tag = row["danbooru_tag"].strip()
        internal_title = (row.get("internal_tag_title") or "").strip()
        raw_id = (row.get("internal_tag_id") or "").strip()
        action = (row.get("action") or "map").strip()

        # Resolve the target internal_tag_id for this row.
        target_id: int | None = None
        if action == "ignore":
            target_id = None  # NULL internal_tag_id means "known but ignored"
        elif raw_id:
            # Explicit id wins: unambiguous, authoritative.
            try:
                tid = int(raw_id)
            except ValueError:
                errors.append(f"Invalid internal_tag_id {raw_id!r} for {danbooru_tag}")
                continue
            if tid not in id_to_title:
                errors.append(f"internal_tag_id {tid} not found for {danbooru_tag}")
                continue
            actual_title = id_to_title[tid]
            if internal_title and (actual_title or "").lower() != internal_title.lower():
                warnings.append(
                    f"{danbooru_tag}: internal_tag_id {tid} is {actual_title!r} but CSV "
                    f"title is {internal_title!r} (using id)"
                )
            target_id = tid
        elif action == "map" and internal_title:
            key = internal_title.lower()
            if key in ambiguous_titles:
                errors.append(
                    f"Ambiguous internal title {internal_title!r} for {danbooru_tag} "
                    "(set internal_tag_id to disambiguate)"
                )
                continue
            target_id = internal_tags.get(key)
            if target_id is None:
                errors.append(f"Internal tag not found: {internal_title!r} for {danbooru_tag}")
                continue
        else:
            continue  # unknown action / nothing to map

        # Upsert by external_tag (insert new, update changed target, else unchanged).
        existing = (
            await db.execute(
                select(TagMappings).where(TagMappings.external_tag == danbooru_tag)  # type: ignore[arg-type]
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.internal_tag_id != target_id:
                existing.internal_tag_id = target_id
                updated += 1
            else:
                unchanged += 1
        else:
            db.add(
                TagMappings(
                    external_tag=danbooru_tag,
                    internal_tag_id=target_id,
                    confidence=1.0,
                )
            )
            created += 1

    await db.commit()

    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "errors": errors,
        "warnings": warnings,
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
    print(f"  Created:   {summary['created']}")
    print(f"  Updated:   {summary['updated']}")
    print(f"  Unchanged: {summary['unchanged']}")
    warnings = summary["warnings"]
    errors = summary["errors"]
    assert isinstance(warnings, list) and isinstance(errors, list)
    print(f"  Warnings:  {len(warnings)}")
    print(f"  Errors:    {len(errors)}")

    if warnings:
        print("\nWarnings:")
        for warn in warnings[:20]:
            print(f"  {warn}")
        if len(warnings) > 20:
            print(f"  ... and {len(warnings) - 20} more")

    if errors:
        print("\nErrors:")
        for err in errors[:20]:
            print(f"  {err}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
