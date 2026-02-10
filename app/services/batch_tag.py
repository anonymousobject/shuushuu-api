"""Batch tag operations service."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_history import TagHistory
from app.models.tag_link import TagLinks
from app.schemas.tag import (
    BatchTagResponse,
    BatchTagResultItem,
    BatchTagSkippedItem,
)

logger = get_logger(__name__)


async def batch_add_tags(
    tag_ids: list[int],
    image_ids: list[int],
    user_id: int,
    db: AsyncSession,
) -> BatchTagResponse:
    """
    Add multiple tags to multiple images, skipping invalid or duplicate pairs.

    Returns a response listing which pairs were added and which were skipped.
    """
    added: list[BatchTagResultItem] = []
    skipped: list[BatchTagSkippedItem] = []

    # 1. Resolve tags: fetch all, resolve aliases, collect missing
    resolved_tags: dict[int, int | None] = {}  # original_tag_id -> resolved_tag_id or None
    for tag_id in tag_ids:
        tag_result = await db.execute(
            select(Tags).where(Tags.tag_id == tag_id)  # type: ignore[arg-type]
        )
        tag = tag_result.scalar_one_or_none()
        if not tag:
            resolved_tags[tag_id] = None
            continue
        # Resolve alias inline (avoids importing from route layer)
        resolved_tags[tag_id] = tag.alias_of if tag.alias_of else tag_id

    missing_tag_ids = {tid for tid, rid in resolved_tags.items() if rid is None}

    # 2. Fetch existing images in one query
    valid_resolved_tag_ids = {rid for rid in resolved_tags.values() if rid is not None}
    existing_image_result = await db.execute(
        select(Images.image_id).where(  # type: ignore[call-overload]
            Images.image_id.in_(image_ids)  # type: ignore[union-attr]
        )
    )
    existing_image_ids = {row[0] for row in existing_image_result.all()}

    # 3. Fetch existing tag links in one query
    existing_links: set[tuple[int, int]] = set()
    if existing_image_ids and valid_resolved_tag_ids:
        links_result = await db.execute(
            select(TagLinks.image_id, TagLinks.tag_id).where(  # type: ignore[call-overload]
                TagLinks.image_id.in_(existing_image_ids),  # type: ignore[attr-defined]
                TagLinks.tag_id.in_(valid_resolved_tag_ids),  # type: ignore[attr-defined]
            )
        )
        existing_links = {(row[0], row[1]) for row in links_result.all()}

    # 4. Process each image-tag pair
    for image_id in image_ids:
        for original_tag_id in tag_ids:
            resolved_tag_id = resolved_tags[original_tag_id]

            if original_tag_id in missing_tag_ids:
                skipped.append(
                    BatchTagSkippedItem(
                        image_id=image_id,
                        tag_id=original_tag_id,
                        reason="tag_not_found",
                    )
                )
                continue

            assert resolved_tag_id is not None  # guaranteed by missing_tag_ids check

            if image_id not in existing_image_ids:
                skipped.append(
                    BatchTagSkippedItem(
                        image_id=image_id,
                        tag_id=resolved_tag_id,
                        reason="image_not_found",
                    )
                )
                continue

            if (image_id, resolved_tag_id) in existing_links:
                skipped.append(
                    BatchTagSkippedItem(
                        image_id=image_id,
                        tag_id=resolved_tag_id,
                        reason="already_tagged",
                    )
                )
                continue

            db.add(
                TagLinks(
                    image_id=image_id,
                    tag_id=resolved_tag_id,
                    user_id=user_id,
                )
            )

            db.add(
                TagHistory(
                    image_id=image_id,
                    tag_id=resolved_tag_id,
                    action="a",
                    user_id=user_id,
                )
            )

            added.append(
                BatchTagResultItem(
                    image_id=image_id,
                    tag_id=resolved_tag_id,
                )
            )

            # Track as existing to prevent duplicates within same batch
            existing_links.add((image_id, resolved_tag_id))

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.warning(
            "batch_tag_integrity_error", user_id=user_id, tag_ids=tag_ids, image_ids=image_ids
        )
        raise

    return BatchTagResponse(added=added, skipped=skipped)
