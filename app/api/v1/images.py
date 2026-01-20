"""
Images API endpoints
"""

import math
from datetime import datetime
from decimal import Decimal
from pathlib import Path as FilePath
from typing import Annotated

import redis.asyncio as redis
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy import and_, asc, delete, desc, func, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.api.dependencies import ImageSortParams, PaginationParams, UserSortParams
from app.api.v1.tags import get_tag_hierarchy, resolve_tag_alias
from app.config import AdminActionType, ReportCategory, ReportStatus, settings
from app.core.auth import CurrentUser, VerifiedUser, get_current_user, get_optional_current_user
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.permissions import Permission, has_permission
from app.core.redis import get_redis
from app.models import (
    AdminActions,
    Comments,
    Favorites,
    ImageRatings,
    ImageReports,
    ImageReportTagSuggestions,
    Images,
    TagHistory,
    TagLinks,
    Tags,
    Users,
)
from app.models.image import ImageSortBy
from app.models.permissions import UserGroups
from app.schemas.audit import ImageTagHistoryListResponse, ImageTagHistoryResponse
from app.schemas.common import UserSummary
from app.schemas.image import (
    TAG_TYPE_SORT_ORDER,
    BookmarkPageResponse,
    ImageDetailedListResponse,
    ImageDetailedResponse,
    ImageHashSearchResponse,
    ImageResponse,
    ImageStatsResponse,
    ImageTagItem,
    ImageTagsResponse,
    ImageUploadResponse,
    SimilarImageResult,
    SimilarImagesResponse,
)
from app.schemas.report import ReportCreate, ReportResponse, SkippedTagsInfo, TagSuggestion
from app.schemas.tag import LinkedTag
from app.schemas.user import UserListResponse, UserResponse
from app.services.image_processing import get_image_dimensions
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES
from app.services.iqdb import check_iqdb_similarity, remove_from_iqdb
from app.services.rating import schedule_rating_recalculation
from app.services.upload import check_upload_rate_limit, link_tags_to_image, save_uploaded_image
from app.tasks.queue import enqueue_job

logger = get_logger(__name__)

router = APIRouter(prefix="/images", tags=["images"])


