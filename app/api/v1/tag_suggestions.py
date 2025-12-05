"""
Tag Suggestions API Endpoints

Provides endpoints for viewing and managing ML-generated tag suggestions.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.database import get_db
from app.core.permissions import Permission, has_permission
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.tag_suggestion import TagSuggestion
from app.schemas.tag import TagResponse
from app.schemas.tag_suggestion import (
    ReviewSuggestionsRequest,
    ReviewSuggestionsResponse,
    TagSuggestionResponse,
    TagSuggestionsListResponse,
)

router = APIRouter(prefix="/images", tags=["tag-suggestions"])


@router.get("/{image_id}/tag-suggestions", response_model=TagSuggestionsListResponse)
async def get_tag_suggestions(
    image_id: int,
    status_filter: Annotated[
        Literal["pending", "approved", "rejected"] | None,
        Query(alias="status", description="Filter by suggestion status"),
    ] = None,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = ...,
) -> TagSuggestionsListResponse:
    """
    Get tag suggestions for an image.

    Permissions:
    - Image uploader can view their own suggestions
    - Moderators with IMAGE_TAG_ADD permission can view all suggestions

    Args:
        image_id: ID of the image
        status_filter: Optional filter by status (pending/approved/rejected)
        db: Database session
        current_user: Current authenticated user

    Returns:
        List of tag suggestions with counts by status

    Raises:
        HTTPException: 404 if image not found, 403 if permission denied
    """
    # Check if image exists
    result = await db.execute(select(Images).where(Images.image_id == image_id))
    image = result.scalar_one_or_none()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Check permissions: owner or moderator
    is_owner = image.user_id == current_user.user_id
    is_moderator = await has_permission(db, current_user.user_id, Permission.IMAGE_TAG_ADD)

    if not is_owner and not is_moderator:
        raise HTTPException(
            status_code=403,
            detail="You can only view suggestions for your own images",
        )

    # Build query for suggestions
    query = select(TagSuggestion).where(TagSuggestion.image_id == image_id)
    if status_filter:
        query = query.where(TagSuggestion.status == status_filter)

    result = await db.execute(query.order_by(TagSuggestion.confidence.desc()))
    suggestions = result.scalars().all()

    # Batch fetch all tags in one query to avoid N+1 problem
    tag_ids = [sugg.tag_id for sugg in suggestions]
    tag_result = await db.execute(select(Tags).where(Tags.tag_id.in_(tag_ids)))
    tags_by_id = {tag.tag_id: tag for tag in tag_result.scalars().all()}

    # Build suggestion responses
    suggestion_responses = [
        TagSuggestionResponse(
            suggestion_id=sugg.suggestion_id,
            tag=TagResponse.model_validate(tags_by_id[sugg.tag_id]),
            confidence=sugg.confidence,
            model_source=sugg.model_source,
            status=sugg.status,
            created_at=sugg.created_at,
            reviewed_at=sugg.reviewed_at,
        )
        for sugg in suggestions
    ]

    # Count by status (for all suggestions, not just filtered ones)
    status_counts_result = await db.execute(
        select(TagSuggestion.status, func.count(TagSuggestion.suggestion_id))
        .where(TagSuggestion.image_id == image_id)
        .group_by(TagSuggestion.status)
    )

    counts = {"pending": 0, "approved": 0, "rejected": 0}
    for status_val, count in status_counts_result:
        counts[status_val] = count

    return TagSuggestionsListResponse(
        image_id=image_id,
        suggestions=suggestion_responses,
        total=len(suggestion_responses),
        pending=counts["pending"],
        approved=counts["approved"],
        rejected=counts["rejected"],
    )


@router.post("/{image_id}/tag-suggestions/review", response_model=ReviewSuggestionsResponse)
async def review_tag_suggestions(
    image_id: int,
    request: ReviewSuggestionsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = ...,
) -> ReviewSuggestionsResponse:
    """
    Approve or reject tag suggestions.

    Approving a suggestion creates a TagLink (if it doesn't already exist)
    and updates the suggestion status. Rejecting only updates the status.

    Permissions:
    - Image uploader can review their own suggestions
    - Moderators with IMAGE_TAG_ADD permission can review all suggestions

    Args:
        image_id: ID of the image
        request: Batch of suggestion reviews (approve/reject actions)
        db: Database session
        current_user: Current authenticated user

    Returns:
        Counts of approved/rejected suggestions and any errors

    Raises:
        HTTPException: 404 if image not found, 403 if permission denied
    """
    # Check if image exists
    result = await db.execute(select(Images).where(Images.image_id == image_id))
    image = result.scalar_one_or_none()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Check permissions: owner or moderator
    is_owner = image.user_id == current_user.user_id
    is_moderator = await has_permission(db, current_user.user_id, Permission.IMAGE_TAG_ADD)

    if not is_owner and not is_moderator:
        raise HTTPException(
            status_code=403,
            detail="You can only review suggestions for your own images",
        )

    # Batch fetch all suggestions in one query to avoid N+1
    suggestion_ids = [item.suggestion_id for item in request.suggestions]
    result = await db.execute(
        select(TagSuggestion).where(
            TagSuggestion.suggestion_id.in_(suggestion_ids),
            TagSuggestion.image_id == image_id,
        )
    )
    suggestions_by_id = {sugg.suggestion_id: sugg for sugg in result.scalars().all()}

    # Batch fetch existing TagLinks to avoid creating duplicates
    tag_ids = [sugg.tag_id for sugg in suggestions_by_id.values()]
    result = await db.execute(
        select(TagLinks).where(
            TagLinks.image_id == image_id,
            TagLinks.tag_id.in_(tag_ids),
        )
    )
    existing_links = {(link.image_id, link.tag_id) for link in result.scalars().all()}

    approved_count = 0
    rejected_count = 0
    errors = []

    review_time = datetime.now(UTC)

    for review_item in request.suggestions:
        # Check if suggestion exists and belongs to this image
        suggestion = suggestions_by_id.get(review_item.suggestion_id)
        if not suggestion:
            errors.append(f"Suggestion {review_item.suggestion_id} not found")
            continue

        if review_item.action == "approve":
            # Create TagLink if it doesn't exist
            if (image_id, suggestion.tag_id) not in existing_links:
                tag_link = TagLinks(
                    image_id=image_id,
                    tag_id=suggestion.tag_id,
                    user_id=current_user.user_id,
                )
                db.add(tag_link)
                existing_links.add((image_id, suggestion.tag_id))

            # Update suggestion status
            suggestion.status = "approved"
            suggestion.reviewed_at = review_time
            suggestion.reviewed_by_user_id = current_user.user_id
            approved_count += 1

        elif review_item.action == "reject":
            # Update suggestion status only (no TagLink)
            suggestion.status = "rejected"
            suggestion.reviewed_at = review_time
            suggestion.reviewed_by_user_id = current_user.user_id
            rejected_count += 1

    await db.commit()

    return ReviewSuggestionsResponse(
        approved=approved_count,
        rejected=rejected_count,
        errors=errors,
    )
