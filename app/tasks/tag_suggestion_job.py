"""
Tag Suggestion Generation Background Job

Generates ML-based tag suggestions for newly uploaded images.
"""

from pathlib import Path as FilePath

from sqlalchemy import select

from app.config import settings
from app.core.database import get_async_session
from app.core.logging import bind_context, get_logger
from app.models.image import Images
from app.models.tag_link import TagLinks
from app.models.tag_suggestion import TagSuggestion
from app.services.tag_resolver import resolve_tag_relationships

logger = get_logger(__name__)

# Minimum confidence threshold for creating suggestions
MIN_CONFIDENCE_THRESHOLD = 0.6


async def generate_tag_suggestions(
    ctx: dict,
    image_id: int,
) -> dict[str, str | int]:
    """
    Generate ML tag suggestions for an image.

    This job is triggered after image upload completes. It:
    1. Fetches the image from the database
    2. Gets the image file path
    3. Calls the ML service to generate predictions
    4. Resolves tag relationships (aliases, hierarchies)
    5. Filters out tags already applied to the image
    6. Creates TagSuggestion records in the database

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
                select(Images).where(Images.image_id == image_id)
            )
            image = result.scalar_one_or_none()

            if not image:
                logger.error("tag_suggestion_job_image_not_found", image_id=image_id)
                return {"status": "error", "error": f"Image {image_id} not found"}

            # 2. Get image file path
            # Images are stored as: {STORAGE_PATH}/fullsize/{filename}.{ext}
            image_path = FilePath(settings.STORAGE_PATH) / "fullsize" / f"{image.filename}.{image.ext}"

            if not image_path.exists():
                logger.error(
                    "tag_suggestion_job_file_not_found",
                    image_id=image_id,
                    path=str(image_path)
                )
                return {
                    "status": "error",
                    "error": f"Image file not found: {image_path}"
                }

            logger.info(
                "tag_suggestion_job_started",
                image_id=image_id,
                path=str(image_path)
            )

            # 3. Call ML service to generate predictions
            ml_service = ctx["ml_service"]
            predictions = await ml_service.generate_suggestions(
                str(image_path),
                min_confidence=MIN_CONFIDENCE_THRESHOLD
            )

            logger.info(
                "tag_suggestion_job_predictions_generated",
                image_id=image_id,
                count=len(predictions)
            )

            # 4. Resolve tag relationships (aliases, hierarchies)
            resolved_predictions = await resolve_tag_relationships(db, predictions)

            logger.info(
                "tag_suggestion_job_tags_resolved",
                image_id=image_id,
                original_count=len(predictions),
                resolved_count=len(resolved_predictions)
            )

            # 5. Filter out tags already applied to the image
            # Batch fetch existing tag links to avoid N+1 queries
            tag_ids_from_predictions = {pred["tag_id"] for pred in resolved_predictions}

            existing_links_result = await db.execute(
                select(TagLinks.tag_id)
                .where(
                    TagLinks.image_id == image_id,
                    TagLinks.tag_id.in_(tag_ids_from_predictions)
                )
            )
            existing_tag_ids = {row[0] for row in existing_links_result.all()}

            logger.info(
                "tag_suggestion_job_existing_tags_found",
                image_id=image_id,
                existing_count=len(existing_tag_ids),
                existing_tags=list(existing_tag_ids)
            )

            # 6. Create TagSuggestion records
            suggestions_created = 0

            for pred in resolved_predictions:
                tag_id = pred["tag_id"]
                confidence = pred["confidence"]

                # Skip if tag is already applied to the image
                if tag_id in existing_tag_ids:
                    logger.debug(
                        "tag_suggestion_job_skipping_existing_tag",
                        image_id=image_id,
                        tag_id=tag_id
                    )
                    continue

                # Filter by confidence threshold (double-check, ML service should already filter)
                if confidence < MIN_CONFIDENCE_THRESHOLD:
                    logger.debug(
                        "tag_suggestion_job_skipping_low_confidence",
                        image_id=image_id,
                        tag_id=tag_id,
                        confidence=confidence
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
                suggestions_created=suggestions_created
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