def _get_client_ip(request: Request) -> str:
    """Extract client IP address from request, checking X-Forwarded-For first."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # X-Forwarded-For can contain multiple IPs, first one is the client
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


@router.get("/", response_model=ImageDetailedListResponse, include_in_schema=False)
@router.get("", response_model=ImageDetailedListResponse)
async def list_images(
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[ImageSortParams, Depends()],
    # Basic filters
    user_id: Annotated[int | None, Query(description="Filter by uploader user ID")] = None,
    favorited_by_user_id: Annotated[
        int | None, Query(description="Filter by user who favorited the image")
    ] = None,
    image_status: Annotated[
        int | None, Query(description="Filter by status (1=active, 2=pending, etc)", alias="status")
    ] = None,
    # Tag filtering
    tags: Annotated[
        str | None, Query(description="Comma-separated tag IDs (e.g., '1,2,3')")
    ] = None,
    tags_mode: Annotated[
        str, Query(pattern="^(any|all)$", description="Match ANY or ALL tags")
    ] = "any",
    # Date filtering
    date_from: Annotated[str | None, Query(description="Start date (YYYY-MM-DD)")] = None,
    date_to: Annotated[str | None, Query(description="End date (YYYY-MM-DD)")] = None,
    # Size filtering
    min_width: Annotated[int | None, Query(ge=1, description="Minimum width in pixels")] = None,
    max_width: Annotated[int | None, Query(ge=1, description="Maximum width in pixels")] = None,
    min_height: Annotated[int | None, Query(ge=1, description="Minimum height in pixels")] = None,
    max_height: Annotated[int | None, Query(ge=1, description="Maximum height in pixels")] = None,
    # Rating filtering
    min_rating: Annotated[
        float | None, Query(ge=1, le=10, description="Minimum rating (1-10)")
    ] = None,
    min_favorites: Annotated[int | None, Query(ge=0, description="Minimum favorite count")] = None,
    min_num_ratings: Annotated[
        int | None, Query(ge=0, description="Minimum number of ratings")
    ] = None,
    # Comment filtering
    commenter: Annotated[
        int | None, Query(description="Filter by user who commented on the image")
    ] = None,
    commentsearch: Annotated[
        str | None, Query(description="Full-text search in comment text")
    ] = None,
    commentsearch_mode: Annotated[
        str | None,
        Query(
            pattern="^(natural|boolean|like)$",
            description="Search mode: natural (default), boolean fulltext, or LIKE",
        ),
    ] = None,
    hascomments: Annotated[
        bool | None,
        Query(description="Filter to images that have comments (true) or no comments (false)"),
    ] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Users | None = Depends(get_optional_current_user),
) -> ImageDetailedListResponse:
    """
    Search and list images with comprehensive filtering.

    **Supports:**
    - Pagination (page, per_page)
    - Sorting by any field
    - Tag filtering (by ID, with ANY/ALL modes)
    - Date range filtering
    - Size/dimension filtering
    - Rating and popularity filtering
    - Comment filtering (by commenter user ID, text search, or presence)

    **Comment Search Modes:**
    - `natural` (default): MySQL fulltext natural language search (10-100x faster, relevance ranking)
    - `boolean`: MySQL fulltext boolean search with operators
    - `like`: Simple pattern matching, works anywhere

    **Boolean Mode Examples:**
    - `+awesome -terrible`: Must contain "awesome", must not contain "terrible"
    - `"exact phrase"`: Search for exact phrase
    - `word*`: Wildcard search

    **Examples:**
    - `/images?tags=1,2,3&tags_mode=all` - Images with ALL tags 1, 2, and 3
    - `/images?min_width=1920&min_height=1080` - HD images only
    - `/images?date_from=2024-01-01&sort_by=favorites` - Images from 2024, sorted by popularity
    - `/images?user_id=5&min_rating=4.0` - High-rated images by user 5
    - `/images?commenter=10` - Images commented on by user 10
    - `/images?commentsearch=awesome` - Images with "awesome" in comments (natural fulltext)
    - `/images?commentsearch=awesome&commentsearch_mode=like` - Simple search using LIKE
    - `/images?commentsearch=+great -bad&commentsearch_mode=boolean` - Boolean fulltext
    - `/images?hascomments=true` - Images that have comments
    - `/images?hascomments=false` - Images with no comments
    """
    # Build base query
    query = select(Images)

    # Apply basic filters
    if user_id is not None:
        query = query.where(Images.user_id == user_id)  # type: ignore[arg-type]
    if favorited_by_user_id is not None:
        # Join with Favorites table to filter by user who favorited
        query = query.join(Favorites).where(Favorites.user_id == favorited_by_user_id)  # type: ignore[arg-type]
    # Status filtering: explicit param overrides, otherwise use user's show_all_images setting
    if image_status is not None:
        # Explicit status filter - always honor it
        query = query.where(Images.status == image_status)  # type: ignore[arg-type]
    else:
        # No explicit filter - apply default based on user's show_all_images setting
        # Anonymous users or users with show_all_images=0 see only public statuses
        show_all = current_user is not None and current_user.show_all_images == 1
        if not show_all:
            if current_user is not None:
                # Logged in: show public statuses OR user's own images (any status)
                query = query.where(
                    or_(
                        Images.status.in_(PUBLIC_IMAGE_STATUSES),  # type: ignore[attr-defined]
                        Images.user_id == current_user.user_id,  # type: ignore[arg-type]
                    )
                )
            else:
                # Anonymous: only public statuses
                query = query.where(Images.status.in_(PUBLIC_IMAGE_STATUSES))  # type: ignore[attr-defined]

    # Tag filtering
    if tags:
        tag_ids = [int(tid.strip()) for tid in tags.split(",") if tid.strip().isdigit()]
        if len(tag_ids) > settings.MAX_SEARCH_TAGS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You can only search for up to {settings.MAX_SEARCH_TAGS} tags at a time.",
            )
        if tag_ids:
            if tags_mode == "all":
                # Images must have ALL specified tags (including their descendants)
                for tag_id in tag_ids:
                    _, resolved_tag_id = await resolve_tag_alias(db, tag_id)
                    # Expand to full hierarchy (parent + all descendants)
                    hierarchy_ids = await get_tag_hierarchy(db, resolved_tag_id)
                    query = query.where(
                        Images.image_id.in_(  # type: ignore[union-attr]
                            select(TagLinks.image_id).where(TagLinks.tag_id.in_(hierarchy_ids))  # type: ignore[call-overload,attr-defined]
                        )
                    )
            else:
                # Images must have ANY of the specified tags (including their descendants)
                # Resolve aliases and expand hierarchies for all tags
                all_hierarchy_ids: set[int] = set()
                for tag_id in tag_ids:
                    _, resolved_tag_id = await resolve_tag_alias(db, tag_id)
                    hierarchy_ids = await get_tag_hierarchy(db, resolved_tag_id)
                    all_hierarchy_ids.update(hierarchy_ids)
                query = query.where(
                    Images.image_id.in_(  # type: ignore[union-attr]
                        select(TagLinks.image_id).where(TagLinks.tag_id.in_(all_hierarchy_ids))  # type: ignore[call-overload,attr-defined]
                    )
                )

    # Date filtering
    if date_from:
        query = query.where(Images.date_added >= date_from)  # type: ignore[arg-type,operator]
    if date_to:
        query = query.where(Images.date_added <= date_to)  # type: ignore[arg-type,operator]

    # Size filtering
    if min_width:
        query = query.where(Images.width >= min_width)  # type: ignore[arg-type]
    if max_width:
        query = query.where(Images.width <= max_width)  # type: ignore[arg-type]
    if min_height:
        query = query.where(Images.height >= min_height)  # type: ignore[arg-type]
    if max_height:
        query = query.where(Images.height <= max_height)  # type: ignore[arg-type]

    # Rating filtering
    if min_rating is not None:
        query = query.where(Images.bayesian_rating >= min_rating)  # type: ignore[arg-type]
    if min_favorites is not None:
        query = query.where(Images.favorites >= min_favorites)  # type: ignore[arg-type]
    if min_num_ratings is not None:
        query = query.where(Images.num_ratings >= min_num_ratings)  # type: ignore[arg-type]

    # Comment filtering
    # Note: We use distinct() to avoid duplicate rows when an image has multiple comments
    # The distinct is applied to the subquery stage for efficiency
    if commenter is not None or commentsearch is not None:
        # Join with Comments table for filtering when we need to filter by comment attributes
        query = query.join(Comments, Images.image_id == Comments.image_id)  # type: ignore[arg-type]
        if commenter is not None:
            query = query.where(Comments.user_id == commenter)  # type: ignore[arg-type]
        if commentsearch is not None:
            # Text search with mode selection (default to natural language fulltext)
            effective_mode = commentsearch_mode or "natural"

            if effective_mode == "boolean":
                # Boolean fulltext: supports +word, -word, "phrase", word*
                match_expr = sql_text("MATCH(post_text) AGAINST(:query IN BOOLEAN MODE)")
                query = query.where(match_expr).params(query=commentsearch)
            elif effective_mode == "natural":
                # Natural language fulltext: ranks by relevance (default, fastest)
                match_expr = sql_text("MATCH(post_text) AGAINST(:query IN NATURAL LANGUAGE MODE)")
                query = query.where(match_expr).params(query=commentsearch)
            else:  # like
                # Simple pattern matching (slowest but works everywhere)
                search_pattern = f"%{commentsearch}%"
                query = query.where(Comments.post_text.like(search_pattern))  # type: ignore[attr-defined]
    elif hascomments is True:
        # Use posts counter field (fast indexed lookup)
        query = query.where(Images.posts > 0)  # type: ignore[arg-type]
    elif hascomments is False:
        # Filter to images WITHOUT comments using posts counter
        query = query.where(Images.posts == 0)  # type: ignore[arg-type]

    # Count total results
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Performance optimization: Two-stage query for fast filtering and sorting
    #
    # Stage 1 (Subquery): Apply filters, sorting, and pagination on just image_id
    # - Uses indexes for filtering (user_id, status, dimensions, etc.)
    # - Sorts only the IDs (lightweight operation)
    # - Returns limited set of image_ids (e.g., 20 IDs)
    #
    # Stage 2 (Main query): Fetch full image data only for those IDs
    # - Joins on primary key (fast)
    # - Only retrieves 20 full image rows instead of thousands
    #
    # This generates SQL similar to:
    # SELECT images.* FROM images
    # JOIN (
    #   SELECT image_id FROM images
    #   WHERE ... (filters)
    #   ORDER BY favorites DESC
    #   LIMIT 20
    # ) AS imageset ON images.image_id = imageset.image_id

    # Apply sorting and pagination
    # Use the centralized get_column() method which handles field aliasing
    # (e.g., maps date_added -> image_id for performance)
    sort_column = sorting.sort_by.get_column(Images)

    if sorting.sort_order == "DESC":
        subquery_order = desc(sort_column)
    else:
        subquery_order = asc(sort_column)

    # Secondary sort by image_id ensures consistent ordering when primary sort has ties
    # (e.g., multiple images with same favorites count). Use descending for "newest first".
    secondary_order = desc(Images.image_id)  # type: ignore[var-annotated,arg-type]

    # Subquery: Apply all filters, sort, and limit to get matching image_ids
    # When comment filters are used with JOIN, apply distinct() to avoid duplicate rows
    # (one image can have multiple comments)
    image_id_subquery = (
        query.with_only_columns(Images.image_id.label("image_id"))  # type: ignore[union-attr]
    )
    # Only need distinct when we JOIN with Comments (commenter or commentsearch filters)
    if commenter is not None or commentsearch is not None:
        image_id_subquery = image_id_subquery.distinct()

    imageset = (
        image_id_subquery.order_by(subquery_order, secondary_order)
        .offset(pagination.offset)
        .limit(pagination.per_page)
        .subquery("imageset")
    )

    # Main query: Fetch full image data only for the limited set of IDs
    # Note: Must re-apply ORDER BY since JOIN doesn't preserve subquery order
    final_query = (
        select(Images)
        .options(
            selectinload(Images.user)  # type: ignore[arg-type]
            .selectinload(Users.user_groups)  # type: ignore[arg-type]
            .selectinload(UserGroups.group),  # type: ignore[arg-type]
            selectinload(Images.tag_links).selectinload(TagLinks.tag),  # type: ignore[arg-type]
        )
        .join(imageset, Images.image_id == imageset.c.image_id)  # type: ignore[arg-type]
        .order_by(subquery_order, secondary_order)  # Re-apply same sort order
    )

    # Execute query
    result = await db.execute(final_query)
    images = result.scalars().all()

    # Get favorite status for authenticated users (separate query for clean separation)
    favorited_ids: set[int] = set()
    if current_user and images:
        image_ids = [img.image_id for img in images]
        fav_result = await db.execute(
            select(Favorites.image_id).where(  # type: ignore[call-overload]
                Favorites.user_id == current_user.id,
                Favorites.image_id.in_(image_ids),  # type: ignore[attr-defined]
            )
        )
        favorited_ids = set(fav_result.scalars().all())

    return ImageDetailedListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        images=[
            ImageDetailedResponse.from_db_model(
                img,
                is_favorited=img.image_id in favorited_ids,
            )
            for img in images
        ],
    )


@router.get("/{image_id}", response_model=ImageDetailedResponse)
async def get_image(
    image_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Users | None = Depends(get_optional_current_user),
) -> ImageDetailedResponse:
    """
    Get a single image by ID.

    Returns detailed information about an image including metadata,
    ratings, statistics, embedded user info, and tags.
    """
    result = await db.execute(
        select(Images)
        # Load related user and tags with optimal strategies
        # - selectinload for user: Simple 1:1, additional query is fine
        # - joinedload for tags: Fetches everything in one query (faster for single image)
        .options(
            selectinload(Images.user)  # type: ignore[arg-type]
            .selectinload(Users.user_groups)  # type: ignore[arg-type]
            .selectinload(UserGroups.group),  # type: ignore[arg-type]
            joinedload(Images.tag_links).joinedload(TagLinks.tag),  # type: ignore[arg-type]
        )
        .where(Images.image_id == image_id)  # type: ignore[arg-type]
    )
    # unique() is required when using joinedload with collections to deduplicate rows
    image = result.unique().scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    is_favorited = False
    user_rating = None
    if current_user:
        # Optimized: Get both favorite status and rating in a single query
        # Uses LEFT JOINs to check if records exist for this user
        # Type ignores needed: mypy doesn't understand SQLAlchemy's operator overloading for column comparisons
        user_data_result = await db.execute(
            select(
                Favorites.user_id.label("fav_user_id"),  # type: ignore[attr-defined]  # Will be non-null if favorited
                ImageRatings.rating.label("user_rating"),  # type: ignore[attr-defined]
            )
            .select_from(Images)
            .outerjoin(
                Favorites,
                and_(
                    Favorites.image_id == Images.image_id,  # type: ignore[arg-type]
                    Favorites.user_id == current_user.id,  # type: ignore[arg-type]
                ),
            )
            .outerjoin(
                ImageRatings,
                and_(
                    ImageRatings.image_id == Images.image_id,  # type: ignore[arg-type]
                    ImageRatings.user_id == current_user.id,  # type: ignore[arg-type]
                ),
            )
            .where(Images.image_id == image_id)  # type: ignore[arg-type]
        )
        row = user_data_result.first()
        if row:
            is_favorited = row.fav_user_id is not None
            user_rating = row.user_rating

    # Get previous and next image IDs (chronological)
    # We only want active images (status >= 1)
    prev_id_result = await db.execute(
        select(Images.image_id)  # type: ignore[call-overload]
        .where(Images.image_id < image_id)  # type: ignore[operator]
        .where(Images.status >= 1)
        .order_by(desc(Images.image_id))  # type: ignore[arg-type]
        .limit(1)
    )
    prev_image_id = prev_id_result.scalar_one_or_none()

    next_id_result = await db.execute(
        select(Images.image_id)  # type: ignore[call-overload]
        .where(Images.image_id > image_id)  # type: ignore[operator]
        .where(Images.status >= 1)
        .order_by(asc(Images.image_id))  # type: ignore[arg-type]
        .limit(1)
    )
    next_image_id = next_id_result.scalar_one_or_none()

    return ImageDetailedResponse.from_db_model(
        image,
        is_favorited=is_favorited,
        user_rating=user_rating,
        prev_image_id=prev_image_id,
        next_image_id=next_image_id,
    )


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    image_id: Annotated[int, Path(description="Image ID to delete")],
    reason: Annotated[str, Query(description="Reason for deletion", min_length=1, max_length=500)],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> None:
    """
    Permanently delete an image from the database and disk.

    This is a destructive operation that:
    - Removes the image from the IQDB similarity index
    - Deletes all image files (fullsize, thumbnail, medium, large variants)
    - Deletes the database record (CASCADE removes tags, favorites, ratings, etc.)
    - Logs the action to admin_actions for audit trail

    Requires IMAGE_DELETE permission.

    **Note:** This cannot be undone. For recoverable removal, use status change instead.
    """
    assert current_user.user_id is not None

    # Check permission
    if not await has_permission(db, current_user.user_id, Permission.IMAGE_DELETE, redis_client):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="IMAGE_DELETE permission required",
        )

    # Get the image
    result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    # Log the deletion action BEFORE deleting (so we have image_id reference)
    admin_action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.IMAGE_DELETE,
        image_id=image_id,
        details={
            "reason": reason,
            "filename": image.filename,
            "ext": image.ext,
            "uploader_id": image.user_id,
            "status_before": image.status,
        },
    )
    db.add(admin_action)
    await db.flush()  # Ensure action is logged before deletion

    # Remove from IQDB (non-blocking, failures logged but don't stop deletion)
    if not remove_from_iqdb(image_id):
        logger.warning("iqdb_remove_failed_for_deleted_image", image_id=image_id)

    # Capture file paths before deleting from DB
    storage_path = FilePath(settings.STORAGE_PATH)
    files_to_delete = [
        storage_path / "fullsize" / f"{image.filename}.{image.ext}",
        storage_path / "thumbs" / f"{image.filename}.webp",
        storage_path / "thumbs" / f"{image.filename}.jpeg",  # Old format
        storage_path / "medium" / f"{image.filename}.{image.ext}",
        storage_path / "large" / f"{image.filename}.{image.ext}",
    ]

    # Delete database record using raw SQL to let database handle CASCADE
    # (ORM delete tries to manage relationships in Python, causing issues with composite PKs)
    await db.execute(delete(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    await db.commit()

    # Delete files from disk AFTER successful DB commit to avoid inconsistency
    for file_path in files_to_delete:
        try:
            if file_path.exists():
                file_path.unlink()
                logger.info("image_file_deleted", image_id=image_id, path=str(file_path))
        except OSError as e:
            logger.warning(
                "image_file_delete_failed", image_id=image_id, path=str(file_path), error=str(e)
            )

    logger.info(
        "image_deleted",
        image_id=image_id,
        deleted_by=current_user.user_id,
        reason=reason,
    )


@router.get("/{image_id}/tags", response_model=ImageTagsResponse)
async def get_image_tags(image_id: int, db: AsyncSession = Depends(get_db)) -> ImageTagsResponse:
    """
    Get all tags for a specific image.
    """
    # First check if image exists
    image_result = await db.execute(
        select(Images).where(Images.image_id == image_id)  # type: ignore[arg-type]
    )
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Get tags through tag_links
    result = await db.execute(
        select(Tags)
        .join(TagLinks, TagLinks.tag_id == Tags.tag_id)  # type: ignore[arg-type]
        .where(TagLinks.image_id == image_id)  # type: ignore[arg-type]
    )
    tags = result.scalars().all()

    # Sort by type (artist → source → character → theme) then alphabetically
    sorted_tags = sorted(
        tags,
        key=lambda t: (
            TAG_TYPE_SORT_ORDER.get(t.type, 99),
            (t.title or "").lower(),
        ),
    )

    return ImageTagsResponse(
        image_id=image_id,
        tags=[
            ImageTagItem(
                tag_id=tag.tag_id or 0,  # tag_id guaranteed from database
                tag=tag.title or "",  # title guaranteed from database
                type_id=tag.type or 0,  # type guaranteed from database
            )
            for tag in sorted_tags
        ],
    )


@router.get("/{image_id}/tag-history", response_model=ImageTagHistoryListResponse)
async def get_image_tag_history(
    image_id: Annotated[int, Path(description="Image ID")],
    pagination: Annotated[PaginationParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> ImageTagHistoryListResponse:
    """
    Get tag history for an image.

    Returns paginated list of tags that were added or removed from this image.
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    if not image_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Image not found")

    # Query tag history with joined tag and user info
    # Eager load user groups for UserSummary
    query = (
        select(TagHistory, Tags, Users)
        .join(Tags, TagHistory.tag_id == Tags.tag_id)  # type: ignore[arg-type]
        .outerjoin(Users, TagHistory.user_id == Users.user_id)  # type: ignore[arg-type]
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(TagHistory.image_id == image_id)  # type: ignore[arg-type]
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate and order by most recent first
    # Secondary sort by tag_history_id for stable ordering when timestamps match
    query = (
        query.order_by(
            desc(TagHistory.date),  # type: ignore[arg-type]
            desc(TagHistory.tag_history_id),  # type: ignore[arg-type]
        )
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )

    result = await db.execute(query)
    rows = result.all()

    items = []
    for history, tag, user in rows:
        user_summary = None
        if user:
            user_summary = UserSummary(
                user_id=user.user_id,
                username=user.username,
                avatar=user.avatar,
                groups=user.groups if user else [],
            )

        items.append(
            ImageTagHistoryResponse(
                tag_history_id=history.tag_history_id,
                image_id=history.image_id,
                tag_id=history.tag_id,
                action="added" if history.action == "a" else "removed",
                user=user_summary,
                date=history.date,
                tag=LinkedTag(tag_id=tag.tag_id, title=tag.title) if tag else None,
            )
        )

    return ImageTagHistoryListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        items=items,
    )


