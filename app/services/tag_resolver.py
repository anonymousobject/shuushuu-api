"""
Tag Relationship Resolver

Resolves tag aliases and hierarchies for ML suggestions.
"""

from typing import List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.tag import Tags


async def resolve_tag_relationships(
    db: AsyncSession,
    suggestions: List[Dict]
) -> List[Dict]:
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
    resolved = []

    for sugg in suggestions:
        # Fetch tag to check alias and hierarchy
        result = await db.execute(
            select(Tags).where(Tags.tag_id == sugg["tag_id"])
        )
        tag = result.scalar_one_or_none()

        if not tag:
            # Tag doesn't exist, skip
            continue

        # Resolve alias
        if tag.alias:
            # This tag is an alias, use the canonical tag
            sugg = sugg.copy()  # Don't modify original
            sugg["tag_id"] = tag.alias
            sugg["resolved_from_alias"] = True

            # Update tag reference for hierarchy check
            result = await db.execute(
                select(Tags).where(Tags.tag_id == tag.alias)
            )
            tag = result.scalar_one_or_none()

        resolved.append(sugg)

        # Add parent tag if exists and confidence is high enough
        if tag and tag.inheritedfrom_id and sugg["confidence"] > 0.7:
            parent_sugg = sugg.copy()
            parent_sugg["tag_id"] = tag.inheritedfrom_id
            parent_sugg["confidence"] *= 0.9  # Reduce confidence slightly
            parent_sugg["from_hierarchy"] = True
            resolved.append(parent_sugg)

    return resolved
