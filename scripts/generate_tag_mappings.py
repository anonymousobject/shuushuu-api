#!/usr/bin/env python3
"""
Generate tag mappings between WD-Tagger Danbooru tags and internal Shuushuu tags.

This script:
1. Loads internal theme tags (type=1) from the database
2. Loads WD-Tagger tags from selected_tags.csv
3. Finds matches based on normalized names
4. Outputs a CSV for manual review and import

Usage:
    uv run python scripts/generate_tag_mappings.py
"""

import asyncio
import csv
import re
from pathlib import Path

from sqlalchemy import select

from app.core.database import get_async_session
from app.models.tag import Tags

# Paths
WD_TAGGER_TAGS = Path("ml_models/wd-swinv2-tagger-v3/selected_tags.csv")
OUTPUT_CSV = Path("data/tag_mappings_draft.csv")


def normalize_tag(tag: str) -> str:
    """
    Normalize tag for comparison.

    - lowercase
    - replace underscores with spaces
    - remove special characters
    - collapse multiple spaces
    """
    tag = tag.lower()
    tag = tag.replace("_", " ")
    tag = re.sub(r"[^a-z0-9\s]", "", tag)
    tag = re.sub(r"\s+", " ", tag)
    return tag.strip()


def load_wd_tagger_tags() -> dict[str, dict]:
    """Load WD-Tagger tags from CSV, return dict by normalized name."""
    tags = {}
    with open(WD_TAGGER_TAGS) as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["name"]
            category = int(row["category"])
            # Only include general tags (category 0)
            if category == 0:
                normalized = normalize_tag(name)
                tags[normalized] = {
                    "original": name,
                    "category": category,
                }
    return tags


async def load_internal_tags() -> dict[str, dict]:
    """Load internal theme tags (type=1) from database."""
    async with get_async_session() as db:
        result = await db.execute(
            select(Tags.tag_id, Tags.title, Tags.type)
            .where(Tags.type == 1)  # Theme/general tags only
        )
        tags = {}
        for tag_id, title, tag_type in result.all():
            normalized = normalize_tag(title)
            tags[normalized] = {
                "tag_id": tag_id,
                "title": title,
                "type": tag_type,
            }
        return tags


def find_matches(
    wd_tags: dict[str, dict],
    internal_tags: dict[str, dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Find matches between WD-Tagger and internal tags.

    Returns:
        - exact_matches: Tags with exact normalized name match
        - partial_matches: Tags with partial/fuzzy match (for review)
        - unmatched_wd: WD-Tagger tags with no match
    """
    exact_matches = []
    partial_matches = []
    unmatched_wd = []

    # Track which internal tags were matched
    matched_internal = set()

    for wd_normalized, wd_data in wd_tags.items():
        # Try exact match
        if wd_normalized in internal_tags:
            internal = internal_tags[wd_normalized]
            exact_matches.append({
                "danbooru_tag": wd_data["original"],
                "internal_tag_id": internal["tag_id"],
                "internal_title": internal["title"],
                "match_type": "exact",
                "action": "map",
            })
            matched_internal.add(wd_normalized)
            continue

        # Try partial match (WD tag contains internal tag or vice versa)
        found_partial = False
        for int_normalized, int_data in internal_tags.items():
            if int_normalized in matched_internal:
                continue

            # Check if one contains the other
            if wd_normalized in int_normalized or int_normalized in wd_normalized:
                partial_matches.append({
                    "danbooru_tag": wd_data["original"],
                    "internal_tag_id": int_data["tag_id"],
                    "internal_title": int_data["title"],
                    "match_type": "partial",
                    "action": "review",
                })
                found_partial = True
                break

        if not found_partial:
            unmatched_wd.append({
                "danbooru_tag": wd_data["original"],
                "internal_tag_id": "",
                "internal_title": "",
                "match_type": "none",
                "action": "ignore",  # Default to ignore, can be changed
            })

    return exact_matches, partial_matches, unmatched_wd


def write_output(
    exact_matches: list[dict],
    partial_matches: list[dict],
    unmatched_wd: list[dict],
    internal_tags: dict[str, dict]
) -> None:
    """Write results to CSV for review."""
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "danbooru_tag", "internal_tag_id", "internal_title",
            "match_type", "action"
        ])
        writer.writeheader()

        # Write exact matches first
        for row in sorted(exact_matches, key=lambda x: x["danbooru_tag"]):
            writer.writerow(row)

        # Then partial matches for review
        for row in sorted(partial_matches, key=lambda x: x["danbooru_tag"]):
            writer.writerow(row)

        # Then unmatched (limited to common/useful tags)
        # Only include tags that might be worth mapping later
        useful_unmatched = [
            row for row in unmatched_wd
            if not any(skip in row["danbooru_tag"] for skip in [
                "1girl", "1boy", "solo", "multiple_", "2girls", "2boys",
                "_focus", "_background", "simple_background"
            ])
        ][:200]  # Limit to 200 most common
        for row in sorted(useful_unmatched, key=lambda x: x["danbooru_tag"]):
            writer.writerow(row)

    print(f"Output written to: {OUTPUT_CSV}")


async def main() -> None:
    print("Loading WD-Tagger tags...")
    wd_tags = load_wd_tagger_tags()
    print(f"  Loaded {len(wd_tags)} general tags from WD-Tagger")

    print("\nLoading internal tags...")
    internal_tags = await load_internal_tags()
    print(f"  Loaded {len(internal_tags)} theme tags from database")

    print("\nFinding matches...")
    exact, partial, unmatched = find_matches(wd_tags, internal_tags)

    print(f"\nResults:")
    print(f"  Exact matches: {len(exact)}")
    print(f"  Partial matches (need review): {len(partial)}")
    print(f"  Unmatched WD-Tagger tags: {len(unmatched)}")

    print("\nExact matches found:")
    for match in sorted(exact, key=lambda x: x["danbooru_tag"])[:30]:
        print(f"  {match['danbooru_tag']:30} → {match['internal_title']}")
    if len(exact) > 30:
        print(f"  ... and {len(exact) - 30} more")

    print("\nWriting output CSV...")
    write_output(exact, partial, unmatched, internal_tags)

    print("\nDone! Review the CSV and:")
    print("  1. Check 'review' rows and set action to 'map' or 'ignore'")
    print("  2. For useful unmatched tags, manually find internal equivalents")
    print("  3. Run: uv run python scripts/import_tag_mappings.py data/tag_mappings_draft.csv")


if __name__ == "__main__":
    asyncio.run(main())
