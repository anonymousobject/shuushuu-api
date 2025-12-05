"""
Tag Relationship Resolver

Resolves tag aliases and hierarchies for ML suggestions.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tags

# Constants for hierarchy processing
HIERARCHY_CONFIDENCE_THRESHOLD = 0.7
PARENT_CONFIDENCE_MULTIPLIER = 0.9


async def resolve_tag_relationships(db: AsyncSession, suggestions: list[dict]) -> list[dict]:
    """
    Resolve tag aliases and hierarchies.

    - If tag is an alias, replace with canonical tag
    - Optionally add parent tags from hierarchy

    Args:
        db: Database session
        suggestions: List of dicts with keys: tag_id, confidence, model_source

    Returns:
        List of resolved suggestions (may be longer if parent tags added)
    """
    # CRITICAL FIX #1: Batch load all tags upfront to avoid N+1 queries
    # Collect all tag IDs we need to fetch
    tag_ids = {sugg["tag_id"] for sugg in suggestions}

    # Batch load all tags at once
    result = await db.execute(select(Tags).where(Tags.tag_id.in_(tag_ids)))
    tags_by_id = {tag.tag_id: tag for tag in result.scalars().all()}

    # Collect canonical tag IDs from aliases
    canonical_ids = set()
    for tag_id in tag_ids:
        tag = tags_by_id.get(tag_id)
        if tag and tag.alias:
            canonical_ids.add(tag.alias)

    # Fetch canonical tags if any aliases exist
    if canonical_ids:
        result = await db.execute(select(Tags).where(Tags.tag_id.in_(canonical_ids)))
        for tag in result.scalars().all():
            tags_by_id[tag.tag_id] = tag

    # CRITICAL FIX #2: Use dict to deduplicate by tag_id, keeping highest confidence
    resolved_dict = {}

    for sugg in suggestions:
        # IMPORTANT FIX #5: Initialize flags to False at start of loop
        resolved_from_alias = False
        from_hierarchy = False

        # Fetch tag from our batch-loaded dict
        tag = tags_by_id.get(sugg["tag_id"])

        if not tag:
            # Tag doesn't exist, skip
            continue

        # Make a copy to avoid modifying original
        sugg = sugg.copy()

        # Resolve alias
        if tag.alias:
            # This tag is an alias, use the canonical tag
            sugg["tag_id"] = tag.alias
            resolved_from_alias = True

            # CRITICAL FIX #3: Check if canonical tag exists
            tag = tags_by_id.get(tag.alias)
            if not tag:
                # Canonical tag doesn't exist, skip
                continue

        # Set the flag after successful resolution
        if resolved_from_alias:
            sugg["resolved_from_alias"] = True

        # Add or update in dict, keeping highest confidence
        tag_id = sugg["tag_id"]
        if tag_id not in resolved_dict or sugg["confidence"] > resolved_dict[tag_id]["confidence"]:
            resolved_dict[tag_id] = sugg

        # IMPORTANT FIX #4: Use constants instead of hardcoded values
        # Add parent tag if exists and confidence is high enough
        if tag and tag.inheritedfrom_id and sugg["confidence"] > HIERARCHY_CONFIDENCE_THRESHOLD:
            parent_sugg = sugg.copy()
            parent_sugg["tag_id"] = tag.inheritedfrom_id
            parent_sugg["confidence"] *= PARENT_CONFIDENCE_MULTIPLIER
            parent_sugg["from_hierarchy"] = True

            # Remove alias flag from parent (it's not from an alias)
            parent_sugg.pop("resolved_from_alias", None)

            # Add or update parent, keeping highest confidence
            parent_id = parent_sugg["tag_id"]
            if (
                parent_id not in resolved_dict
                or parent_sugg["confidence"] > resolved_dict[parent_id]["confidence"]
            ):
                resolved_dict[parent_id] = parent_sugg

    return list(resolved_dict.values())
