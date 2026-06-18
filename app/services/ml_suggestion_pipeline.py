"""
Shared ML tag suggestion pipeline.

Single source of truth for turning an image into pending MlTagSuggestions
rows. Both the arq job (app/tasks/ml_tag_suggestion_job.py) and the API's
synchronous generate endpoint call generate_and_store_suggestions — the
generation logic is not duplicated.
"""

from pathlib import Path as FilePath
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

if TYPE_CHECKING:
    from app.services.ml_service import MLTagSuggestionService
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.services.tag_mapping_service import resolve_external_tags
from app.services.tag_resolver import resolve_tag_relationships

logger = get_logger(__name__)


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


async def generate_and_store_suggestions(
    db: AsyncSession,
    image: Images,
    ml_service: MLTagSuggestionService,
) -> int:
    """
    Run ML inference for an image and store pending MlTagSuggestions rows.

    Pipeline: predict (external tags) → map to internal tag IDs → resolve
    aliases/hierarchy → drop redundant vs existing tags → upsert suggestions.
    Existing suggestions are kept (idempotent regeneration); approved
    suggestions whose tag was since removed from the image reset to pending.

    Returns the number of new suggestions created.
    Raises FileNotFoundError if the local image file is missing.
    """
    assert image.image_id is not None  # a persisted image always has an id
    image_id = image.image_id

    # Resolve the local image path. Images are stored as:
    #   {STORAGE_PATH}/fullsize/{filename}.{ext}
    image_path = FilePath(settings.STORAGE_PATH) / "fullsize" / f"{image.filename}.{image.ext}"

    if not image_path.exists():
        logger.error(
            "ml_suggestion_pipeline_file_not_found", image_id=image_id, path=str(image_path)
        )
        raise FileNotFoundError(f"Image file not found: {image_path}")

    logger.info("ml_suggestion_pipeline_started", image_id=image_id, path=str(image_path))

    # Run inference for external (Danbooru) tag predictions, then hand off to the
    # DB half. store_predictions is shared with the offline bulk-ingest path,
    # which supplies predictions computed on another host.
    predictions = await ml_service.generate_suggestions(
        str(image_path), min_confidence=settings.ML_MIN_CONFIDENCE
    )

    logger.info(
        "ml_suggestion_pipeline_predictions_generated",
        image_id=image_id,
        count=len(predictions),
    )

    return await store_predictions(db, image_id, predictions)


async def compute_implied_suggestions(
    db: AsyncSession, image_id: int, predictions: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], set[int]]:
    """Map raw external predictions to the internal suggestions implied by the
    current state of the image.

    Pipeline: resolve external tags -> resolve aliases/hierarchy -> filter
    ancestors/substrings of applied tags -> exclude already-applied -> exclude
    below ML_MIN_CONFIDENCE.

    Queries TagLinks to determine which tags are currently applied to the image,
    so the result is image-state-dependent (calling twice may differ for the same
    predictions if the image's tags changed in between). Returns
    ``(implied, applied)`` so the caller can reuse ``applied`` without a second
    query (e.g. to reset approved suggestions whose tag was since removed).
    """
    # 1. Resolve external tags (Danbooru) to internal tag IDs.
    mapped = await resolve_external_tags(db, predictions)

    logger.info(
        "ml_suggestion_pipeline_tags_mapped",
        image_id=image_id,
        original_count=len(predictions),
        mapped_count=len(mapped),
    )

    # 2. Resolve tag relationships (aliases, hierarchies).
    resolved = await resolve_tag_relationships(db, mapped)

    logger.info(
        "ml_suggestion_pipeline_tags_resolved",
        image_id=image_id,
        mapped_count=len(mapped),
        resolved_count=len(resolved),
    )

    # 3. Get ALL existing tags on the image (for redundancy filtering).
    applied = {
        row[0]
        for row in (
            await db.execute(
                select(TagLinks.tag_id).where(  # type: ignore[call-overload]
                    TagLinks.image_id == image_id,
                )
            )
        ).all()
    }

    logger.info(
        "ml_suggestion_pipeline_existing_tags_found",
        image_id=image_id,
        existing_count=len(applied),
    )

    # 4. Filter out redundant suggestions (ancestors or substrings of existing tags).
    filtered = await filter_redundant_suggestions(db, resolved, applied)

    # 5. Drop tags already applied to the image and those below the confidence threshold.
    implied = [
        p
        for p in filtered
        if p["tag_id"] not in applied and p["confidence"] >= settings.ML_MIN_CONFIDENCE
    ]

    return implied, applied


async def store_predictions(
    db: AsyncSession,
    image_id: int,
    predictions: list[dict[str, Any]],
) -> int:
    """
    Map/resolve external-tag predictions and store pending MlTagSuggestions rows.

    This is the database half of the pipeline, independent of how the
    predictions were produced (live inference or an offline GPU backfill).
    ``predictions`` are external-tag dicts (external_tag, confidence,
    model_version) as returned by MLTagSuggestionService.generate_suggestions.

    Existing suggestions are kept (idempotent regeneration); approved
    suggestions whose tag was since removed from the image reset to pending.
    Commits the session. Returns the number of new suggestions created.
    """
    implied, applied = await compute_implied_suggestions(db, image_id, predictions)

    # 5. Get existing suggestions for this image (to avoid duplicate key errors on regenerate).
    existing_suggestions_result = await db.execute(
        select(MlTagSuggestions).where(
            MlTagSuggestions.image_id == image_id,  # type: ignore[arg-type]
        )
    )
    existing_suggestions = list(existing_suggestions_result.scalars().all())
    existing_suggestion_tag_ids = {s.tag_id for s in existing_suggestions}

    if existing_suggestion_tag_ids:
        logger.info(
            "ml_suggestion_pipeline_existing_suggestions_found",
            image_id=image_id,
            count=len(existing_suggestion_tag_ids),
        )

    # 6. Reset "approved" suggestions back to "pending" if their tag was removed.
    suggestions_reset = 0
    for suggestion in existing_suggestions:
        if suggestion.status == "approved" and suggestion.tag_id not in applied:
            suggestion.status = "pending"
            suggestion.reviewed_at = None
            suggestion.reviewed_by_user_id = None
            suggestions_reset += 1
            logger.debug(
                "ml_suggestion_pipeline_reset_removed_tag",
                image_id=image_id,
                tag_id=suggestion.tag_id,
            )

    if suggestions_reset:
        logger.info(
            "ml_suggestion_pipeline_suggestions_reset",
            image_id=image_id,
            count=suggestions_reset,
        )

    # 7. Create MlTagSuggestions records.
    suggestions_created = 0

    for pred in implied:
        tag_id = pred["tag_id"]
        confidence = pred["confidence"]

        # Skip if suggestion already exists for this tag (from previous generation).
        if tag_id in existing_suggestion_tag_ids:
            logger.debug(
                "ml_suggestion_pipeline_skipping_existing_suggestion",
                image_id=image_id,
                tag_id=tag_id,
            )
            continue

        suggestion = MlTagSuggestions(
            image_id=image_id,
            tag_id=tag_id,
            confidence=confidence,
            model_version=pred["model_version"],
            status="pending",
        )
        db.add(suggestion)
        suggestions_created += 1

    # Commit all suggestions (same transactional shape as the original job).
    await db.commit()

    logger.info(
        "ml_suggestion_pipeline_completed",
        image_id=image_id,
        suggestions_created=suggestions_created,
    )

    return suggestions_created
