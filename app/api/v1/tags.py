"""
Tags API endpoints
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Images, TagLinks, Tags
from app.models.image import ImageSortBy
from app.schemas.image import ImageListResponse
from app.schemas.tag import TagListResponse, TagResponse, TagWithStats

router = APIRouter(prefix="/tags", tags=["tags"])


async def resolve_tag_alias(db: AsyncSession, tag_id: int) -> tuple[Tags | None, int]:
    """
    Resolve a tag alias to its actual tag.

    Aliases are synonyms - if a tag has an 'alias' field pointing to another tag,
    it means they're the same concept (e.g., "collar" -> "choker").

    Returns:
        tuple: (tag_object, resolved_tag_id)
            - tag_object: The original tag object (or None if not found)
            - resolved_tag_id: The actual tag ID if this is an alias, otherwise the original tag_id
    """
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
    tag = tag_result.scalar_one_or_none()

    if not tag:
        return None, tag_id

    # If this tag is an alias, use the actual tag for queries
    if tag.alias:
        return tag, tag.alias

    return tag, tag_id


async def get_tag_hierarchy(db: AsyncSession, tag_id: int) -> list[int]:
    """
    Get all tag IDs in a tag's hierarchy (self + all descendants).

    For parent tags (like "swimsuit"), this returns the parent ID plus all child tag IDs
    (like "school swimsuit", "bikini") that have inheritedfrom_id pointing to it.
    This allows querying a parent tag to include all images tagged with child tags.

    Returns:
        list[int]: List of tag IDs including the parent and all descendants
    """
    # Start with the requested tag
    tag_ids = [tag_id]

    # Find all tags that inherit from this tag (children)
    children_result = await db.execute(
        select(Tags.tag_id).where(Tags.inheritedfrom_id == tag_id)  # type: ignore[call-overload]
    )
    child_tag_ids = children_result.scalars().all()

    # Add all child tag IDs
    tag_ids.extend(child_tag_ids)

    # Recursively get descendants of children (grandchildren, etc.)
    for child_id in child_tag_ids:
        grandchildren = await get_tag_hierarchy(db, child_id)
        # Add only the new ones (exclude the child_id itself which is already in the list)
        tag_ids.extend([gid for gid in grandchildren if gid not in tag_ids])

    return tag_ids


@router.get("/{tag_id}/images", response_model=ImageListResponse)
async def get_images_by_tag(
    tag_id: int = Path(..., description="Tag ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: ImageSortBy = Query(ImageSortBy.image_id, description="Sort field"),
    sort_order: str = Query("DESC", pattern="^(ASC|DESC)$", description="Sort order"),
    db: AsyncSession = Depends(get_db)
) -> ImageListResponse:
    """
    Get all images with a specific tag.

    Handles both aliases and inheritance:
    - If the tag is an alias, resolves to the actual tag
    - Includes images tagged with child tags (via inheritedfrom_id hierarchy)

    For example, querying "dress" will include images tagged with "sundress",
    "cocktail dress", etc.

    For multiple tags, use `/images?tags=1,2,3` instead.
    """
    # First resolve any alias (synonym)
    tag, resolved_tag_id = await resolve_tag_alias(db, tag_id)

    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    # Get the full tag hierarchy (parent + all descendants)
    tag_hierarchy = await get_tag_hierarchy(db, resolved_tag_id)

    # Performance optimization: Two-stage query for fast tag filtering
    #
    # Stage 1 (Subquery): Use tag_links index to quickly find matching image_ids
    # - Filters by tag_id using index (very fast)
    # - Applies pagination on just IDs (small dataset)
    # - Returns limited set of image_ids (e.g., 20 IDs)
    # - NO SORTING in subquery - sorting happens in Stage 2
    #
    # Stage 2 (Main query): Fetch full image data only for those IDs
    # - Joins images table on primary key (fast)
    # - Applies sorting on the limited result set (20 rows)
    # - Only retrieves and sorts 20 full image rows instead of thousands
    #
    # This generates SQL similar to:
    # SELECT images.* FROM images
    # JOIN (
    #   SELECT DISTINCT tag_links.image_id
    #   FROM tag_links
    #   WHERE tag_links.tag_id IN (4, 5, 6)
    #   LIMIT 20
    # ) AS imageset ON images.image_id = imageset.image_id
    # ORDER BY images.image_id DESC
    from sqlalchemy import asc

    offset = (page - 1) * per_page

    # Subquery: Fast index scan to get matching image_ids with limit/offset
    # Uses ONLY tag_links table for maximum speed - no Images join needed
    # Sorting is deferred to the main query for better performance
    image_id_subquery = (
        select(TagLinks.image_id.distinct().label('image_id'))
        .where(TagLinks.tag_id.in_(tag_hierarchy))
        .limit(per_page)
        .offset(offset)
        .subquery('imageset')
    )

    # Count total (fast - only counts tag_links, not full images join)
    count_result = await db.execute(
        select(func.count(func.distinct(TagLinks.image_id)))
        .where(TagLinks.tag_id.in_(tag_hierarchy))
    )
    total = count_result.scalar()

    # Main query: Join full image data only for the limited set of IDs
    # Apply sorting here on the small result set (e.g., 20 rows)
    sort_column = getattr(Images, sort_by, Images.image_id)
    query = (
        select(Images)
        .join(image_id_subquery, Images.image_id == image_id_subquery.columns.image_id)  # type: ignore[arg-type]
    )

    # Apply sorting on main query
    if sort_order == "DESC":
        query = query.order_by(desc(sort_column))  # type: ignore[arg-type]
    else:
        query = query.order_by(asc(sort_column))  # type: ignore[arg-type]

    # Execute
    result = await db.execute(query)
    images = result.scalars().all()

    return ImageListResponse(
        total=total or 0,
        page=page,
        per_page=per_page,
        images=images
    )


@router.get("/", response_model=TagListResponse)
async def list_tags(
    search: str | None = Query(None, description="Search tags by name"),
    type_id: int | None = Query(None, description="Filter by tag type"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db)
) -> TagListResponse:
    """
    List and search tags.

    Supports:
    - Searching by tag name (partial match)
    - Filtering by tag type
    - Pagination
    """
    query = select(Tags)

    # Apply filters
    if search:
        query = query.where(Tags.title.like(f"%{search}%"))  # type: ignore[union-attr]
    if type_id is not None:
        query = query.where(Tags.type == type_id)  # type: ignore[arg-type]

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Sort by tag date added
    from sqlalchemy import desc as sql_desc
    query = query.order_by(sql_desc(Tags.date_added))  # type: ignore[arg-type]

    # Paginate
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    tags = result.scalars().all()

    return TagListResponse(
        total=total or 0,
        page=page,
        per_page=per_page,
        tags=[TagResponse.model_validate(tag) for tag in tags]
    )


@router.get("/{tag_id}", response_model=TagWithStats)
async def get_tag(
    tag_id: int = Path(..., description="Tag ID"),
    db: AsyncSession = Depends(get_db)
) -> TagWithStats:
    """
    Get a single tag by ID with usage statistics.

    Returns complete tag information including:
    - Alias information (if this tag is a synonym of another)
    - Inheritance information (parent tag and child count)
    - Image count (includes images tagged with child tags in the hierarchy)
    """
    # First resolve any alias (synonym)
    tag, resolved_tag_id = await resolve_tag_alias(db, tag_id)

    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    # Get the full tag hierarchy (includes all descendant tags)
    tag_hierarchy = await get_tag_hierarchy(db, resolved_tag_id)

    # Count images tagged with any tag in the hierarchy
    count_result = await db.execute(
        select(func.count(TagLinks.image_id.distinct())).where(
            TagLinks.tag_id.in_(tag_hierarchy)
        )
    )
    image_count = count_result.scalar()

    # Count direct children (tags that inherit from this tag)
    children_result = await db.execute(
        select(func.count(Tags.tag_id)).where(Tags.inheritedfrom_id == resolved_tag_id)  # type: ignore[arg-type]
    )
    child_count = children_result.scalar()

    # Determine if this is an alias tag (synonym)
    is_alias = tag.alias is not None
    aliased_tag_id = tag.alias if is_alias else None

    # Get parent tag in hierarchy (inheritance, not alias)
    parent_tag_id = tag.inheritedfrom_id

    return TagWithStats(
        tag_id=tag.tag_id,
        title=tag.title,
        type=tag.type,
        image_count=image_count or 0,
        is_alias=is_alias,
        aliased_tag_id=aliased_tag_id,
        parent_tag_id=parent_tag_id,
        child_count=child_count or 0,
    )
