"""
Tag Mapping Service

Resolves external tags (from Danbooru/WD-Tagger) to internal tag IDs.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.tag_mapping import TagMapping

logger = get_logger(__name__)


async def resolve_external_tags(
    db: AsyncSession,
    suggestions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Resolve external tags to internal tag IDs.

    Args:
        db: Database session
        suggestions: List of dicts with keys:
            - external_tag: Danbooru tag name (e.g., "long_hair")
            - confidence: float 0-1
            - model_source: str
            - model_version: str

    Returns:
        List of resolved suggestions with keys:
            - tag_id: internal tag ID
            - confidence: float (may be adjusted by mapping confidence)
            - model_source: str
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
        select(TagMapping).where(
            TagMapping.external_tag.in_(external_tags),  # type: ignore[attr-defined]
            TagMapping.external_source == "danbooru",  # type: ignore[arg-type]
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
                "model_source": sugg["model_source"],
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