@router.get("/search/by-hash/{md5_hash}", response_model=ImageHashSearchResponse)
async def search_by_hash(
    md5_hash: str, db: AsyncSession = Depends(get_db)
) -> ImageHashSearchResponse:
    """
    Search for an image by MD5 hash.

    Useful for duplicate detection and reverse image search.
    """
    result = await db.execute(
        select(Images)
        .options(selectinload(Images.user).load_only(Users.user_id, Users.username, Users.avatar))  # type: ignore[arg-type]
        .where(Images.md5_hash == md5_hash)  # type: ignore[arg-type]
    )
    images = result.scalars().all()

    return ImageHashSearchResponse(
        md5_hash=md5_hash,
        found=len(images),
        images=[ImageResponse.model_validate(img) for img in images],
    )


@router.get("/stats/summary", response_model=ImageStatsResponse)
async def get_stats(db: AsyncSession = Depends(get_db)) -> ImageStatsResponse:
    """
    Get overall image statistics.
    """
    total_result = await db.execute(select(func.count(Images.image_id)))  # type: ignore[arg-type]
    total_images = total_result.scalar()

    total_favorites_result = await db.execute(select(func.sum(Images.favorites)))
    total_favorites = total_favorites_result.scalar() or 0

    avg_rating_result = await db.execute(select(func.avg(Images.rating)))
    avg_rating = avg_rating_result.scalar() or 0.0

    return ImageStatsResponse(
        total_images=total_images or 0,
        total_favorites=int(total_favorites),
        average_rating=round(float(avg_rating), 2),
    )


