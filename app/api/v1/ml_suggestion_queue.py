"""Cross-image ML suggestion review queue API endpoints.

Provides a worklist (pending counts per tag), a per-tag paginated grid of
pending suggestions, and a cross-image bulk approve/reject endpoint, so
taggers / mods / admins can clear the ML suggestion backlog efficiently.

All endpoints are gated by ``require_image_tag_add`` (admin OR IMAGE_TAG_ADD).
The per-image review router lives in ``app/api/v1/ml_tag_suggestions.py``.
"""

import json
import logging
from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.permission_deps import require_image_tag_add
from app.core.redis import get_redis
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.schemas.image import ImageResponse, TagSummary
from app.schemas.ml_suggestion_queue import (
    BulkReviewItem,
    BulkReviewResult,
    SuggestionGridItem,
    SuggestionGridResponse,
    SuggestionTagWorklistItem,
    SuggestionTagWorklistResponse,
)
from app.services.ml_suggestion_queue import count_pending_by_tag, list_pending_for_tag
from app.services.ml_suggestion_review import bulk_review_suggestions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ml-suggestions", tags=["ml-suggestions"])

_WORKLIST_CACHE_PREFIX = "ml_suggestions:worklist:"
_WORKLIST_CACHE_TTL = 60  # seconds


@router.get(
    "/tags",
    response_model=SuggestionTagWorklistResponse,
    response_description="Pending suggestion counts grouped by tag (worklist)",
)
async def get_suggestion_worklist(
    _user: Annotated[Users, Depends(require_image_tag_add)],
    type: Annotated[
        int | None,
        Query(description="Filter to tags of this type (e.g. 4 = character)"),
    ] = None,
    min_confidence: Annotated[
        float,
        Query(description="Only count suggestions at or above this confidence"),
    ] = 0.0,
    page: Annotated[
        int,
        Query(ge=1, description="1-based page number"),
    ] = 1,
    per_page: Annotated[
        int,
        Query(ge=1, le=200, description="Number of tags per page"),
    ] = 50,
    search: Annotated[
        str | None,
        Query(description="Filter to tags whose title contains this string (case-insensitive)"),
    ] = None,
    db: AsyncSession = Depends(get_db),
    redis_client: Annotated[redis.Redis, Depends(get_redis)] = None,  # type: ignore[assignment, type-arg]
) -> SuggestionTagWorklistResponse:
    """Return paginated pending-suggestion counts grouped by tag, ordered by count DESC.

    Used as the entry point of the review queue: a reviewer picks a tag to work
    through. ``type`` narrows to one tag type; ``min_confidence`` excludes
    low-confidence suggestions from the counts. ``page`` and ``per_page``
    control pagination. ``search`` filters by tag title (bypasses cache).

    When ``search`` is not provided, the result is cached in Redis for
    ``_WORKLIST_CACHE_TTL`` seconds keyed by (type, min_confidence, page,
    per_page). Redis errors silently fall back to a direct DB query.

    The DISTINCT tag count query is slow on large datasets (~300ms), so the
    full response (items + total) is cached together to avoid repeating it.
    """

    def _build_response(
        rows: list[tuple[int, str | None, int, int]],
        total: int,
    ) -> SuggestionTagWorklistResponse:
        return SuggestionTagWorklistResponse(
            items=[
                SuggestionTagWorklistItem(
                    tag_id=tag_id,
                    title=title,
                    type=tag_type,
                    pending_count=pending_count,
                )
                for (tag_id, title, tag_type, pending_count) in rows
            ],
            total=total,
            page=page,
        )

    # Only cache when search is not active (search results are per-query and cheap to skip)
    if search is None and redis_client is not None:
        cache_key = f"{_WORKLIST_CACHE_PREFIX}{type}:{min_confidence}:{page}:{per_page}"
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                raw = json.loads(cached)
                return SuggestionTagWorklistResponse(**raw)
        except Exception:
            logger.warning(
                "Redis get failed for worklist cache key %s; falling back to DB", cache_key
            )

        rows, total = await count_pending_by_tag(
            db, type_filter=type, min_confidence=min_confidence, page=page, per_page=per_page
        )
        response = _build_response(rows, total)

        try:
            await redis_client.setex(
                cache_key,
                _WORKLIST_CACHE_TTL,
                json.dumps(response.model_dump()),
            )
        except Exception:
            logger.warning("Redis setex failed for worklist cache key %s; ignoring", cache_key)

        return response

    # search provided or no redis client — query directly, no cache
    rows, total = await count_pending_by_tag(
        db,
        type_filter=type,
        min_confidence=min_confidence,
        page=page,
        per_page=per_page,
        search=search,
    )
    return _build_response(rows, total)


