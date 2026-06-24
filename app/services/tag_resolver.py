"""
Tag Relationship Resolver

Resolves tag aliases for ML suggestions.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tags


async def resolve_tag_relationships(
    db: AsyncSession, suggestions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    Resolve tag aliases.

    - If tag is an alias, replace with canonical tag

    Args:
        db: Database session
        suggestions: List of dicts with keys: tag_id, confidence, model_version

    Returns:
        List of resolved suggestions (deduped by tag_id, keeping highest confidence)
    """
    # Nothing to resolve — skip the guaranteed-empty tag query (mirrors
    # resolve_external_tags' early return when there are no inputs).
    if not suggestions:
        return []

    # Batch load all tags upfront to avoid N+1 queries
    tag_ids = {sugg["tag_id"] for sugg in suggestions}

    result = await db.execute(
        select(Tags).where(Tags.tag_id.in_(tag_ids))  # type: ignore[union-attr]
    )
    tags_by_id = {tag.tag_id: tag for tag in result.scalars().all()}

    # Collect canonical tag IDs from aliases
    canonical_ids = set()
    for tag_id in tag_ids:
        tag = tags_by_id.get(tag_id)
        if tag and tag.alias_of:
            canonical_ids.add(tag.alias_of)

    # Fetch canonical tags if any aliases exist
    if canonical_ids:
        result = await db.execute(
            select(Tags).where(Tags.tag_id.in_(canonical_ids))  # type: ignore[union-attr]
        )
        for tag in result.scalars().all():
            tags_by_id[tag.tag_id] = tag

    # Use dict to deduplicate by tag_id, keeping highest confidence
    resolved_dict: dict[int, dict[str, Any]] = {}

    for sugg in suggestions:
        resolved_from_alias = False

        tag = tags_by_id.get(sugg["tag_id"])

        if not tag:
            # Tag doesn't exist, skip
            continue

        # Make a copy to avoid modifying original
        sugg = sugg.copy()

        # Resolve alias
        if tag.alias_of:
            # This tag is an alias, use the canonical tag
            sugg["tag_id"] = tag.alias_of
            resolved_from_alias = True

            tag = tags_by_id.get(tag.alias_of)
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

    return list(resolved_dict.values())