@router.get("/{image_id}/favorites", response_model=UserListResponse)
async def get_image_favorites(
    image_id: int,
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[UserSortParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    """
    Get all users who have favorited a specific image.
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Get users who favorited the image
    query = select(Users).join(Favorites).where(Favorites.image_id == image_id)  # type: ignore[arg-type]

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting
    sort_column = getattr(Users, sorting.sort_by, Users.user_id)
    if sorting.sort_order == "DESC":
        query = query.order_by(desc(sort_column))  # type: ignore[arg-type]
    else:
        query = query.order_by(asc(sort_column))  # type: ignore[arg-type]

    # Apply pagination
    query = query.offset(pagination.offset).limit(pagination.per_page)

    # Execute
    result = await db.execute(query)
    users = result.scalars().all()

    return UserListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        users=[UserResponse.model_validate(user) for user in users],
    )


@router.get("/{image_id}/similar", response_model=SimilarImagesResponse)
async def get_similar_images(
    image_id: int,
    threshold: Annotated[
        float | None, Query(description="Minimum similarity score (0-100)", ge=0, le=100)
    ] = None,
    db: AsyncSession = Depends(get_db),
) -> SimilarImagesResponse:
    """
    Find images similar to the specified image using IQDB.

    Queries the IQDB similarity index using the image's thumbnail and returns
    matching images ordered by similarity score (highest first).

    The query image itself is excluded from results.
    """
    # Get the image to find its thumbnail path
    result = await db.execute(
        select(Images).where(Images.image_id == image_id)  # type: ignore[arg-type]
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Construct thumbnail path
    thumb_path = FilePath(settings.STORAGE_PATH) / "thumbs" / f"{image.filename}.webp"

    if not thumb_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Image thumbnail not found - cannot perform similarity search",
        )

    # Query IQDB for similar images (threshold is 0-100 scale)
    similar_results = await check_iqdb_similarity(thumb_path, db, threshold=threshold)

    # Filter out the query image itself
    similar_results = [r for r in similar_results if r["image_id"] != image_id]

    if not similar_results:
        return SimilarImagesResponse(query_image_id=image_id, similar_images=[])

    # Get full image details for similar images
    similar_ids = [r["image_id"] for r in similar_results]

    images_result = await db.execute(
        select(Images)
        .options(
            selectinload(Images.user).load_only(Users.user_id, Users.username, Users.avatar)  # type: ignore[arg-type]
        )
        .where(Images.image_id.in_(similar_ids))  # type: ignore[union-attr]
    )
    images: dict[int, Images] = {
        img.image_id: img  # type: ignore[misc]
        for img in images_result.scalars().all()
    }

    # Build response with similarity scores, ordered by score descending
    similar_images = []
    for r in sorted(similar_results, key=lambda x: x["score"], reverse=True):
        img = images.get(int(r["image_id"]))
        if img:
            img_data = ImageResponse.model_validate(img).model_dump()
            img_data["similarity_score"] = r["score"]
            similar_images.append(SimilarImageResult(**img_data))

    return SimilarImagesResponse(query_image_id=image_id, similar_images=similar_images)


@router.get("/bookmark/me", response_model=ImageResponse)
async def get_bookmark_image(
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> ImageResponse:
    """
    Get the user's current bookmarked image from their profile.
    """

    if not current_user.bookmark:
        raise HTTPException(status_code=404, detail="No bookmarked image set for user")

    result = await db.execute(
        select(Images)
        .options(selectinload(Images.user).load_only(Users.user_id, Users.username, Users.avatar))  # type: ignore[arg-type]
        .where(Images.image_id == current_user.bookmark)  # type: ignore[arg-type]
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Bookmarked image not found")

    return ImageResponse.model_validate(image)


@router.get("/bookmark/page", response_model=BookmarkPageResponse)
async def get_bookmark_page(
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> BookmarkPageResponse:
    """
    Get the page number where the user's bookmark appears in their image list.

    Uses the user's sorting preferences (sorting_pref, sorting_pref_order)
    and visibility settings (show_all_images) to calculate which page
    contains their bookmarked image.

    If the bookmark is not visible under user's visibility settings
    (show_all_images=0 and bookmark is not public/owned), returns page: null.

    This allows frontend to redirect to: /images?page=42#i12345
    """
    if not current_user.bookmark:
        raise HTTPException(status_code=404, detail="No bookmarked image set for user")

    bookmark_id = current_user.bookmark

    # Get the bookmark image to check it exists and get its sort values
    bookmark_result = await db.execute(
        select(Images).where(Images.image_id == bookmark_id)  # type: ignore[arg-type]
    )
    bookmark_image = bookmark_result.scalar_one_or_none()

    if not bookmark_image:
        raise HTTPException(status_code=404, detail="Bookmarked image not found")

    # Build the same filter as list_images uses
    show_all = current_user.show_all_images == 1
    images_per_page = current_user.images_per_page or 15

    # Check if bookmark is visible under user's settings
    # If not visible, return page: null (bookmark exists but won't appear in list)
    if not show_all:
        is_public = bookmark_image.status in PUBLIC_IMAGE_STATUSES
        is_own_image = bookmark_image.user_id == current_user.user_id
        if not is_public and not is_own_image:
            return BookmarkPageResponse(
                page=None,
                image_id=bookmark_id,
                images_per_page=images_per_page,
            )

    # Get sort column and direction from user preferences
    sort_field = current_user.sorting_pref or "image_id"
    sort_order = current_user.sorting_pref_order or "DESC"

    # Convert to ImageSortBy enum, falling back to image_id if invalid
    try:
        sort_enum = ImageSortBy(sort_field)
    except ValueError:
        sort_enum = ImageSortBy.image_id

    # Use ImageSortBy.get_column() to get the actual column (handles aliasing like date_added -> image_id)
    sort_column = sort_enum.get_column(Images)
    sort_column_name = sort_column.key  # Get column name for getattr on bookmark_image

    # Get the bookmark's sort value
    bookmark_sort_value = getattr(bookmark_image, sort_column_name)

    # Handle NULL sort values - treat as "sorts last" (after all non-NULL values)
    # If bookmark has NULL, it would appear at the end of both ASC and DESC sorts
    if bookmark_sort_value is None:
        # Count all non-NULL values as coming before this bookmark
        # Also count NULL values with higher image_id (secondary sort is DESC)
        position_filter = or_(
            sort_column.isnot(None),
            and_(
                sort_column.is_(None),
                Images.image_id > bookmark_id,  # type: ignore[operator,arg-type]
            ),
        )
    else:
        # Count images that come BEFORE the bookmark in the sorted order
        # Secondary sort is always desc(image_id), so higher image_id wins ties
        # For DESC: higher sort_value comes first, then higher image_id
        # For ASC: lower sort_value comes first, then higher image_id (secondary is still DESC)
        if sort_order.upper() == "DESC":
            # Images before bookmark: higher sort value, or same value with higher image_id
            position_filter = or_(
                sort_column > bookmark_sort_value,
                and_(
                    sort_column == bookmark_sort_value,
                    Images.image_id > bookmark_id,  # type: ignore[operator,arg-type]
                ),
            )
        else:
            # ASC: lower sort value, or same value with higher image_id (secondary is DESC)
            position_filter = or_(
                sort_column < bookmark_sort_value,
                and_(
                    sort_column == bookmark_sort_value,
                    Images.image_id > bookmark_id,  # type: ignore[operator,arg-type]
                ),
            )

    # Build count query with visibility filter
    count_query = select(func.count()).select_from(Images).where(position_filter)

    if not show_all:
        # Apply same visibility filter as list_images
        count_query = count_query.where(
            or_(
                Images.status.in_(PUBLIC_IMAGE_STATUSES),  # type: ignore[attr-defined]
                Images.user_id == current_user.user_id,  # type: ignore[arg-type]
            )
        )

    result = await db.execute(count_query)
    position = result.scalar() or 0

    # Page is 1-indexed: position 0-14 = page 1, 15-29 = page 2, etc.
    page = math.ceil((position + 1) / images_per_page)

    return BookmarkPageResponse(
        page=page,
        image_id=bookmark_id,
        images_per_page=images_per_page,
    )


@router.post("/{image_id}/tags/{tag_id}", status_code=status.HTTP_201_CREATED)
async def add_tag_to_image(
    image_id: Annotated[int, Path(description="Image ID")],
    tag_id: Annotated[int, Path(description="Tag ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> dict[str, str]:
    """
    Add a tag to an image.

    Tags can be added to images if:
    - The user owns the image
    - The user has admin privileges
    - The user has the IMAGE_TAG_ADD permission

    Returns:
        Success message
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Check ownership, admin, or permission
    is_owner = image.user_id == current_user.id
    is_admin = current_user.admin
    has_edit_permission = await has_permission(
        db, current_user.id, Permission.IMAGE_TAG_ADD, redis_client
    )

    if not is_owner and not is_admin and not has_edit_permission:
        raise HTTPException(403, "Not authorized to edit this image")

    # Verify tag exists and resolve aliases
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
    tag = tag_result.scalar_one_or_none()

    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    # Resolve alias tags to their actual tag (pass the already-fetched tag to avoid duplicate query)
    _, resolved_tag_id = await resolve_tag_alias(db, tag_id, tag)

    # Check if tag link already exists (using resolved tag ID)
    existing_link = await db.execute(
        select(TagLinks).where(
            TagLinks.image_id == image_id,  # type: ignore[arg-type]
            TagLinks.tag_id == resolved_tag_id,  # type: ignore[arg-type]
        )
    )
    if existing_link.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Tag already linked to this image")

    # Create tag link (usage_count is maintained by database trigger)
    tag_link = TagLinks(
        image_id=image_id,
        tag_id=resolved_tag_id,
        user_id=current_user.id,
    )
    db.add(tag_link)

    # Record in tag history
    history_entry = TagHistory(
        image_id=image_id,
        tag_id=resolved_tag_id,
        action="a",
        user_id=current_user.id,
    )
    db.add(history_entry)

    await db.commit()

    return {"message": "Tag added successfully"}


@router.delete("/{image_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_tag_from_image(
    image_id: Annotated[int, Path(description="Image ID")],
    tag_id: Annotated[int, Path(description="Tag ID")],
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> None:
    """
    Remove a tag from an image.

    Tags can be removed from images if:
    - The user owns the image
    - The user has admin privileges
    - The user has the IMAGE_TAG_REMOVE permission
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Check ownership, admin, or permission
    is_owner = image.user_id == current_user.id
    is_admin = current_user.admin
    has_edit_permission = await has_permission(
        db, current_user.id, Permission.IMAGE_TAG_REMOVE, redis_client
    )

    if not is_owner and not is_admin and not has_edit_permission:
        raise HTTPException(403, "Not authorized to edit this image")

    # Check if tag link exists
    link_result = await db.execute(
        select(TagLinks).where(
            TagLinks.image_id == image_id,  # type: ignore[arg-type]
            TagLinks.tag_id == tag_id,  # type: ignore[arg-type]
        )
    )
    tag_link = link_result.scalar_one_or_none()

    if not tag_link:
        raise HTTPException(status_code=404, detail="Tag not linked to this image")

    # Record in tag history (before deleting the link)
    history_entry = TagHistory(
        image_id=image_id,
        tag_id=tag_id,
        action="r",
        user_id=current_user.id,
    )
    db.add(history_entry)

    # Delete tag link (usage_count is maintained by database trigger)
    await db.execute(
        delete(TagLinks).where(
            TagLinks.image_id == image_id,  # type: ignore[arg-type]
            TagLinks.tag_id == tag_id,  # type: ignore[arg-type]
        )
    )
    await db.commit()


@router.post("/{image_id}/rating", status_code=status.HTTP_201_CREATED)
async def rate_image(
    image_id: Annotated[int, Path(description="Image ID")],
    rating: Annotated[int, Query(ge=1, le=10, description="Rating value (1-10)")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Rate an image (1-10 scale).

    Users can rate any image once. If they rate again, their previous rating is updated.

    Args:
        image_id: The image to rate
        rating: Rating value from 1 to 10

    Returns:
        Success message indicating if rating was created or updated
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Check if user already rated this image
    existing_rating = await db.execute(
        select(ImageRatings).where(
            ImageRatings.user_id == current_user.id,  # type: ignore[arg-type]
            ImageRatings.image_id == image_id,  # type: ignore[arg-type]
        )
    )
    existing = existing_rating.scalar_one_or_none()

    if existing:
        # Update existing rating
        existing.rating = rating
        message = "Rating updated successfully"
    else:
        # Create new rating
        new_rating = ImageRatings(
            user_id=current_user.id,
            image_id=image_id,
            rating=rating,
        )
        db.add(new_rating)
        message = "Rating added successfully"

    # Commit the rating first
    await db.commit()

    # Schedule background recalculation (non-blocking)
    await schedule_rating_recalculation(image_id)

    return {"message": message}


@router.post("/{image_id}/favorite", response_model=None)
async def favorite_image(
    image_id: Annotated[int, Path(description="Image ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    Favorite an image.

    Users can favorite any image. If they've already favorited it, this is idempotent
    and returns 200 OK. Returns 201 Created for new favorites.

    Args:
        image_id: The image to favorite

    Returns:
        Success message indicating if favorite was created or already existed
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Check if user already favorited this image
    existing_favorite = await db.execute(
        select(Favorites).where(
            Favorites.user_id == current_user.id,  # type: ignore[arg-type]
            Favorites.image_id == image_id,  # type: ignore[arg-type]
        )
    )
    existing = existing_favorite.scalar_one_or_none()

    if existing:
        # Favorite already exists, return 200 OK (idempotent)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "message": "Image already favorited",
                "favorited": True,
                "favorites_count": image.favorites,
            },
        )

    # Create new favorite
    new_favorite = Favorites(
        user_id=current_user.id,
        image_id=image_id,
    )
    db.add(new_favorite)

    # Update counters
    image.favorites += 1
    current_user.favorites += 1

    # Commit all changes
    await db.commit()

    # Return 201 Created for new favorites
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "message": "Favorite added successfully",
            "favorited": True,
            "favorites_count": image.favorites,
        },
    )


@router.delete("/{image_id}/favorite")
async def unfavorite_image(
    image_id: Annotated[int, Path(description="Image ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool | int | str]:
    """
    Unfavorite an image.

    Users can unfavorite an image they have previously favorited.

    Args:
        image_id: The image to unfavorite
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Check if user has favorited this image
    existing_favorite = await db.execute(
        select(Favorites).where(
            Favorites.user_id == current_user.id,  # type: ignore[arg-type]
            Favorites.image_id == image_id,  # type: ignore[arg-type]
        )
    )
    existing = existing_favorite.scalar_one_or_none()

    if not existing:
        raise HTTPException(status_code=404, detail="Favorite not found")

    # Delete the favorite
    await db.execute(
        delete(Favorites).where(
            Favorites.user_id == current_user.id,  # type: ignore[arg-type]
            Favorites.image_id == image_id,  # type: ignore[arg-type]
        )
    )

    # Update counters (ensure they don't go negative)
    image.favorites = max(0, image.favorites - 1)
    current_user.favorites = max(0, current_user.favorites - 1)

    await db.commit()

    return {
        "message": "Favorite removed successfully",
        "favorited": False,
        "favorites_count": image.favorites,
    }


@router.post("/upload", response_model=ImageUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(
    request: Request,
    current_user: VerifiedUser,
    file: Annotated[UploadFile, File(description="Image file to upload")],
    caption: Annotated[str, Form(max_length=35)] = "",
    tag_ids: Annotated[str, Form(description="Comma-separated tag IDs (e.g., '1,2,3')")] = "",
    db: AsyncSession = Depends(get_db),
) -> ImageUploadResponse:
    """
    Upload a new image with metadata and tags.

    Requires authentication.

    Process:
    1. Create temporary image record to get image_id
    2. Validate image file (type, size, extension) using PIL
    3. Check for duplicate (MD5 hash)
    4. Save image to storage (filename: YYYY-MM-DD-{image_id}.{ext})
    5. Extract image dimensions
    6. Update image record with metadata
    7. Link tags to image
    8. Schedule thumbnail generation (background)
    9. Return created image details

    Security:
    - Requires authentication
    - Validates file is actually an image using PIL (prevents malicious uploads)
    - Validates file type, size, and extension
    - Prevents duplicate images (MD5 check)

    Filename Format:
    - Main image: YYYY-MM-DD-{image_id}.{ext} (e.g., 2025-11-15-1111881.jpeg)
    - Thumbnail: YYYY-MM-DD-{image_id}.{ext} (same format, in thumbs/ directory)
    """
    logger.info(
        "image_upload_started",
        user_id=current_user.id,
        filename=file.filename,
        content_type=file.content_type,
    )

    # Check upload rate limit (skip for admins/moderators)
    if not current_user.admin:
        await check_upload_rate_limit(current_user.id, db)

    # Get client IP address for logging
    client_ip = _get_client_ip(request)

    # Create temporary image record to get image_id for filename
    temp_image = Images(
        filename="temp",  # Will be updated after save
        ext="tmp",
        original_filename=file.filename or "unknown",
        md5_hash="",  # Will be calculated during save
        filesize=0,
        width=0,
        height=0,
        user_id=current_user.id,
        ip=client_ip,  # Log IP address
        status=1,
        locked=0,
    )
    db.add(temp_image)
    await db.flush()  # Get image_id
    image_id: int = temp_image.image_id  # type: ignore[assignment]

    logger.info("image_record_created", image_id=image_id)

    file_path: FilePath | None = None  # Initialize to track if file was saved
    try:
        # Save image to storage (validates and calculates hash)
        # If validation fails, this will raise HTTPException
        file_path, ext, md5_hash = await save_uploaded_image(file, settings.STORAGE_PATH, image_id)
        logger.info("image_saved", image_id=image_id, file_path=str(file_path), md5_hash=md5_hash)

        # Check for duplicate image (MD5)
        existing_result = await db.execute(
            select(Images).where(
                Images.md5_hash == md5_hash,  # type: ignore[arg-type]
                Images.image_id != image_id,  # type: ignore[arg-type]
            )
        )
        existing_image = existing_result.scalar_one_or_none()

        if existing_image:
            # Delete the uploaded file and temp record since it's a duplicate
            logger.warning(
                "duplicate_image_detected",
                image_id=image_id,
                duplicate_of=existing_image.image_id,
                md5_hash=md5_hash,
            )
            # Don't rollback here - let the exception handler do it
            # Just raise the exception and the handler will clean up
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Image already exists with ID {existing_image.image_id}",
            )

        # Get image dimensions and update record
        width, height = get_image_dimensions(file_path)
        filesize = file_path.stat().st_size

        # Calculate total pixels (in megapixels)
        total_pixels = Decimal((width * height) / 1_000_000)

        # Generate filename for storage (date-id format)
        date_prefix = datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_prefix}-{image_id}"  # Store without extension

        # Check IQDB for similar images (runs in main thread, ~100-300ms)
        # Uses the main uploaded file for similarity check
        similar_images = await check_iqdb_similarity(file_path, db)

        # TODO: If similar_images has high-scoring matches:
        # - Show them to user for confirmation (return 409 with list of matches)
        # - Allow user to confirm upload anyway (add skip_similarity_check param)
        # - Example: if similar_images and not skip_similarity_check:
        #     raise HTTPException(409, {"matches": similar_images, "message": "Similar images found"})
        # For now, we just check but don't block the upload

        # Determine if medium/large variants should be created
        has_medium = 1 if (width > settings.MEDIUM_EDGE or height > settings.MEDIUM_EDGE) else 0
        has_large = 1 if (width > settings.LARGE_EDGE or height > settings.LARGE_EDGE) else 0

        # Update temporary record with actual data
        temp_image.filename = filename
        temp_image.ext = ext
        temp_image.md5_hash = md5_hash
        temp_image.filesize = filesize
        temp_image.width = width
        temp_image.height = height
        temp_image.total_pixels = total_pixels
        temp_image.medium = has_medium
        temp_image.large = has_large
        temp_image.caption = caption

        # Link tags if provided
        if tag_ids.strip():
            try:
                tag_id_list = [int(tid.strip()) for tid in tag_ids.split(",") if tid.strip()]
                await link_tags_to_image(image_id, tag_id_list, current_user.id, db)
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid tag IDs format. Must be comma-separated integers.",
                ) from e

        # Commit all changes
        await db.commit()
        await db.refresh(temp_image)

        logger.info(
            "image_upload_completed",
            image_id=image_id,
            width=width,
            height=height,
            filesize=filesize,
            md5_hash=md5_hash,
            has_tags=bool(tag_ids.strip()),
        )

        # Schedule thumbnail generation
        await enqueue_job(
            "create_thumbnail_job",
            image_id=image_id,
            source_path=str(file_path),
            ext=ext,
            storage_path=settings.STORAGE_PATH,
        )
        logger.debug("thumbnail_job_enqueued", image_id=image_id)

        # Schedule medium variant generation if needed
        if has_medium:
            await enqueue_job(
                "create_variant_job",
                image_id=image_id,
                source_path=str(file_path),
                ext=ext,
                storage_path=settings.STORAGE_PATH,
                width=width,
                height=height,
                variant_type="medium",
            )

        # Schedule large variant generation if needed
        if has_large:
            await enqueue_job(
                "create_variant_job",
                image_id=image_id,
                source_path=str(file_path),
                ext=ext,
                storage_path=settings.STORAGE_PATH,
                width=width,
                height=height,
                variant_type="large",
            )

        # Add to IQDB index AFTER thumbnail is created
        # Use defer to ensure thumbnail completes first (simple approach)
        thumb_path = FilePath(settings.STORAGE_PATH) / "thumbs" / f"{date_prefix}-{image_id}.webp"
        await enqueue_job(
            "add_to_iqdb_job",
            image_id=image_id,
            thumb_path=str(thumb_path),
            _defer_by=5.0,  # Wait 5 seconds for thumbnail to complete
        )

        # Build response
        image_response = ImageResponse(
            image_id=temp_image.image_id or 0,  # image_id is guaranteed to exist after flush
            filename=temp_image.filename,
            ext=temp_image.ext,
            original_filename=temp_image.original_filename,
            md5_hash=temp_image.md5_hash,
            filesize=temp_image.filesize,
            width=temp_image.width,
            height=temp_image.height,
            caption=temp_image.caption,
            rating=temp_image.rating,
            user_id=temp_image.user_id,  # user_id is guaranteed from database
            date_added=temp_image.date_added,
            status=temp_image.status,
            locked=temp_image.locked,
            posts=temp_image.posts,
            favorites=temp_image.favorites,
            bayesian_rating=temp_image.bayesian_rating,
            num_ratings=temp_image.num_ratings,
            medium=temp_image.medium,
            large=temp_image.large,
        )

        return ImageUploadResponse(
            message="Image uploaded successfully",
            image_id=temp_image.image_id or 0,  # image_id is guaranteed after flush
            image=image_response,
        )

    except HTTPException as he:
        # Clean up file and database record on validation error
        logger.warning(
            "image_upload_failed",
            image_id=image_id if "image_id" in locals() else None,
            error=he.detail,
            status_code=he.status_code,
        )
        # Rollback database first, then delete file
        await db.rollback()
        if file_path and file_path.exists():
            file_path.unlink()
        raise
    except Exception as e:
        # Clean up file and database record on any error
        logger.error(
            "image_upload_error",
            image_id=image_id if "image_id" in locals() else None,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        # Rollback database first, then delete file
        await db.rollback()
        if file_path and file_path.exists():
            file_path.unlink()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload image: {str(e)}",
        ) from e


@router.post(
    "/{image_id}/report", response_model=ReportResponse, status_code=status.HTTP_201_CREATED
)
async def report_image(
    image_id: Annotated[int, Path(description="Image ID to report")],
    report_data: ReportCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    """
    Report an image for review.

    Users can report images for various reasons:
    - 1: Repost (duplicate of another image)
    - 2: Inappropriate content
    - 3: Spam
    - 4: Tag suggestions (can include add/remove suggestions)
    - 5: Spoiler
    - 127: Other

    For TAG_SUGGESTIONS (category 4), users can optionally include a list of
    suggested_tag_ids. Invalid tags and tags already on the image are
    skipped and reported in the response.

    A user can only have one pending report per image. The report goes into
    a triage queue for admin review.

    Requires authentication.
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Check if user already has a pending report for this image
    existing_report = await db.execute(
        select(ImageReports).where(
            ImageReports.image_id == image_id,  # type: ignore[arg-type]
            ImageReports.user_id == current_user.id,  # type: ignore[arg-type]
            ImageReports.status == ReportStatus.PENDING,  # type: ignore[arg-type]
        )
    )
    if existing_report.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="You already have a pending report for this image",
        )

    # Process tag suggestions for TAG_SUGGESTIONS category
    skipped_tags = SkippedTagsInfo()
    valid_add_tags: list[int] = []
    valid_remove_tags: list[int] = []

    if report_data.category == ReportCategory.TAG_SUGGESTIONS:
        # Get existing tags on image
        existing_tags_result = await db.execute(
            select(TagLinks.tag_id).where(TagLinks.image_id == image_id)  # type: ignore[call-overload]
        )
        existing_tag_ids = set(existing_tags_result.scalars().all())

        # Process addition suggestions
        if report_data.suggested_tag_ids_add:
            valid_add_result = await db.execute(
                select(Tags.tag_id).where(Tags.tag_id.in_(report_data.suggested_tag_ids_add))  # type: ignore[call-overload,union-attr]
            )
            valid_add_db_ids = set(valid_add_result.scalars().all())

            for tag_id in report_data.suggested_tag_ids_add:
                if tag_id not in valid_add_db_ids:
                    skipped_tags.invalid_tag_ids.append(tag_id)
                elif tag_id in existing_tag_ids:
                    skipped_tags.already_on_image.append(tag_id)
                else:
                    valid_add_tags.append(tag_id)

        # Process removal suggestions
        if report_data.suggested_tag_ids_remove:
            valid_remove_result = await db.execute(
                select(Tags.tag_id).where(Tags.tag_id.in_(report_data.suggested_tag_ids_remove))  # type: ignore[call-overload,union-attr]
            )
            valid_remove_db_ids = set(valid_remove_result.scalars().all())

            for tag_id in report_data.suggested_tag_ids_remove:
                if tag_id not in valid_remove_db_ids:
                    skipped_tags.invalid_tag_ids.append(tag_id)
                elif tag_id not in existing_tag_ids:
                    skipped_tags.not_on_image.append(tag_id)
                else:
                    valid_remove_tags.append(tag_id)

    # Create the report
    new_report = ImageReports(
        image_id=image_id,
        user_id=current_user.id,
        category=report_data.category,
        reason_text=report_data.reason_text,
        status=ReportStatus.PENDING,
    )
    db.add(new_report)
    await db.flush()  # Get report_id

    # Create tag suggestions
    suggestions: list[ImageReportTagSuggestions] = []

    # Addition suggestions (type=1)
    for tag_id in valid_add_tags:
        suggestion = ImageReportTagSuggestions(
            report_id=new_report.report_id,
            tag_id=tag_id,
            suggestion_type=1,
        )
        db.add(suggestion)
        suggestions.append(suggestion)

    # Removal suggestions (type=2)
    for tag_id in valid_remove_tags:
        suggestion = ImageReportTagSuggestions(
            report_id=new_report.report_id,
            tag_id=tag_id,
            suggestion_type=2,
        )
        db.add(suggestion)
        suggestions.append(suggestion)

    await db.commit()
    await db.refresh(new_report)
    for s in suggestions:
        await db.refresh(s)

    logger.info(
        "image_reported",
        report_id=new_report.report_id,
        image_id=image_id,
        user_id=current_user.id,
        category=report_data.category,
        tag_suggestions_count=len(suggestions),
    )

    # Build response
    response = ReportResponse.model_validate(new_report)
    response.username = current_user.username

    # Add tag suggestions to response
    if suggestions:
        # Fetch tag names for the suggestions
        tag_ids = [s.tag_id for s in suggestions]
        tags_result = await db.execute(
            select(Tags).where(Tags.tag_id.in_(tag_ids))  # type: ignore[union-attr]
        )
        tags_by_id = {t.tag_id: t for t in tags_result.scalars().all()}

        response.suggested_tags = []
        for s in suggestions:
            tag = tags_by_id.get(s.tag_id)
            if not tag:
                logger.warning(
                    "missing_tag_for_suggestion",
                    suggestion_id=s.suggestion_id,
                    tag_id=s.tag_id,
                )
                continue
            response.suggested_tags.append(
                TagSuggestion(
                    suggestion_id=s.suggestion_id or 0,
                    tag_id=s.tag_id,
                    tag_name=tag.title or "",
                    tag_type=tag.type,
                    suggestion_type=s.suggestion_type,
                    accepted=s.accepted,
                )
            )

    # Include skipped tags info if any were skipped
    if skipped_tags.invalid_tag_ids or skipped_tags.already_on_image or skipped_tags.not_on_image:
        response.skipped_tags = skipped_tags

    return response
