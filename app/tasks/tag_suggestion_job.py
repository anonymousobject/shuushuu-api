"""
Tag Suggestion Generation Background Job

Generates ML-based tag suggestions for newly uploaded images.
"""

from pathlib import Path as FilePath
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_async_session
from app.core.logging import bind_context, get_logger
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.tag_suggestion import TagSuggestion
from app.services.tag_mapping_service import resolve_external_tags
from app.services.tag_resolver import resolve_tag_relationships

logger = get_logger(__name__)

# Minimum confidence threshold for creating suggestions
MIN_CONFIDENCE_THRESHOLD = 0.6


async def filter_redundant_suggestions(
    db: AsyncSession,
    suggestions: list[dict[str, Any]],
    existing_tag_ids: set[int],
) -> list[dict[str, Any]]:
    """
    Filter out suggestions that are redundant given existing tags.

    A suggestion is considered redundant if:
    1. It's an ancestor (parent, grandparent, etc.) of an existing tag
    2. Its title is a substring of an existing tag's title (e.g., "kimono" in "short kimono")

    Args:
        db: Database session
        suggestions: List of suggestion dicts with tag_id
        existing_tag_ids: Set of tag IDs already on the image

    Returns:
        Filtered list of suggestions
    """
    if not existing_tag_ids or not suggestions:
        return suggestions

    # Fetch existing tags with their details
    existing_result = await db.execute(
        select(Tags).where(Tags.tag_id.in_(existing_tag_ids))  # type: ignore[union-attr]
    )
    existing_tags = list(existing_result.scalars().all())

    # Build set of all ancestor tag IDs for existing tags
    ancestor_ids: set[int] = set()
    tags_to_check = [t.inheritedfrom_id for t in existing_tags if t.inheritedfrom_id]

    # Walk up the inheritance chain (with depth limit to prevent infinite loops)
    checked_ids: set[int] = set()
    for _ in range(10):  # Max depth of 10
        if not tags_to_check:
            break

        # Fetch parent tags
        parent_result = await db.execute(
            select(Tags).where(Tags.tag_id.in_(tags_to_check))  # type: ignore[union-attr]
        )
        parent_tags = list(parent_result.scalars().all())

        for parent in parent_tags:
            if parent.tag_id is None or parent.tag_id in checked_ids:
                continue
            checked_ids.add(parent.tag_id)
            ancestor_ids.add(parent.tag_id)
            if parent.inheritedfrom_id:
                tags_to_check.append(parent.inheritedfrom_id)

        tags_to_check = [t for t in tags_to_check if t not in checked_ids]

    # Build set of existing tag titles (lowercase for comparison)
    existing_titles = {(t.title or "").lower() for t in existing_tags}

    # Fetch suggested tags to get their titles
    suggested_tag_ids = [s["tag_id"] for s in suggestions]
    suggested_result = await db.execute(
        select(Tags).where(Tags.tag_id.in_(suggested_tag_ids))  # type: ignore[union-attr]
    )
    suggested_tags_by_id = {t.tag_id: t for t in suggested_result.scalars().all()}

    # Filter suggestions
    filtered: list[dict[str, Any]] = []
    for sugg in suggestions:
        tag_id = sugg["tag_id"]
        tag = suggested_tags_by_id.get(tag_id)

        # Skip if this is an ancestor of an existing tag
        if tag_id in ancestor_ids:
            logger.debug(
                "filter_redundant_skipping_ancestor",
                tag_id=tag_id,
                tag_title=tag.title if tag else None,
            )
            continue

        # Skip if tag title is contained in an existing tag's title
        if tag and tag.title:
            tag_title_lower = tag.title.lower()
            # Check if this tag's title is a substring of any existing tag
            # Only filter if it's a proper substring (not exact match)
            is_substring = any(
                tag_title_lower in existing_title and tag_title_lower != existing_title
                for existing_title in existing_titles
            )
            if is_substring:
                logger.debug(
                    "filter_redundant_skipping_substring",
                    tag_id=tag_id,
                    tag_title=tag.title,
                )
                continue

        filtered.append(sugg)

    if len(filtered) < len(suggestions):
        logger.info(
            "filter_redundant_removed",
            original_count=len(suggestions),
            filtered_count=len(filtered),
            removed_count=len(suggestions) - len(filtered),
        )

    return filtered


