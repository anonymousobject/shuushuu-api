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


def load_wd_tagger_tags() -> dict[str, dict[str, object]]:
    """Load WD-Tagger tags from CSV, return dict by normalized name."""
    if not WD_TAGGER_TAGS.exists():
        raise SystemExit(
            f"Missing model file: {WD_TAGGER_TAGS}\n"
            "Download the model first — see ml_models/wd-swinv2-tagger-v3/README.md"
        )
    tags: dict[str, dict[str, object]] = {}
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


def build_internal_tag_index(
    rows: list[tuple[int, str, int]],
) -> dict[str, dict[str, object]]:
    """Index internal tags by normalized title, keeping a deterministic winner
    on collision.

    Two internal tags whose titles normalize identically (normalize_tag strips
    all non-[a-z0-9\\s] chars WITHOUT inserting a space, so punctuation just
    vanishes — e.g. "Re:Zero" and "ReZero" both become "rezero") would
    otherwise silently overwrite each other, so the draft CSV would map to
    whichever DB row happened to be indexed last. `rows` must be pre-ordered
    (e.g. by tag_id) so "first seen" is deterministic; the first row wins and
    every collision is reported via a loud warning so a human curator notices
    it — this script only produces a human-reviewed draft CSV, so a warning
    is the right altitude, not a hard failure.
    """
    tags: dict[str, dict[str, object]] = {}
    collisions: dict[str, list[str]] = {}
    for tag_id, title, tag_type in rows:
        normalized = normalize_tag(title)
        if normalized in tags:
            winner = tags[normalized]
            collisions.setdefault(
                normalized, [f"{winner['title']!r} (id={winner['tag_id']})"]
            ).append(f"{title!r} (id={tag_id})")
            continue  # keep the first-seen winner
        tags[normalized] = {
            "tag_id": tag_id,
            "title": title,
            "type": tag_type,
        }
    for normalized, entries in collisions.items():
        print(
            f"WARNING: tag titles collide after normalization ({normalized!r}): "
            f"{', '.join(entries)} -- keeping {entries[0]}"
        )
    return tags


async def load_internal_tags() -> dict[str, dict[str, object]]:
    """Load internal theme tags (type=1) from database."""
    async with get_async_session() as db:
        result = await db.execute(
            select(Tags.tag_id, Tags.title, Tags.type)  # type: ignore[call-overload]
            .where(Tags.type == 1)
            .order_by(Tags.tag_id)  # deterministic first-seen for collision handling
        )
        rows = [(tag_id, title, tag_type) for tag_id, title, tag_type in result.all()]
        return build_internal_tag_index(rows)


def find_matches(
    wd_tags: dict[str, dict[str, object]],
    internal_tags: dict[str, dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    """
    Find matches between WD-Tagger and internal tags.

    Returns:
        - exact_matches: Tags with exact normalized name match
        - partial_matches: Tags with partial/fuzzy match (for review)
        - unmatched_wd: WD-Tagger tags with no match
    """
    exact_matches: list[dict[str, object]] = []
    partial_matches: list[dict[str, object]] = []
    unmatched_wd: list[dict[str, object]] = []

    # Track which internal tags were matched
    matched_internal: set[str] = set()

    for wd_normalized, wd_data in wd_tags.items():
        # Try exact match
        if wd_normalized in internal_tags:
            internal = internal_tags[wd_normalized]
            exact_matches.append({
                "danbooru_tag": wd_data["original"],
                "internal_tag_id": internal["tag_id"],
                "internal_tag_title": internal["title"],
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
                    "internal_tag_title": int_data["title"],
                    "match_type": "partial",
                    "action": "review",
                })
                found_partial = True
                break

        if not found_partial:
            unmatched_wd.append({
                "danbooru_tag": wd_data["original"],
                "internal_tag_id": "",
                "internal_tag_title": "",
                "match_type": "none",
                "action": "ignore",  # Default to ignore, can be changed
            })

    return exact_matches, partial_matches, unmatched_wd


def write_output(
    exact_matches: list[dict[str, object]],
    partial_matches: list[dict[str, object]],
    unmatched_wd: list[dict[str, object]],
) -> None:
    """Write results to CSV for review."""
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "danbooru_tag",
                "internal_tag_id",
                "internal_tag_title",
                "match_type",
                "action",
            ],
        )
        writer.writeheader()

        # Write exact matches first
        for row in sorted(exact_matches, key=lambda x: str(x["danbooru_tag"])):
            writer.writerow(row)

        # Then partial matches for review
        for row in sorted(partial_matches, key=lambda x: str(x["danbooru_tag"])):
            writer.writerow(row)

        # Then unmatched (limited to common/useful tags)
        # Only include tags that might be worth mapping later
        useful_unmatched = [
            row
            for row in unmatched_wd
            if not any(
                skip in str(row["danbooru_tag"])
                for skip in [
                    "1girl",
                    "1boy",
                    "solo",
                    "multiple_",
                    "2girls",
                    "2boys",
                    "_focus",
                    "_background",
                    "simple_background",
                ]
            )
        ][:200]  # Limit to 200 most common
        for row in sorted(useful_unmatched, key=lambda x: str(x["danbooru_tag"])):
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

    print("\nResults:")
    print(f"  Exact matches: {len(exact)}")
    print(f"  Partial matches (need review): {len(partial)}")
    print(f"  Unmatched WD-Tagger tags: {len(unmatched)}")

    print("\nExact matches found:")
    for match in sorted(exact, key=lambda x: str(x["danbooru_tag"]))[:30]:
        print(f"  {str(match['danbooru_tag']):30} → {match['internal_tag_title']}")
    if len(exact) > 30:
        print(f"  ... and {len(exact) - 30} more")

    print("\nWriting output CSV...")
    write_output(exact, partial, unmatched)

    print("\nDone! Review the CSV and:")
    print("  1. Check 'review' rows and set action to 'map' or 'ignore'")
    print("  2. For useful unmatched tags, manually find internal equivalents")
    print("  3. Run: uv run python scripts/import_tag_mappings.py data/tag_mappings_draft.csv")


if __name__ == "__main__":
    asyncio.run(main())
