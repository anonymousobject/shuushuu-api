"""
Tags API endpoints
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import case, desc, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.api.dependencies import ImageSortParams, PaginationParams
from app.config import TagAuditActionType, TagType
from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.permission_deps import require_permission
from app.core.permissions import Permission
from app.models import Images, TagExternalLinks, TagLinks, Tags, Users
from app.models.character_source_link import CharacterSourceLinks
from app.models.permissions import UserGroups
from app.models.tag_audit_log import TagAuditLog
from app.models.tag_history import TagHistory
from app.schemas.audit import (
    TagAuditLogListResponse,
    TagAuditLogResponse,
    TagHistoryListResponse,
    TagHistoryResponse,
)
from app.schemas.common import UserSummary
from app.schemas.image import ImageListResponse, ImageResponse
from app.schemas.tag import (
    CharacterSourceLinkCreate,
    CharacterSourceLinkListResponse,
    CharacterSourceLinkResponse,
    LinkedTag,
    TagCreate,
    TagExternalLinkCreate,
    TagExternalLinkResponse,
    TagListResponse,
    TagResponse,
    TagWithStats,
)

router = APIRouter(prefix="/tags", tags=["tags"])

# Separate router for character-source-links (mounted at /api/v1/character-source-links)
character_source_links_router = APIRouter(
    prefix="/character-source-links", tags=["character-source-links"]
)

# MySQL/MariaDB default fulltext stopwords that cause search failures when used with `+` (required) operator
# Source: INFORMATION_SCHEMA.INNODB_FT_DEFAULT_STOPWORD
FULLTEXT_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "are",
        "as",
        "at",
        "be",
        "by",
        "com",
        "de",
        "en",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "la",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "who",
        "will",
        "with",
        "und",
        "www",
    }
)

# Minimum token size for InnoDB fulltext (innodb_ft_min_token_size default is 3)
FULLTEXT_MIN_TOKEN_SIZE = 3

# MySQL fulltext boolean operators that need to be stripped from search terms
# These characters have special meaning in BOOLEAN MODE and could cause unexpected behavior
# e.g., "C++" would create "+C++*" which interprets the extra + as operators
FULLTEXT_SPECIAL_CHARS = frozenset('+-~*"()><@')

# Characters that MySQL/MariaDB InnoDB fulltext parser typically treats as word delimiters.
# This is an approximation of default tokenization behavior in BOOLEAN MODE, used only for
# local validation to determine if a search term will produce valid tokens.
#
# Derived from testing against default InnoDB FULLTEXT indexes on MariaDB with utf8mb4.
# Actual delimiters may vary by server version, storage engine, charset, collation, or
# custom fulltext parser configuration. If any change, verify against current behavior.
#
# These split terms into separate tokens, which may result in tokens too short for fulltext.
# e.g., "C.C." becomes ["C", "C"] which are both below min token size.
FULLTEXT_WORD_DELIMITERS = frozenset(" \n\t;:!?.'\"`()[]{}|&/\\,-_=~")

# Pre-computed translation table for efficient delimiter replacement (used by str.translate)
_DELIMITER_TRANS_TABLE = str.maketrans(dict.fromkeys(FULLTEXT_WORD_DELIMITERS, " "))


def _escape_like_pattern(value: str) -> str:
    """Escape special LIKE pattern characters (% and _) in a search value."""
    return value.replace("%", r"\%").replace("_", r"\_")


def _sanitize_fulltext_term(term: str) -> str:
    """Remove MySQL fulltext boolean operators from a search term."""
    return "".join(char for char in term if char not in FULLTEXT_SPECIAL_CHARS)


def _get_fulltext_tokens(term: str) -> list[str]:
    """
    Simulate MySQL fulltext tokenization of a term.

    MySQL splits on word delimiters, so "C.C." becomes ["C", "C"].
    This helps determine if a term will actually produce searchable tokens.
    """
    # Replace all delimiters with spaces using pre-computed translation table, then split
    return term.translate(_DELIMITER_TRANS_TABLE).split()


def _has_valid_fulltext_tokens(term: str) -> bool:
    """
    Check if a term will produce at least one valid fulltext token.

    A token is valid if it's >= FULLTEXT_MIN_TOKEN_SIZE after tokenization.
    e.g., "C.C." -> False (tokens are "C", "C", both < 3)
    e.g., "sakura" -> True (token is "sakura", >= 3)
    """
    tokens = _get_fulltext_tokens(term)
    return any(len(t) >= FULLTEXT_MIN_TOKEN_SIZE for t in tokens)


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


async def get_tag_hierarchy(db: AsyncSession, tag_id: int, max_depth: int = 10) -> list[int]:
    """
    Get all tag IDs in a tag's hierarchy (self + all descendants).

    For parent tags (like "swimsuit"), this returns the parent ID plus all child tag IDs
    (like "school swimsuit", "bikini") that have inheritedfrom_id pointing to it.
    This allows querying a parent tag to include all images tagged with child tags.

    Uses a recursive CTE for single-query performance instead of N+1 queries.

    Args:
        db: Database session
        tag_id: The root tag ID to get hierarchy for
        max_depth: Maximum depth to traverse (default 10, safety limit)

    Returns:
        list[int]: List of tag IDs including the parent and all descendants
    """
    query = text("""
        WITH RECURSIVE tag_tree AS (
            SELECT tag_id, 1 as depth
            FROM tags
            WHERE tag_id = :root_id
            UNION ALL
            SELECT t.tag_id, tt.depth + 1
            FROM tags t
            INNER JOIN tag_tree tt ON t.inheritedfrom_id = tt.tag_id
            WHERE tt.depth < :max_depth
        )
        SELECT tag_id FROM tag_tree
    """)
    result = await db.execute(query, {"root_id": tag_id, "max_depth": max_depth})
    return [row[0] for row in result.fetchall()]


@router.get("/", response_model=TagListResponse, include_in_schema=False)
@router.get("", response_model=TagListResponse)
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
        # Deduplicate tag IDs to prevent duplicate results
        tag_ids = list(dict.fromkeys(tag_ids))
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
            # Escape LIKE special characters to prevent unintended wildcard matching
            escaped_search = _escape_like_pattern(search)
            query = query.where(Tags.title.like(f"{escaped_search}%"))  # type: ignore[union-attr]
        else:
            # Long query: word-order independent full-text search with wildcard expansion
            # Split search into words and add wildcard to each word to match partial terms
            # e.g., "sakura kinomoto" -> "+sakura* +kinomoto*"
            #
            # IMPORTANT: Filter out stopwords and short terms to prevent search failures.
            # MySQL/MariaDB fulltext treats words like "the", "a", "of" as stopwords.
            # When combined with `+` (required), the entire query fails if a stopword is required.
            # Also, terms shorter than innodb_ft_min_token_size (default 3) are ignored.
            # Additionally, strip special fulltext boolean operators from terms to prevent
            # unexpected behavior (e.g., "C++" becoming "+C++*" with extra operators).
            search_terms = search.split()
            # Sanitize terms first, then filter by stopwords and token validity.
            # Order matters: we need to check stopwords and tokens AFTER sanitization.
            # e.g., "+the+" becomes "the" (a stopword), and "C++" becomes "C" (too short).
            #
            # IMPORTANT: We check _has_valid_fulltext_tokens() to handle cases like "C.C."
            # where the overall length >= 3, but MySQL tokenizes it into "C", "C" which
            # are both below the minimum token size. Such terms should fall back to LIKE.
            valid_terms = []
            for term in search_terms:
                sanitized = _sanitize_fulltext_term(term)
                if not sanitized:
                    continue
                if sanitized.lower() in FULLTEXT_STOPWORDS:
                    continue
                # Check both overall length AND whether MySQL will produce valid tokens
                if len(sanitized) >= FULLTEXT_MIN_TOKEN_SIZE and _has_valid_fulltext_tokens(
                    sanitized
                ):
                    valid_terms.append(sanitized)

            if valid_terms:
                # Build fulltext query with valid terms only
                fulltext_query_str = " ".join(f"+{term}*" for term in valid_terms)
                query = query.where(
                    text("MATCH (tags.title) AGAINST (:search IN BOOLEAN MODE)").bindparams(
                        search=fulltext_query_str
                    )
                )
            else:
                # All terms were stopwords or too short - fall back to LIKE prefix search
                # This handles edge cases like searching for "The" or "A" or "The A"
                # Escape LIKE special characters to prevent unintended wildcard matching
                escaped_search = _escape_like_pattern(search)
                query = query.where(Tags.title.like(f"{escaped_search}%"))  # type: ignore[union-attr]
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
        if len(search) < 3 or not fulltext_query_str:
            # Short query or fallback to LIKE (prefix match): prioritize exact and starts-with
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
    query = (
        select(Images)
        .options(
            selectinload(Images.user).load_only(Users.user_id, Users.username, Users.avatar)  # type: ignore[arg-type]
        )
        .join(
            image_id_subquery,
            Images.image_id == image_id_subquery.columns.image_id,  # type: ignore[arg-type]
        )
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


@router.get("/{tag_id}/characters", response_model=TagListResponse)
async def get_characters_for_source(
    tag_id: Annotated[int, Path(description="Source tag ID")],
    pagination: Annotated[PaginationParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> TagListResponse:
    """
    Get all character tags linked to a source tag.

    Returns a paginated list of character tags that are linked to the specified source.
    Returns 400 if the tag is not a Source type.
    Returns 404 if the tag doesn't exist.
    """
    # Verify tag exists and is a source
    tag_result = await db.execute(
        select(Tags).where(Tags.tag_id == tag_id)  # type: ignore[arg-type]
    )
    tag = tag_result.scalar_one_or_none()

    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    if tag.type != TagType.SOURCE:
        raise HTTPException(
            status_code=400,
            detail=f"Tag must be a Source tag (type={TagType.SOURCE}), got type={tag.type}",
        )

    # Get linked characters
    query = (
        select(Tags)
        .join(
            CharacterSourceLinks,
            Tags.tag_id == CharacterSourceLinks.character_tag_id,  # type: ignore[arg-type]
        )
        .where(CharacterSourceLinks.source_tag_id == tag_id)  # type: ignore[arg-type]
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Paginate and order
    query = query.order_by(Tags.title).offset(pagination.offset).limit(pagination.per_page)

    result = await db.execute(query)
    tags = result.scalars().all()

    return TagListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        tags=[TagResponse.model_validate(t) for t in tags],
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
    - External links (URLs associated with the tag)
    """
    # First resolve any alias (synonym) - join with Users to get creator info
    # Eager load user groups for UserSummary
    tag_result = await db.execute(
        select(Tags, Users)
        .outerjoin(Users, Tags.user_id == Users.user_id)  # type: ignore[arg-type]
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
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
        created_by = UserSummary(
            user_id=user.user_id or 0,
            username=user.username,
            avatar=user.avatar or None,
            groups=user.groups,  # Uses the eager-loaded groups property
        )

    # Fetch external links for this tag
    links_result = await db.execute(
        select(TagExternalLinks.url)  # type: ignore[call-overload]
        .where(TagExternalLinks.tag_id == tag_id)
        .order_by(TagExternalLinks.date_added)
    )
    links = links_result.scalars().all()

    # Fetch linked sources/characters based on tag type
    sources: list[dict[str, Any]] = []
    characters: list[dict[str, Any]] = []

    if tag.type == TagType.CHARACTER:
        # Get all sources linked to this character
        sources_result = await db.execute(
            select(Tags.tag_id, Tags.title, Tags.type)  # type: ignore[call-overload]
            .join(
                CharacterSourceLinks,
                Tags.tag_id == CharacterSourceLinks.source_tag_id,
            )
            .where(CharacterSourceLinks.character_tag_id == tag_id)
            .order_by(Tags.title)
        )
        sources = [
            {"tag_id": row[0], "title": row[1], "type": row[2]} for row in sources_result.all()
        ]

    elif tag.type == TagType.SOURCE:
        # Get all characters linked to this source
        characters_result = await db.execute(
            select(Tags.tag_id, Tags.title, Tags.type)  # type: ignore[call-overload]
            .join(
                CharacterSourceLinks,
                Tags.tag_id == CharacterSourceLinks.character_tag_id,
            )
            .where(CharacterSourceLinks.source_tag_id == tag_id)
            .order_by(Tags.title)
        )
        characters = [
            {"tag_id": row[0], "title": row[1], "type": row[2]} for row in characters_result.all()
        ]

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
        links=list(links),
        sources=sources,
        characters=characters,
    )


