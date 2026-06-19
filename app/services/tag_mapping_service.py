"""
Tag Mapping Service

Resolves external tags (from ML taggers) to internal tag IDs.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.tag_mapping import TagMappings

logger = get_logger(__name__)


def find_orphan_mappings(
    mapped_tag_names: set[str],
    vocab_tag_names: set[str],
) -> list[str]:
    """Return sorted danbooru tag names that are in our mappings but NOT in the vocab.

    This is a pure function — no DB or IO.

    Args:
        mapped_tag_names: Set of external tag names referenced by our curated mappings.
        vocab_tag_names: Set of tag names present in the model's selected_tags.csv vocab.

    Returns:
        Sorted list of tag names that are orphaned (in mappings but absent from vocab).
    """
    return sorted(mapped_tag_names - vocab_tag_names)


async def get_mapped_external_tag_names(db: AsyncSession) -> set[str]:
    """Fetch the set of external tag names referenced by any row in tag_mappings.

    Includes both real mappings (non-NULL internal_tag_id) and ignored rows
    (NULL internal_tag_id, i.e. "known but ignored").  Both kinds reference an
    external tag name that must still appear in the model's vocabulary — if the
    vocab drops a tag, the mapping row becomes stale regardless of whether it
    maps or ignores it.

    Args:
        db: Async database session.

    Returns:
        Set of external_tag strings from all tag_mappings rows.
    """
    result = await db.execute(select(TagMappings.external_tag))
    return {row[0] for row in result.all()}


async def resolve_external_tags(
    db: AsyncSession,
    suggestions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Resolve external tags to internal tag IDs.

    Args:
        db: Database session
        suggestions: List of dicts with keys:
            - external_tag: tagger vocabulary name (e.g., "long_hair")
            - confidence: float 0-1
            - model_version: str

    Returns:
        List of resolved suggestions with keys:
            - tag_id: internal tag ID
            - confidence: float (may be adjusted by mapping confidence)
            - model_version: str

        Tags without mappings or with NULL internal_tag_id are excluded.
    """
    # Collect unique external tags
    external_tags = {s["external_tag"] for s in suggestions if "external_tag" in s}

    if not external_tags:
        logger.debug("tag_mapping_no_external_tags")
        return []

    # Batch fetch all mappings
    result = await db.execute(
        select(TagMappings).where(
            TagMappings.external_tag.in_(external_tags),  # type: ignore[attr-defined]
        )
    )
    mappings = {m.external_tag: m for m in result.scalars().all()}

    resolved: list[dict[str, Any]] = []
    unmapped_tags: list[str] = []

    for sugg in suggestions:
        external_tag = sugg.get("external_tag")
        if not external_tag:
            continue

        mapping = mappings.get(external_tag)

        if not mapping:
            # No mapping exists - track for logging
            unmapped_tags.append(external_tag)
            continue

        if mapping.internal_tag_id is None:
            # Explicitly ignored tag (e.g., "1girl", "solo")
            continue

        # Create resolved suggestion
        resolved.append(
            {
                "tag_id": mapping.internal_tag_id,
                "confidence": sugg["confidence"] * mapping.confidence,
                "model_version": sugg.get("model_version", "unknown"),
            }
        )

    logger.info(
        "tag_mapping_resolved",
        total_suggestions=len(suggestions),
        resolved_count=len(resolved),
        ignored_count=len(suggestions) - len(resolved) - len(unmapped_tags),
        unmapped_count=len(unmapped_tags),
    )

    if unmapped_tags:
        # Log first few unmapped tags (useful for expanding mappings)
        logger.debug(
            "tag_mapping_unmapped_tags",
            unmapped_sample=unmapped_tags[:10],
            total_unmapped=len(unmapped_tags),
        )

    return resolved
