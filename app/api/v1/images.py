"""
Images API endpoints
"""
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.tags import resolve_tag_alias
from app.core.database import get_db
from app.models import Favorites, Images, TagLinks, Tags, Users
from app.models.image import ImageSortBy  # Import from model
from app.schemas.image import (
    ImageHashSearchResponse,
    ImageListResponse,
    ImageResponse,
    ImageStatsResponse,
    ImageTagItem,
    ImageTagsResponse,
)
from app.schemas.user import UserListResponse, UserResponse

router = APIRouter(prefix="/images", tags=["images"])


class SortOrder(str, Enum):
    """Sort order options."""
    ASC = "ASC"
    DESC = "DESC"


@router.get("/{image_id}", response_model=ImageResponse)
async def get_image(
    image_id: int,
    db: AsyncSession = Depends(get_db)
) -> ImageResponse:
    """
    Get a single image by ID.

    Returns detailed information about an image including metadata,
    ratings, and statistics.
    """
    result = await db.execute(
        select(Images).where(Images.image_id == image_id)  # type: ignore[arg-type]
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    return ImageResponse.model_validate(image)


@router.get("/", response_model=ImageListResponse)
async def list_images(
    # Pagination
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),

    # Sorting
    sort_by: ImageSortBy = Query(ImageSortBy.image_id, description="Sort field"),
    sort_order: SortOrder = Query(SortOrder.DESC, description="Sort order"),

    # Basic filters
    user_id: int | None = Query(None, description="Filter by uploader user ID"),
    status: int | None = Query(None, description="Filter by status (1=active, 2=pending, etc)"),

    # Tag filtering
    tags: str | None = Query(None, description="Comma-separated tag IDs (e.g., '1,2,3')"),
    tags_mode: str = Query("any", pattern="^(any|all)$", description="Match ANY or ALL tags"),

    # Date filtering
    date_from: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="End date (YYYY-MM-DD)"),

    # Size filtering
    min_width: int | None = Query(None, ge=1, description="Minimum width in pixels"),
    max_width: int | None = Query(None, ge=1, description="Maximum width in pixels"),
    min_height: int | None = Query(None, ge=1, description="Minimum height in pixels"),
    max_height: int | None = Query(None, ge=1, description="Maximum height in pixels"),

    # Rating filtering
    min_rating: float | None = Query(None, ge=0, le=5, description="Minimum rating"),
    min_favorites: int | None = Query(None, ge=0, description="Minimum favorite count"),

    # Content filtering
    artist: str | None = Query(None, description="Filter by artist name (partial match)"),
    characters: str | None = Query(None, description="Filter by characters (partial match)"),

    db: AsyncSession = Depends(get_db)
) -> ImageListResponse:
    """
    Search and list images with comprehensive filtering.

    **Supports:**
    - Pagination (page, per_page)
    - Sorting by any field
    - Tag filtering (by ID, with ANY/ALL modes)
    - Date range filtering
    - Size/dimension filtering
    - Rating and popularity filtering
    - Content filtering (artist, characters)

    **Examples:**
    - `/images?tags=1,2,3&tags_mode=all` - Images with ALL tags 1, 2, and 3
    - `/images?min_width=1920&min_height=1080` - HD images only
    - `/images?date_from=2024-01-01&sort_by=favorites` - Images from 2024, sorted by popularity
    - `/images?user_id=5&min_rating=4.0` - High-rated images by user 5
    """
    # Build base query
    query = select(Images)

    # Apply basic filters
    if user_id is not None:
        query = query.where(Images.user_id == user_id)  # type: ignore[arg-type]
    if status is not None:
        query = query.where(Images.status == status)  # type: ignore[arg-type]

    # Tag filtering
    if tags:
        tag_ids = [int(tid.strip()) for tid in tags.split(",") if tid.strip().isdigit()]
        if tag_ids:
            if tags_mode == "all":
                # Images must have ALL specified tags
                for tag_id in tag_ids:
                    _, resolved_tag_id = await resolve_tag_alias(db, tag_id)
                    query = query.where(
                        Images.image_id.in_(  # type: ignore[union-attr]
                            select(TagLinks.image_id).where(TagLinks.tag_id == resolved_tag_id)
                        )
                    )
            else:
                # Images must have ANY of the specified tags
                query = query.where(
                    Images.image_id.in_(  # type: ignore[union-attr]
                        select(TagLinks.image_id).where(TagLinks.tag_id.in_(tag_ids))
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
        query = query.where(Images.rating >= min_rating)  # type: ignore[arg-type]
    if min_favorites is not None:
        query = query.where(Images.favorites >= min_favorites)  # type: ignore[arg-type]

    # Content filtering
    if artist:
        query = query.where(Images.artist.like(f"%{artist}%"))  # type: ignore[union-attr]
    if characters:
        query = query.where(Images.characters.like(f"%{characters}%"))  # type: ignore[union-attr]

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
    offset = (page - 1) * per_page
    sort_column = getattr(Images, sort_by.value)
    if sort_column == Images.date_added: # image_ids are assigned by date so use that. `date_added` doesn't have its own index.
        sort_column = Images.image_id

    if sort_order == SortOrder.DESC:
        subquery_order = desc(sort_column)
    else:
        subquery_order = asc(sort_column)

    # Subquery: Apply all filters, sort, and limit to get matching image_ids
    image_id_subquery = (
        query.with_only_columns(Images.image_id.label('image_id'))  # type: ignore[union-attr]
        .order_by(subquery_order)
        .offset(offset)
        .limit(per_page)
        .subquery('imageset')
    )

    # Main query: Fetch full image data only for the limited set of IDs
    final_query = (
        select(Images)
        .join(image_id_subquery, Images.image_id == image_id_subquery.c.image_id)  # type: ignore[arg-type]
    )

    # Execute query
    result = await db.execute(final_query)
    images = result.scalars().all()

    return ImageListResponse(
        total=total or 0,
        page=page,
        per_page=per_page,
        images=[ImageResponse.model_validate(img) for img in images]
    )


@router.get("/{image_id}/tags", response_model=ImageTagsResponse)
async def get_image_tags(
    image_id: int,
    db: AsyncSession = Depends(get_db)
) -> ImageTagsResponse:
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
        .join(TagLinks, TagLinks.tag_id == Tags.tag_id)
        .where(TagLinks.image_id == image_id)
    )
    tags = result.scalars().all()

    return ImageTagsResponse(
        image_id=image_id,
        tags=[
            ImageTagItem(
                tag_id=tag.tag_id,
                tag=tag.title,
                type_id=tag.type
            )
            for tag in tags
        ]
    )


@router.get("/search/by-hash/{md5_hash}", response_model=ImageHashSearchResponse)
async def search_by_hash(
    md5_hash: str,
    db: AsyncSession = Depends(get_db)
) -> ImageHashSearchResponse:
    """
    Search for an image by MD5 hash.

    Useful for duplicate detection and reverse image search.
    """
    result = await db.execute(
        select(Images).where(Images.md5_hash == md5_hash)  # type: ignore[arg-type]
    )
    images = result.scalars().all()

    return ImageHashSearchResponse(
        md5_hash=md5_hash,
        found=len(images),
        images=[ImageResponse.model_validate(img) for img in images]
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
        average_rating=round(float(avg_rating), 2)
    )


@router.get("/{image_id}/favorites", response_model=UserListResponse)
async def get_image_favorites(
    image_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("user_id", description="Sort field (user_id, date_joined, etc)"),
    sort_order: SortOrder = Query(SortOrder.DESC, description="Sort order"),
    db: AsyncSession = Depends(get_db)
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
    sort_column = getattr(Users, sort_by, Users.user_id)
    if sort_order == SortOrder.DESC:
        query = query.order_by(desc(sort_column))  # type: ignore[arg-type]
    else:
        query = query.order_by(asc(sort_column))  # type: ignore[arg-type]

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    users = result.scalars().all()

    return UserListResponse(
        total=total or 0,
        page=page,
        per_page=per_page,
        users=[UserResponse.model_validate(user) for user in users]
    )
