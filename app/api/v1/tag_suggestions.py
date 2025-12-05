"""
Tag Suggestions API Endpoints

Provides endpoints for viewing and managing ML-generated tag suggestions.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.database import get_db
from app.core.permissions import Permission, has_permission
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_suggestion import TagSuggestion
from app.schemas.tag import TagResponse
from app.schemas.tag_suggestion import TagSuggestionResponse, TagSuggestionsListResponse

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
