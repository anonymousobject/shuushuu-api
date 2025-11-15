"""
Images API endpoints
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import asc, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import ImageSortParams, PaginationParams, UserSortParams
from app.api.v1.tags import resolve_tag_alias
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import Favorites, ImageRatings, Images, TagLinks, Tags, Users
from app.schemas.image import (
    ImageHashSearchResponse,
    ImageListResponse,
    ImageResponse,
    ImageStatsResponse,
    ImageTagItem,
    ImageTagsResponse,
)
from app.schemas.user import UserListResponse, UserResponse
from app.services.rating import schedule_rating_recalculation

router = APIRouter(prefix="/images", tags=["images"])


@router.get("/", response_model=ImageListResponse)
async def list_images(
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[ImageSortParams, Depends()],
    # Basic filters
    user_id: Annotated[int | None, Query(description="Filter by uploader user ID")] = None,
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
    min_rating: Annotated[float | None, Query(ge=0, le=5, description="Minimum rating")] = None,
    min_favorites: Annotated[int | None, Query(ge=0, description="Minimum favorite count")] = None,
    # Content filtering
    artist: Annotated[
        str | None, Query(description="Filter by artist name (partial match)")
    ] = None,
    characters: Annotated[
        str | None, Query(description="Filter by characters (partial match)")
    ] = None,
    db: AsyncSession = Depends(get_db),
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
    if image_status is not None:
        query = query.where(Images.status == image_status)  # type: ignore[arg-type]

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
                            select(TagLinks.image_id).where(TagLinks.tag_id == resolved_tag_id)  # type: ignore[call-overload]
                        )
                    )
            else:
                # Images must have ANY of the specified tags
                query = query.where(
                    Images.image_id.in_(  # type: ignore[union-attr]
                        select(TagLinks.image_id).where(TagLinks.tag_id.in_(tag_ids))  # type: ignore[call-overload,attr-defined]
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
    sort_column = getattr(Images, sorting.sort_by.value)
    if (
        sort_column == Images.date_added
    ):  # image_ids are assigned by date so use that. `date_added` doesn't have its own index.
        sort_column = Images.image_id

    if sorting.sort_order == "DESC":
        subquery_order = desc(sort_column)
    else:
        subquery_order = asc(sort_column)

    # Subquery: Apply all filters, sort, and limit to get matching image_ids
    image_id_subquery = (
        query.with_only_columns(Images.image_id.label("image_id"))  # type: ignore[union-attr]
        .order_by(subquery_order)
        .offset(pagination.offset)
        .limit(pagination.per_page)
        .subquery("imageset")
    )

    # Main query: Fetch full image data only for the limited set of IDs
    final_query = (
        select(Images).join(image_id_subquery, Images.image_id == image_id_subquery.c.image_id)  # type: ignore[arg-type]
    )

    # Execute query
    result = await db.execute(final_query)
    images = result.scalars().all()

    return ImageListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        images=[ImageResponse.model_validate(img) for img in images],
    )


@router.get("/{image_id}", response_model=ImageResponse)
async def get_image(image_id: int, db: AsyncSession = Depends(get_db)) -> ImageResponse:
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

    return ImageTagsResponse(
        image_id=image_id,
        tags=[ImageTagItem(tag_id=tag.tag_id, tag=tag.title, type_id=tag.type) for tag in tags],
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
        select(Images).where(Images.md5_hash == md5_hash)  # type: ignore[arg-type]
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
        select(Images).where(Images.image_id == current_user.bookmark)  # type: ignore[arg-type]
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Bookmarked image not found")

    return ImageResponse.model_validate(image)


@router.post("/{image_id}/tags/{tag_id}", status_code=status.HTTP_201_CREATED)
async def add_tag_to_image(
    image_id: Annotated[int, Path(description="Image ID")],
    tag_id: Annotated[int, Path(description="Tag ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Add a tag to an image.

    - Regular users can only tag their own images
    - Admins can tag any image

    Returns:
        Success message
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Permission check: user owns image or is admin
    if image.user_id != current_user.user_id and not current_user.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to tag this image",
        )

    # Verify tag exists
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
    tag = tag_result.scalar_one_or_none()

    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    # Check if tag link already exists
    existing_link = await db.execute(
        select(TagLinks).where(
            TagLinks.image_id == image_id,  # type: ignore[arg-type]
            TagLinks.tag_id == tag_id,  # type: ignore[arg-type]
        )
    )
    if existing_link.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Tag already linked to this image")

    # Create tag link
    tag_link = TagLinks(
        image_id=image_id,
        tag_id=tag_id,
        user_id=current_user.user_id,
    )
    db.add(tag_link)
    await db.commit()

    return {"message": "Tag added successfully"}


@router.delete("/{image_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_tag_from_image(
    image_id: Annotated[int, Path(description="Image ID")],
    tag_id: Annotated[int, Path(description="Tag ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Remove a tag from an image.

    - Regular users can only remove tags from their own images
    - Admins can remove tags from any image
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Permission check: user owns image or is admin
    if image.user_id != current_user.user_id and not current_user.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to remove tags from this image",
        )

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

    # Delete tag link
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
    rating: Annotated[int, Query(ge=0, le=10, description="Rating value (0-10)")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Rate an image (0-10 scale).

    Users can rate any image once. If they rate again, their previous rating is updated.

    Args:
        image_id: The image to rate
        rating: Rating value from 0 to 10

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
            ImageRatings.user_id == current_user.user_id,  # type: ignore[arg-type]
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
            user_id=current_user.user_id,
            image_id=image_id,
            rating=rating,
        )
        db.add(new_rating)
        message = "Rating added successfully"

    # Commit the rating first
    await db.commit()

    # Schedule background recalculation (non-blocking)
    schedule_rating_recalculation(image_id)

    return {"message": message}
