"""Tag alias resolution and hierarchy expansion, shared by the tag, image and feed endpoints."""

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tags


async def resolve_tag_alias(
    db: AsyncSession, tag_id: int, tag: Tags | None = None
) -> tuple[Tags | None, int]:
    """
    Resolve a tag alias to its actual tag.

    Aliases are synonyms - if a tag has an 'alias' field pointing to another tag,
    it means they're the same concept (e.g., "collar" -> "choker").

    Args:
        db: Database session
        tag_id: ID of the tag to resolve
        tag: Optional pre-fetched tag object to avoid duplicate queries

    Returns:
        tuple: (tag_object, resolved_tag_id)
            - tag_object: The original tag object (or None if not found)
            - resolved_tag_id: The actual tag ID if this is an alias, otherwise the original tag_id
    """
    # Use provided tag object if available, otherwise fetch it
    if tag is None:
        tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
        tag = tag_result.scalar_one_or_none()

    if not tag:
        return None, tag_id

    # If this tag is an alias, use the actual tag for queries
    if tag.alias_of:
        return tag, tag.alias_of

    return tag, tag_id


async def get_tag_hierarchy(db: AsyncSession, tag_id: int, max_depth: int = 10) -> list[int]:
    """
    Get all tag IDs in a tag's hierarchy (self + all descendants).

    For parent tags (like "swimsuit"), this returns the parent ID plus all child tag IDs
    (like "school swimsuit", "bikini") that have inheritedfrom_id pointing to it.
    This allows querying a parent tag to include all images tagged with child tags.

    Uses a recursive CTE for single-query performance instead of N+1 queries.

    Args:
        db: Database session
        tag_id: The root tag ID to get hierarchy for
        max_depth: Maximum depth to traverse (default 10, safety limit)

    Returns:
        list[int]: List of tag IDs including the parent and all descendants
    """
    query = text("""
        WITH RECURSIVE tag_tree AS (
            SELECT tag_id, 1 as depth
            FROM tags
            WHERE tag_id = :root_id
            UNION ALL
            SELECT t.tag_id, tt.depth + 1
            FROM tags t
            INNER JOIN tag_tree tt ON t.inheritedfrom_id = tt.tag_id
            WHERE tt.depth < :max_depth
        )
        SELECT tag_id FROM tag_tree
    """)
    result = await db.execute(query, {"root_id": tag_id, "max_depth": max_depth})
    return [row[0] for row in result.fetchall()]
