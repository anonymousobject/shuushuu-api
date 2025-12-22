"""
Tags API endpoints
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import case, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.dependencies import ImageSortParams, PaginationParams
from app.core.database import get_db
from app.core.permission_deps import require_permission
from app.core.permissions import Permission
from app.models import Images, TagLinks, Tags, Users
from app.schemas.image import ImageListResponse, ImageResponse
from app.schemas.tag import TagCreate, TagCreator, TagListResponse, TagResponse, TagWithStats

router = APIRouter(prefix="/tags", tags=["tags"])

# TODO: Create tag proposal/review system. Let users petition for new tags, and allow admins to review and approve them.


async def resolve_tag_alias(
    db: AsyncSession, tag_id: int, tag: Tags | None = None
) -> tuple[Tags | None, int]:
    """
    Resolve a tag alias to its actual tag.

    Aliases are synonyms - if a tag has an 'alias' field pointing to another tag,
    it means they're the same concept (e.g., "collar" -> "choker").

    Args:
        db: Database session
        tag_id: ID of the tag to resolve
        tag: Optional pre-fetched tag object to avoid duplicate queries

    Returns:
        tuple: (tag_object, resolved_tag_id)
            - tag_object: The original tag object (or None if not found)
            - resolved_tag_id: The actual tag ID if this is an alias, otherwise the original tag_id
    """
    # Use provided tag object if available, otherwise fetch it
    if tag is None:
        tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
        tag = tag_result.scalar_one_or_none()

    if not tag:
        return None, tag_id

    # If this tag is an alias, use the actual tag for queries
    if tag.alias_of:
        return tag, tag.alias_of

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


@router.get("/", response_model=TagListResponse)
async def list_tags(
    pagination: Annotated[PaginationParams, Depends()],
    search: Annotated[str | None, Query(description="Search tags by name")] = None,
    type_id: Annotated[int | None, Query(description="Filter by tag type", alias="type")] = None,
    ids: Annotated[str | None, Query(description="Comma-separated tag IDs to fetch")] = None,
    parent_tag_id: Annotated[int | None, Query(description="Filter by parent tag ID")] = None,
    exclude_aliases: Annotated[
        bool, Query(description="Exclude alias tags (tags that redirect to others)")
    ] = False,
    db: AsyncSession = Depends(get_db),
) -> TagListResponse:
    """
    List and search tags with intelligent hybrid search.

    Supports:
    - Intelligent tag search (word-order independent for multi-word queries)
    - Filtering by tag type
    - Filtering by specific IDs (comma-separated)
    - Filtering by parent tag ID (get child tags)
    - Excluding alias tags
    - Pagination

    ## Search Strategy

    Uses a hybrid approach for optimal UX:
    - **Short queries (< 3 chars)**: Prefix matching (e.g., "sa" finds "sakura kinomoto")
    - **Long queries (≥ 3 chars)**: Full-text search (word-order independent)

    The full-text search solves the Japanese character name problem:
    Searching "sakura kinomoto" will find tags with "kinomoto sakura" in any word order.

    When filtering by IDs, invalid (non-numeric) IDs are reported in the response
    via the `invalid_ids` field, while valid tags are still returned.

    **Examples:**
    - Get all tags: `/tags`
    - Search tags with "cat" (uses prefix if < 3 chars): `/tags?search=cat`
    - Search Japanese name (word-order independent): `/tags?search=sakura%20kinomoto`
    - Filter by type: `/tags?type_id=1`
    - Get specific tags by ID: `/tags?ids=1,2,3`
    - Get child tags of a parent: `/tags?parent_tag_id=10`
    - Exclude alias tags: `/tags?search=sakura&exclude_aliases=true`
    """
    # Create table alias to retrieve the title of the tag that this tag is aliased to
    AliasedTag = aliased(Tags)

    query = select(Tags, AliasedTag.title.label("alias_of_name")).outerjoin(  # type: ignore[union-attr]
        AliasedTag,
        Tags.alias_of == AliasedTag.tag_id,  # type: ignore[arg-type]
    )

    # Apply filters
    invalid_ids: list[str] = []
    if ids:
        # Filter by specific IDs (takes precedence over other filters)
        # Track invalid IDs for better user feedback
        tag_ids = []
        for id_str in ids.split(","):
            id_str = id_str.strip()
            if not id_str:
                continue  # Skip empty strings
            if id_str.isdigit():
                tag_ids.append(int(id_str))
            else:
                invalid_ids.append(id_str)
        if tag_ids:  # Only apply filter if we have valid IDs
            query = query.where(Tags.tag_id.in_(tag_ids))  # type: ignore[union-attr]
        else:
            # All IDs were invalid - return empty result
            query = query.where(False)  # type: ignore[arg-type]
    # Track whether we're using fulltext search and what query string
    fulltext_query_str: str | None = None

    if search:
        # Hybrid search strategy:
        # - Queries < 3 chars: Use LIKE (autocomplete, prefix matching)
        # - Queries >= 3 chars: Use FULLTEXT MATCH with wildcard expansion (word-order independent + partial words)
        #
        # This handles both:
        # 1. Japanese name problem: "sakura kinomoto" finds "kinomoto sakura" (word-order independence)
        # 2. Partial word matching: "thig" finds "thighs" (wildcard expansion)
        if len(search) < 3:
            # Short query: prefix match with LIKE (e.g., "sa" -> "sakura")
            query = query.where(Tags.title.like(f"{search}%"))  # type: ignore[union-attr]
        else:
            # Long query: word-order independent full-text search with wildcard expansion
            # Split search into words and add wildcard to each word to match partial terms
            # e.g., "sakura kinomoto" -> "+sakura* +kinomoto*"
            search_terms = search.split()
            fulltext_query_str = " ".join(f"+{term}*" for term in search_terms)
            query = query.where(
                text("MATCH (tags.title) AGAINST (:search IN BOOLEAN MODE)").bindparams(
                    search=fulltext_query_str
                )
            )
    if type_id is not None:
        query = query.where(Tags.type == type_id)  # type: ignore[arg-type]
    if parent_tag_id is not None:
        query = query.where(Tags.inheritedfrom_id == parent_tag_id)  # type: ignore[arg-type]
    if exclude_aliases:
        query = query.where(Tags.alias_of.is_(None))  # type: ignore[union-attr]

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Sort: For search queries, use smart ranking
    # For general listing, sort by date added
    if search:
        if len(search) < 3:
            # Short query (prefix match): prioritize exact and starts-with
            # 1. Exact matches first
            # 2. Starts-with matches second
            # 3. Contains matches last (alphabetical within each group)
            query = query.order_by(
                case(
                    (func.lower(Tags.title) == search.lower(), 0),  # Exact match (case-insensitive)
                    (
                        func.lower(Tags.title).like(f"{search.lower()}%"),
                        1,
                    ),  # Starts with (case-insensitive)
                    else_=2,  # Contains (middle/end)
                ),
                func.lower(Tags.title),  # Alphabetical within each priority group
            )
        else:
            # Long query (full-text): prioritize exact matches, then sort by relevance
            # 1. Exact matches first (e.g., searching "maid" finds "maid" tag first)
            # 2. Then by relevance score from FULLTEXT search
            # 3. Then by popularity (usage_count)
            # 4. Finally alphabetical for consistent ordering
            # Must use the same fulltext_query_str as the WHERE clause
            query = query.order_by(
                case(
                    (func.lower(Tags.title) == search.lower(), 0),  # Exact match (case-insensitive)
                    else_=1,  # Non-exact matches
                ),
                text("MATCH (tags.title) AGAINST (:search IN BOOLEAN MODE) DESC"),
                desc(Tags.usage_count),  # type: ignore[arg-type]  # Most popular tags first
                func.lower(Tags.title),  # Tertiary sort: alphabetical (case-insensitive)
            ).params(search=fulltext_query_str)
    else:
        # No search - sort by usage count (most popular first), then by date added
        query = query.order_by(
            desc(Tags.usage_count),  # type: ignore[arg-type]
            desc(Tags.date_added),  # type: ignore[arg-type]
        )

    # Paginate
    query = query.offset(pagination.offset).limit(pagination.per_page)

    # Execute
    result = await db.execute(query)
    rows = result.all()

    tags_list = []
    for row in rows:
        tag = row[0]
        alias_name = row[1]

        tag_resp = TagResponse.model_validate(tag)
        tag_resp.alias_of_name = alias_name
        tags_list.append(tag_resp)

    return TagListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        tags=tags_list,
        invalid_ids=invalid_ids if invalid_ids else None,
    )


@router.get("/{tag_id}/images", response_model=ImageListResponse)
async def get_images_by_tag(
    tag_id: Annotated[int, Path(description="Tag ID")],
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[ImageSortParams, Depends()],
    db: AsyncSession = Depends(get_db),
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

    # Subquery: Fast index scan to get matching image_ids with limit/offset
    # Uses ONLY tag_links table for maximum speed - no Images join needed
    # Sorting is deferred to the main query for better performance
    image_id_subquery = (
        select(TagLinks.image_id.distinct().label("image_id"))  # type: ignore[attr-defined]
        .where(TagLinks.tag_id.in_(tag_hierarchy))  # type: ignore[attr-defined]
        .limit(pagination.per_page)
        .offset(pagination.offset)
        .subquery("imageset")
    )

    # Count total (fast - only counts tag_links, not full images join)
    count_result = await db.execute(
        select(func.count(func.distinct(TagLinks.image_id))).where(
            TagLinks.tag_id.in_(tag_hierarchy)  # type: ignore[attr-defined]
        )
    )
    total = count_result.scalar()

    # Main query: Join full image data only for the limited set of IDs
    # Apply sorting here on the small result set (e.g., 20 rows)
    sort_column = sorting.sort_by.get_column(Images)
    query = select(Images).join(
        image_id_subquery,
        Images.image_id == image_id_subquery.columns.image_id,  # type: ignore[arg-type]
    )

    # Apply sorting on main query
    if sorting.sort_order == "DESC":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Execute
    result = await db.execute(query)
    images = result.scalars().all()

    return ImageListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        images=[ImageResponse.model_validate(img) for img in images],
    )


@router.get("/{tag_id}", response_model=TagWithStats)
async def get_tag(
    tag_id: Annotated[int, Path(description="Tag ID")],
    db: AsyncSession = Depends(get_db),
) -> TagWithStats:
    """
    Get a single tag by ID with usage statistics.

    Returns complete tag information including:
    - Alias information (if this tag is a synonym of another)
    - Inheritance information (parent tag and child count)
    - Image count (includes images tagged with child tags in the hierarchy)
    - Creator user information (who created the tag)
    - Creation date (when the tag was created)
    """
    # First resolve any alias (synonym) - join with Users to get creator info
    tag_result = await db.execute(
        select(Tags, Users)
        .outerjoin(Users, Tags.user_id == Users.user_id)  # type: ignore[arg-type]
        .where(Tags.tag_id == tag_id)  # type: ignore[arg-type]
    )
    row = tag_result.first()

    if not row:
        raise HTTPException(status_code=404, detail="Tag not found")

    tag, user = row

    # Resolve alias if needed
    resolved_tag_id = tag.alias_of if tag.alias_of else tag_id

    # Get the full tag hierarchy (includes all descendant tags)
    tag_hierarchy = await get_tag_hierarchy(db, resolved_tag_id)

    # Count images tagged with any tag in the hierarchy
    count_result = await db.execute(
        select(func.count(TagLinks.image_id.distinct())).where(TagLinks.tag_id.in_(tag_hierarchy))  # type: ignore[attr-defined]
    )
    image_count = count_result.scalar()

    # Count direct children (tags that inherit from this tag)
    children_result = await db.execute(
        select(func.count(Tags.tag_id)).where(Tags.inheritedfrom_id == resolved_tag_id)  # type: ignore[arg-type]
    )
    child_count = children_result.scalar()

    # Determine if this is an alias tag (synonym)
    is_alias = tag.alias_of is not None
    aliased_tag_id = tag.alias_of if is_alias else None

    # Get parent tag in hierarchy (inheritance, not alias)
    parent_tag_id = tag.inheritedfrom_id

    # Build creator info if user exists
    created_by = None
    if user:
        created_by = TagCreator(
            user_id=user.user_id or 0, username=user.username, avatar=user.avatar or None
        )

    return TagWithStats(
        tag_id=tag.tag_id or 0,
        title=tag.title,
        desc=tag.desc,
        type=tag.type,
        image_count=image_count or 0,
        is_alias=is_alias,
        aliased_tag_id=aliased_tag_id,
        parent_tag_id=parent_tag_id,
        child_count=child_count or 0,
        created_by=created_by,
        date_added=tag.date_added,
    )


@router.post("/", response_model=TagResponse)
async def create_tag(
    tag_data: TagCreate,
    _: Annotated[None, Depends(require_permission(Permission.TAG_CREATE))],
    db: AsyncSession = Depends(get_db),
) -> TagResponse:
    """
    Create a new tag.
    """

    # check if tag already exists
    existing_tag_result = await db.execute(
        select(Tags).where(Tags.title == tag_data.title).where(Tags.type == tag_data.type)  # type: ignore[arg-type]
    )
    if existing_tag_result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Tag already exists")

    # if inherited_from is set, ensure that tag exists
    if tag_data.inheritedfrom_id:
        parent_tag_result = await db.execute(
            select(Tags).where(Tags.tag_id == tag_data.inheritedfrom_id)  # type: ignore[arg-type]
        )
        if not parent_tag_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Parent tag does not exist")

    # if alias is set, ensure that tag exists
    if tag_data.alias_of:
        alias_tag_result = await db.execute(
            select(Tags).where(Tags.tag_id == tag_data.alias_of)  # type: ignore[arg-type]
        )
        if not alias_tag_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Alias tag does not exist")

    new_tag = Tags(
        title=tag_data.title,
        type=tag_data.type,
        desc=tag_data.desc,
        inheritedfrom_id=tag_data.inheritedfrom_id,
        alias_of=tag_data.alias_of,
    )
    db.add(new_tag)
    await db.commit()
    await db.refresh(new_tag)

    return TagResponse.model_validate(new_tag)


@router.put("/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: Annotated[int, Path(description="Tag ID")],
    tag_data: TagCreate,
    _: Annotated[None, Depends(require_permission(Permission.TAG_UPDATE))],
    db: AsyncSession = Depends(get_db),
) -> TagResponse:
    """
    Update an existing tag.
    """

    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
    tag = tag_result.scalar_one_or_none()

    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    update_data = tag_data.model_dump(exclude_unset=True)

    # Validate inheritedfrom_id and alias fields if present
    inheritedfrom_id = update_data.get("inheritedfrom_id")
    if inheritedfrom_id is not None:
        parent_result = await db.execute(select(Tags).where(Tags.tag_id == inheritedfrom_id))
        parent_tag = parent_result.scalar_one_or_none()
        if not parent_tag:
            raise HTTPException(
                status_code=400, detail=f"Parent tag with id {inheritedfrom_id} does not exist"
            )

    alias_id = update_data.get("alias_of")
    if alias_id is not None:
        alias_result = await db.execute(select(Tags).where(Tags.tag_id == alias_id))
        alias_tag = alias_result.scalar_one_or_none()
        if not alias_tag:
            raise HTTPException(
                status_code=400, detail=f"Alias of tag with id {alias_id} does not exist"
            )
    # Update fields
    for key, value in update_data.items():
        setattr(tag, key, value)

    db.add(tag)
    await db.commit()
    await db.refresh(tag)

    return TagResponse.model_validate(tag)


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: Annotated[int, Path(description="Tag ID")],
    _: Annotated[None, Depends(require_permission(Permission.TAG_DELETE))],
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a tag.
    """

    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
    tag = tag_result.scalar_one_or_none()

    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    await db.delete(tag)
    await db.commit()