@router.get(
    "",
    response_model=SuggestionGridResponse,
    response_description="Paginated pending suggestions for a single tag",
)
async def get_suggestions_for_tag(
    _user: Annotated[Users, Depends(require_image_tag_add)],
    tag_id: Annotated[int, Query(description="Tag to list pending suggestions for")],
    min_confidence: Annotated[
        float,
        Query(description="Only include suggestions at or above this confidence"),
    ] = 0.0,
    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1,
    per_page: Annotated[int, Query(ge=1, description="Items per page")] = 50,
    db: AsyncSession = Depends(get_db),
) -> SuggestionGridResponse:
    """Return a confidence-sorted page of pending suggestions for one tag.

    Each item embeds the full image (via ``ImageResponse``) so the frontend
    gets the computed ``thumbnail_url`` rather than a raw filename. The
    confidence-DESC ordering from the queue service is preserved.

    The response also includes:
    - ``tag``: summary of the tag being reviewed (for a human header + tag-detail link).
    - ``items[*].tags``: the image's currently-applied tags (for hover/redundancy detection).
      These are loaded via a single batched selectinload — NOT per-image/N+1.
    """
    items, total = await list_pending_for_tag(
        db,
        tag_id=tag_id,
        min_confidence=min_confidence,
        page=page,
        per_page=per_page,
    )

    # Fetch the tag summary (one SELECT — null-safe if tag_id doesn't exist).
    tag_row = await db.get(Tags, tag_id)
    tag_summary: TagSummary | None = TagSummary.model_validate(tag_row) if tag_row else None

    # Load the page's images in one query, then serialize in the service's
    # confidence-DESC order (a dict lookup keeps the order from `items`).
    image_ids = [image_id for (_suggestion_id, image_id, _confidence) in items]
    images_by_id: dict[int, Images] = {}
    if image_ids:
        # Eager-load the uploader and all applied tag_links in the same query.
        # selectinload(tag_links).selectinload(tag) issues one extra batched SQL
        # for the whole page — NOT one per image (no N+1).
        result = await db.execute(
            select(Images)
            .options(
                selectinload(Images.user).load_only(  # type: ignore[arg-type]
                    Users.user_id,  # type: ignore[arg-type]
                    Users.username,  # type: ignore[arg-type]
                    Users.avatar,  # type: ignore[arg-type]
                    Users.avatar_in_r2,  # type: ignore[arg-type]
                    Users.user_title,  # type: ignore[arg-type]
                ),
                selectinload(Images.tag_links).selectinload(TagLinks.tag),  # type: ignore[arg-type]
            )
            .where(Images.image_id.in_(image_ids))  # type: ignore[union-attr]
        )
        images_by_id = {img.image_id: img for img in result.scalars().all()}  # type: ignore[misc]

    grid_items = [
        SuggestionGridItem(
            suggestion_id=suggestion_id,
            confidence=confidence,
            image=ImageResponse.model_validate(images_by_id[image_id]),
            tags=[TagSummary.model_validate(tl.tag) for tl in images_by_id[image_id].tag_links],
        )
        for (suggestion_id, image_id, confidence) in items
        if image_id in images_by_id
    ]

    return SuggestionGridResponse(items=grid_items, total=total, page=page, tag=tag_summary)


@router.post(
    "/review",
    response_model=BulkReviewResult,
    response_description="Counts of approved/rejected suggestions and any errors",
)
async def review_suggestions(
    reviews: list[BulkReviewItem],
    current_user: Annotated[Users, Depends(require_image_tag_add)],
    db: AsyncSession = Depends(get_db),
) -> BulkReviewResult:
    """Approve or reject pending suggestions across multiple images at once.

    Approving applies the suggestion's (alias-resolved) tag to its image and
    records tag history; rejecting only marks the suggestion. Missing
    suggestion ids are reported in ``errors`` without aborting the valid ones.
    """
    result = await bulk_review_suggestions(
        db,
        [{"suggestion_id": r.suggestion_id, "action": r.action} for r in reviews],
        current_user.id,
    )
    return BulkReviewResult(
        approved=result.approved,
        rejected=result.rejected,
        errors=result.errors,
    )