async def generate_tag_suggestions(
    ctx: dict[str, Any],
    image_id: int,
) -> dict[str, str | int]:
    """
    Generate ML tag suggestions for an image.

    This job is triggered after image upload completes. It:
    1. Fetches the image from the database
    2. Gets the image file path
    3. Calls the ML service to generate predictions (Danbooru tags)
    4. Resolves external tags to internal tag IDs via tag mappings
    5. Resolves tag relationships (aliases, hierarchies)
    6. Filters out tags already applied to the image
    7. Creates TagSuggestion records in the database

    Args:
        ctx: ARQ context dict (contains ml_service)
        image_id: ID of image to generate suggestions for

    Returns:
        dict with status and suggestions_created count, or error info

    Note:
        This job handles errors gracefully and logs them without raising
        exceptions, so it won't fail the entire worker queue.
    """
    bind_context(task="tag_suggestion_generation", image_id=image_id)

    try:
        async with get_async_session() as db:
            # 1. Fetch image from database
            result = await db.execute(
                select(Images).where(Images.image_id == image_id)  # type: ignore[arg-type]
            )
            image = result.scalar_one_or_none()

            if not image:
                logger.error("tag_suggestion_job_image_not_found", image_id=image_id)
                return {"status": "error", "error": f"Image {image_id} not found"}

            # 2. Get image file path
            # Images are stored as: {STORAGE_PATH}/fullsize/{filename}.{ext}
            image_path = (
                FilePath(settings.STORAGE_PATH) / "fullsize" / f"{image.filename}.{image.ext}"
            )

            if not image_path.exists():
                logger.error(
                    "tag_suggestion_job_file_not_found", image_id=image_id, path=str(image_path)
                )
                return {"status": "error", "error": f"Image file not found: {image_path}"}

            logger.info("tag_suggestion_job_started", image_id=image_id, path=str(image_path))

            # 3. Call ML service to generate predictions
            ml_service = ctx["ml_service"]
            predictions = await ml_service.generate_suggestions(
                str(image_path), min_confidence=MIN_CONFIDENCE_THRESHOLD
            )

            logger.info(
                "tag_suggestion_job_predictions_generated",
                image_id=image_id,
                count=len(predictions),
            )

            # 4. Resolve external tags (Danbooru) to internal tag IDs
            mapped_predictions = await resolve_external_tags(db, predictions)

            logger.info(
                "tag_suggestion_job_tags_mapped",
                image_id=image_id,
                original_count=len(predictions),
                mapped_count=len(mapped_predictions),
            )

            # 5. Resolve tag relationships (aliases, hierarchies)
            resolved_predictions = await resolve_tag_relationships(db, mapped_predictions)

            logger.info(
                "tag_suggestion_job_tags_resolved",
                image_id=image_id,
                mapped_count=len(mapped_predictions),
                resolved_count=len(resolved_predictions),
            )

            # 6. Get ALL existing tags on the image (for redundancy filtering)
            all_existing_links_result = await db.execute(
                select(TagLinks.tag_id).where(  # type: ignore[call-overload]
                    TagLinks.image_id == image_id,
                )
            )
            all_existing_tag_ids = {row[0] for row in all_existing_links_result.all()}

            logger.info(
                "tag_suggestion_job_existing_tags_found",
                image_id=image_id,
                existing_count=len(all_existing_tag_ids),
            )

            # 7. Filter out redundant suggestions (ancestors or substrings of existing tags)
            filtered_predictions = await filter_redundant_suggestions(
                db, resolved_predictions, all_existing_tag_ids
            )

            # 8. Create TagSuggestion records
            suggestions_created = 0

            for pred in filtered_predictions:
                tag_id = pred["tag_id"]
                confidence = pred["confidence"]

                # Skip if tag is already applied to the image
                if tag_id in all_existing_tag_ids:
                    logger.debug(
                        "tag_suggestion_job_skipping_existing_tag", image_id=image_id, tag_id=tag_id
                    )
                    continue

                # Filter by confidence threshold (double-check, ML service should already filter)
                if confidence < MIN_CONFIDENCE_THRESHOLD:
                    logger.debug(
                        "tag_suggestion_job_skipping_low_confidence",
                        image_id=image_id,
                        tag_id=tag_id,
                        confidence=confidence,
                    )
                    continue

                # Create suggestion
                suggestion = TagSuggestion(
                    image_id=image_id,
                    tag_id=tag_id,
                    confidence=confidence,
                    model_source=pred["model_source"],
                    model_version=pred.get("model_version", "v1"),
                    status="pending",
                )
                db.add(suggestion)
                suggestions_created += 1

            # Commit all suggestions
            await db.commit()

            logger.info(
                "tag_suggestion_job_completed",
                image_id=image_id,
                suggestions_created=suggestions_created,
            )

            return {
                "status": "completed",
                "suggestions_created": suggestions_created,
            }

    except Exception as e:
        logger.error(
            "tag_suggestion_job_failed",
            image_id=image_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        # Don't raise - job should handle errors gracefully
        return {
            "status": "error",
            "error": str(e),
        }