@router.get("/{tag_id}/history", response_model=TagAuditLogListResponse)
async def get_tag_history(
    tag_id: Annotated[int, Path(description="Tag ID")],
    pagination: Annotated[PaginationParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> TagAuditLogListResponse:
    """
    Get tag metadata change history.

    Returns paginated list of all metadata changes (renames, type changes,
    alias changes, inheritance changes, character-source links).
    """
    # Verify tag exists
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
    if not tag_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Tag not found")

    # Query audit log entries
    # Include where tag_id matches OR character_tag_id/source_tag_id matches
    # Eager load user groups for UserSummary
    query = (
        select(TagAuditLog, Users)
        .outerjoin(Users, TagAuditLog.user_id == Users.user_id)  # type: ignore[arg-type]
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(
            (TagAuditLog.tag_id == tag_id)  # type: ignore[arg-type]
            | (TagAuditLog.character_tag_id == tag_id)
            | (TagAuditLog.source_tag_id == tag_id)
        )
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate and order by most recent first
    # Secondary sort by ID for stable ordering when timestamps match
    query = (
        query.order_by(
            desc(TagAuditLog.created_at),  # type: ignore[arg-type]
            desc(TagAuditLog.id),  # type: ignore[arg-type]
        )
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )

    result = await db.execute(query)
    rows = result.all()

    # Batch load all referenced tag IDs to avoid N+1 queries
    # Includes: character_tag, source_tag, alias targets, parent tags
    tag_ids_to_load: set[int] = set()
    for audit, _ in rows:
        if audit.character_tag_id:
            tag_ids_to_load.add(audit.character_tag_id)
        if audit.source_tag_id:
            tag_ids_to_load.add(audit.source_tag_id)
        if audit.old_alias_of:
            tag_ids_to_load.add(audit.old_alias_of)
        if audit.new_alias_of:
            tag_ids_to_load.add(audit.new_alias_of)
        if audit.old_parent_id:
            tag_ids_to_load.add(audit.old_parent_id)
        if audit.new_parent_id:
            tag_ids_to_load.add(audit.new_parent_id)

    tags_map: dict[int, tuple[str | None, int]] = {}  # tag_id -> (title, type)
    if tag_ids_to_load:
        tags_result = await db.execute(
            select(Tags.tag_id, Tags.title, Tags.type).where(  # type: ignore[call-overload]
                Tags.tag_id.in_(tag_ids_to_load)  # type: ignore[union-attr]
            )
        )
        tags_map = {row[0]: (row[1], row[2]) for row in tags_result.all()}

    items = []
    for audit, user in rows:
        user_summary = None
        if user:
            user_summary = UserSummary(
                user_id=user.user_id,
                username=user.username,
                avatar=user.avatar,
                groups=user.groups if user else [],
            )

        response = TagAuditLogResponse(
            id=audit.id,
            tag_id=audit.tag_id,
            action_type=audit.action_type,
            old_title=audit.old_title,
            new_title=audit.new_title,
            old_type=audit.old_type,
            new_type=audit.new_type,
            old_alias_of=audit.old_alias_of,
            new_alias_of=audit.new_alias_of,
            old_parent_id=audit.old_parent_id,
            new_parent_id=audit.new_parent_id,
            user=user_summary,
            created_at=audit.created_at,
        )

        # Enrich with resolved tag info for related tags
        # Character-source links
        if audit.character_tag_id and audit.source_tag_id:
            char_info = tags_map.get(audit.character_tag_id)
            if char_info:
                response.character_tag = LinkedTag(
                    tag_id=audit.character_tag_id, title=char_info[0], type=char_info[1]
                )

            source_info = tags_map.get(audit.source_tag_id)
            if source_info:
                response.source_tag = LinkedTag(
                    tag_id=audit.source_tag_id, title=source_info[0], type=source_info[1]
                )

        # Alias changes - resolve the target tag
        alias_target_id = audit.new_alias_of or audit.old_alias_of
        if alias_target_id:
            alias_info = tags_map.get(alias_target_id)
            if alias_info:
                response.alias_tag = LinkedTag(
                    tag_id=alias_target_id, title=alias_info[0], type=alias_info[1]
                )

        # Parent changes - resolve the parent tag
        parent_target_id = audit.new_parent_id or audit.old_parent_id
        if parent_target_id:
            parent_info = tags_map.get(parent_target_id)
            if parent_info:
                response.parent_tag = LinkedTag(
                    tag_id=parent_target_id, title=parent_info[0], type=parent_info[1]
                )

        items.append(response)

    return TagAuditLogListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        items=items,
    )


@router.get("/{tag_id}/usage-history", response_model=TagHistoryListResponse)
async def get_tag_usage_history(
    tag_id: Annotated[int, Path(description="Tag ID")],
    pagination: Annotated[PaginationParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> TagHistoryListResponse:
    """
    Get tag usage history (add/remove on images).

    Returns paginated list of when this tag was added or removed from images.
    """
    # Verify tag exists
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
    if not tag_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Tag not found")

    # Query tag history with user info
    # Eager load user groups for UserSummary
    query = (
        select(TagHistory, Users)
        .outerjoin(Users, TagHistory.user_id == Users.user_id)  # type: ignore[arg-type]
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(TagHistory.tag_id == tag_id)  # type: ignore[arg-type]
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
    for history, user in rows:
        user_summary = None
        if user:
            user_summary = UserSummary(
                user_id=user.user_id,
                username=user.username,
                avatar=user.avatar,
                groups=user.groups if user else [],
            )

        items.append(
            TagHistoryResponse(
                tag_history_id=history.tag_history_id,
                image_id=history.image_id,
                tag_id=history.tag_id,
                action="added" if history.action == "a" else "removed",
                user=user_summary,
                date=history.date,
            )
        )

    return TagHistoryListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        items=items,
    )


@router.post("/", response_model=TagResponse, include_in_schema=False)
@router.post("", response_model=TagResponse)
async def create_tag(
    tag_data: TagCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
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
        user_id=current_user.user_id,
    )
    db.add(new_tag)
    await db.commit()
    await db.refresh(new_tag)

    return TagResponse.model_validate(new_tag)


@router.put("/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: Annotated[int, Path(description="Tag ID")],
    tag_data: TagCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
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

    # Store original values for audit logging
    original_title = tag.title
    original_type = tag.type
    original_alias_of = tag.alias_of
    original_inheritedfrom_id = tag.inheritedfrom_id

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

    # Create audit log entries for changes
    # Check for title change (rename)
    if tag.title != original_title:
        audit_entry = TagAuditLog(
            tag_id=tag_id,
            action_type=TagAuditActionType.RENAME,
            old_title=original_title,
            new_title=tag.title,
            user_id=current_user.user_id,
        )
        db.add(audit_entry)

    # Check for type change
    if tag.type != original_type:
        audit_entry = TagAuditLog(
            tag_id=tag_id,
            action_type=TagAuditActionType.TYPE_CHANGE,
            old_type=original_type,
            new_type=tag.type,
            user_id=current_user.user_id,
        )
        db.add(audit_entry)

    # Check for alias change
    if tag.alias_of != original_alias_of:
        if original_alias_of is None and tag.alias_of is not None:
            # Alias was set
            audit_entry = TagAuditLog(
                tag_id=tag_id,
                action_type=TagAuditActionType.ALIAS_SET,
                old_alias_of=original_alias_of,
                new_alias_of=tag.alias_of,
                user_id=current_user.user_id,
            )
            db.add(audit_entry)
        elif original_alias_of is not None and tag.alias_of is None:
            # Alias was removed
            audit_entry = TagAuditLog(
                tag_id=tag_id,
                action_type=TagAuditActionType.ALIAS_REMOVED,
                old_alias_of=original_alias_of,
                new_alias_of=tag.alias_of,
                user_id=current_user.user_id,
            )
            db.add(audit_entry)
        else:
            # Alias was changed (from one to another)
            # This is a removal of old alias and setting of new alias
            audit_entry = TagAuditLog(
                tag_id=tag_id,
                action_type=TagAuditActionType.ALIAS_REMOVED,
                old_alias_of=original_alias_of,
                new_alias_of=None,
                user_id=current_user.user_id,
            )
            db.add(audit_entry)
            audit_entry = TagAuditLog(
                tag_id=tag_id,
                action_type=TagAuditActionType.ALIAS_SET,
                old_alias_of=None,
                new_alias_of=tag.alias_of,
                user_id=current_user.user_id,
            )
            db.add(audit_entry)

    # Check for parent (inheritedfrom_id) change
    if tag.inheritedfrom_id != original_inheritedfrom_id:
        if original_inheritedfrom_id is None and tag.inheritedfrom_id is not None:
            # Parent was set
            audit_entry = TagAuditLog(
                tag_id=tag_id,
                action_type=TagAuditActionType.PARENT_SET,
                old_parent_id=original_inheritedfrom_id,
                new_parent_id=tag.inheritedfrom_id,
                user_id=current_user.user_id,
            )
            db.add(audit_entry)
        elif original_inheritedfrom_id is not None and tag.inheritedfrom_id is None:
            # Parent was removed
            audit_entry = TagAuditLog(
                tag_id=tag_id,
                action_type=TagAuditActionType.PARENT_REMOVED,
                old_parent_id=original_inheritedfrom_id,
                new_parent_id=tag.inheritedfrom_id,
                user_id=current_user.user_id,
            )
            db.add(audit_entry)
        else:
            # Parent was changed (from one to another)
            # This is a removal of old parent and setting of new parent
            audit_entry = TagAuditLog(
                tag_id=tag_id,
                action_type=TagAuditActionType.PARENT_REMOVED,
                old_parent_id=original_inheritedfrom_id,
                new_parent_id=None,
                user_id=current_user.user_id,
            )
            db.add(audit_entry)
            audit_entry = TagAuditLog(
                tag_id=tag_id,
                action_type=TagAuditActionType.PARENT_SET,
                old_parent_id=None,
                new_parent_id=tag.inheritedfrom_id,
                user_id=current_user.user_id,
            )
            db.add(audit_entry)

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


@router.post("/{tag_id}/links", response_model=TagExternalLinkResponse, status_code=201)
async def add_tag_link(
    tag_id: Annotated[int, Path(description="Tag ID")],
    link_data: TagExternalLinkCreate,
    _: Annotated[None, Depends(require_permission(Permission.TAG_UPDATE))],
    db: AsyncSession = Depends(get_db),
) -> TagExternalLinkResponse:
    """
    Add an external link to a tag.

    Requires TAG_UPDATE permission.
    Returns 404 if tag doesn't exist.
    Returns 409 if URL already exists for this tag.
    """
    # Verify tag exists
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
    if not tag_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Tag not found")

    # Create new link
    new_link = TagExternalLinks(tag_id=tag_id, url=link_data.url)
    db.add(new_link)

    try:
        await db.commit()
        await db.refresh(new_link)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="URL already exists for this tag") from None

    return TagExternalLinkResponse.model_validate(new_link)


@router.delete("/{tag_id}/links/{link_id}", status_code=204)
async def delete_tag_link(
    tag_id: Annotated[int, Path(description="Tag ID")],
    link_id: Annotated[int, Path(description="Link ID")],
    _: Annotated[None, Depends(require_permission(Permission.TAG_UPDATE))],
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Remove an external link from a tag.

    Requires TAG_UPDATE permission.
    Returns 404 if link doesn't exist or doesn't belong to the specified tag.
    """
    # Fetch and verify link belongs to this tag
    link_result = await db.execute(
        select(TagExternalLinks)
        .where(TagExternalLinks.link_id == link_id)  # type: ignore[arg-type]
        .where(TagExternalLinks.tag_id == tag_id)  # type: ignore[arg-type]
    )
    link = link_result.scalar_one_or_none()

    if not link:
        raise HTTPException(
            status_code=404,
            detail="Link not found or does not belong to this tag",
        )

    await db.delete(link)
    await db.commit()


# =============================================================================
# Character-Source Links Endpoints
# =============================================================================


@character_source_links_router.post(
    "/", response_model=CharacterSourceLinkResponse, status_code=201, include_in_schema=False
)
@character_source_links_router.post("", response_model=CharacterSourceLinkResponse, status_code=201)
async def create_character_source_link(
    link_data: CharacterSourceLinkCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.TAG_CREATE))],
    db: AsyncSession = Depends(get_db),
) -> CharacterSourceLinkResponse:
    """Create a character-source link. Requires TAG_CREATE permission."""
    # Verify character tag exists and is type CHARACTER
    char_result = await db.execute(
        select(Tags).where(Tags.tag_id == link_data.character_tag_id)  # type: ignore[arg-type]
    )
    char_tag = char_result.scalar_one_or_none()
    if not char_tag:
        raise HTTPException(status_code=404, detail="Character tag not found")
    if char_tag.type != TagType.CHARACTER:
        raise HTTPException(
            status_code=400,
            detail=f"character_tag_id must be a Character tag (type={TagType.CHARACTER}), got type={char_tag.type}",
        )

    # Verify source tag exists and is type SOURCE
    source_result = await db.execute(
        select(Tags).where(Tags.tag_id == link_data.source_tag_id)  # type: ignore[arg-type]
    )
    source_tag = source_result.scalar_one_or_none()
    if not source_tag:
        raise HTTPException(status_code=404, detail="Source tag not found")
    if source_tag.type != TagType.SOURCE:
        raise HTTPException(
            status_code=400,
            detail=f"source_tag_id must be a Source tag (type={TagType.SOURCE}), got type={source_tag.type}",
        )

    # Create link
    new_link = CharacterSourceLinks(
        character_tag_id=link_data.character_tag_id,
        source_tag_id=link_data.source_tag_id,
        created_by_user_id=current_user.user_id,
    )
    db.add(new_link)

    # Log audit trail for character tag
    audit = TagAuditLog(
        tag_id=link_data.character_tag_id,
        action_type=TagAuditActionType.SOURCE_LINKED,
        character_tag_id=link_data.character_tag_id,
        source_tag_id=link_data.source_tag_id,
        user_id=current_user.user_id,
    )
    db.add(audit)

    try:
        await db.commit()
        await db.refresh(new_link)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Link between this character and source already exists",
        ) from None

    return CharacterSourceLinkResponse.model_validate(new_link)


@character_source_links_router.get(
    "/", response_model=CharacterSourceLinkListResponse, include_in_schema=False
)
@character_source_links_router.get("", response_model=CharacterSourceLinkListResponse)
async def list_character_source_links(
    pagination: Annotated[PaginationParams, Depends()],
    character_tag_id: Annotated[int | None, Query(description="Filter by character tag ID")] = None,
    source_tag_id: Annotated[int | None, Query(description="Filter by source tag ID")] = None,
    db: AsyncSession = Depends(get_db),
) -> CharacterSourceLinkListResponse:
    """List character-source links with optional filtering."""
    query = select(CharacterSourceLinks)

    if character_tag_id is not None:
        query = query.where(
            CharacterSourceLinks.character_tag_id == character_tag_id  # type: ignore[arg-type]
        )
    if source_tag_id is not None:
        query = query.where(
            CharacterSourceLinks.source_tag_id == source_tag_id  # type: ignore[arg-type]
        )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Paginate and order
    query = (
        query.order_by(desc(CharacterSourceLinks.created_at))  # type: ignore[arg-type]
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )

    result = await db.execute(query)
    links = result.scalars().all()

    return CharacterSourceLinkListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        links=[CharacterSourceLinkResponse.model_validate(link) for link in links],
    )


@character_source_links_router.delete("/{link_id}", status_code=204)
async def delete_character_source_link(
    link_id: Annotated[int, Path(description="Link ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.TAG_CREATE))],
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a character-source link. Requires TAG_CREATE permission."""
    result = await db.execute(
        select(CharacterSourceLinks).where(
            CharacterSourceLinks.id == link_id  # type: ignore[arg-type]
        )
    )
    link = result.scalar_one_or_none()

    if not link:
        raise HTTPException(status_code=404, detail="Link not found")

    # Log audit trail before deleting
    audit = TagAuditLog(
        tag_id=link.character_tag_id,
        action_type=TagAuditActionType.SOURCE_UNLINKED,
        character_tag_id=link.character_tag_id,
        source_tag_id=link.source_tag_id,
        user_id=current_user.user_id,
    )
    db.add(audit)

    await db.delete(link)
    await db.commit()
