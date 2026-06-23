"""
Images API endpoints
"""

import math
import random
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path as FilePath
from typing import Annotated, Any

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
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import and_, asc, delete, desc, func, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.api.dependencies import (
    ImageRatingsSortParams,
    ImageSortParams,
    PaginationParams,
    UserSortParams,
)
from app.api.v1.tags import get_tag_hierarchy, resolve_tag_alias
from app.config import (
    AdminActionType,
    DeactivationReason,
    ImageStatus,
    ReportCategory,
    ReportStatus,
    ReviewOutcome,
    ReviewStatus,
    settings,
)
from app.core.auth import CurrentUser, VerifiedUser, get_current_user, get_optional_current_user
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.permission_deps import require_permission
from app.core.permissions import Permission, has_any_permission, has_permission
from app.core.r2_constants import R2Location
from app.core.redis import get_redis
from app.models import (
    AdminActions,
    Comments,
    Favorites,
    ImageRatings,
    ImageReports,
    ImageReportTagSuggestions,
    ImageReviews,
    Images,
    TagHistory,
    TagLinks,
    Tags,
    Users,
)
from app.models.image import ImageSortBy, VariantStatus
from app.models.image_status_history import ImageStatusHistory
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.permissions import UserGroups
from app.schemas.audit import (
    ImageReviewListResponse,
    ImageReviewPublicResponse,
    ImageStatusHistoryListResponse,
    ImageStatusHistoryResponse,
    ImageTagHistoryListResponse,
    ImageTagHistoryResponse,
)
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
    ImageUpdate,
    ImageUploadDuplicateResponse,
    ImageUploadResponse,
    ImageUploadSimilarResponse,
    SimilarImageResult,
    SimilarImagesResponse,
    SimilarImagesUploadResponse,
)
from app.schemas.report import (
    ReportCreate,
    ReportListResponse,
    ReportResponse,
    SkippedTagsInfo,
    TagSuggestion,
)
from app.schemas.tag import LinkedTag
from app.schemas.user import (
    ImageRatingsListResponse,
    UserListResponse,
    UserResponse,
    UserWithRatingResponse,
)
from app.services.feed_count_cache import get_feed_counts
from app.services.image_processing import (
    create_thumbnail,
    get_image_dimensions,
    validate_image_file,
)
from app.services.image_status import enqueue_r2_sync_on_status_change
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES
from app.services.iqdb import check_iqdb_similarity, check_iqdb_similarity_by_hash, remove_from_iqdb
from app.services.rate_limit import check_similarity_rate_limit
from app.services.rating import recalculate_image_ratings
from app.services.search import sync_tag_to_search
from app.services.tag_type_flags import refresh_image_tag_type_flags
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


async def _hydrate_similar_images(
    iqdb_results: list[dict[str, int | float]],
    db: AsyncSession,
) -> list[SimilarImageResult]:
    """Fetch full image data for IQDB results and build SimilarImageResult list."""
    similar_ids = [r["image_id"] for r in iqdb_results]
    images_result = await db.execute(
        select(Images)
        .options(
            selectinload(Images.user).load_only(  # type: ignore[arg-type]
                Users.user_id,  # type: ignore[arg-type]
                Users.username,  # type: ignore[arg-type]
                Users.avatar,  # type: ignore[arg-type]
                Users.avatar_in_r2,  # type: ignore[arg-type]
                Users.user_title,  # type: ignore[arg-type]
            )
        )
        .where(Images.image_id.in_(similar_ids))  # type: ignore[union-attr]
    )
    images_by_id: dict[int, Images] = {
        img.image_id: img  # type: ignore[misc]
        for img in images_result.scalars().all()
    }

    similar: list[SimilarImageResult] = []
    for r in sorted(iqdb_results, key=lambda x: x["score"], reverse=True):
        img = images_by_id.get(int(r["image_id"]))
        if img:
            img_data = ImageResponse.model_validate(img).model_dump()
            img_data["similarity_score"] = r["score"]
            similar.append(SimilarImageResult(**img_data))
    return similar


@router.post("/check-similar", response_model=SimilarImagesUploadResponse)
async def check_similar_by_upload(
    file: Annotated[UploadFile, File(description="Image file to check for similarity")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    threshold: Annotated[
        float | None, Query(description="Minimum similarity score (0-100)", ge=0, le=100)
    ] = None,
) -> SimilarImagesUploadResponse:
    """Check an uploaded image for similar images in the database.

    Accepts a temporary image upload, queries IQDB for similar matches,
    and returns results. The uploaded image is not stored permanently.
    """
    await check_similarity_rate_limit(current_user.id, redis_client)

    temp_dir = tempfile.mkdtemp()
    try:
        # Derive safe extension from filename (avoid path traversal via user-controlled input)
        original_suffix = FilePath(file.filename or "").suffix
        safe_suffix = original_suffix if original_suffix else ".jpg"
        temp_path = FilePath(temp_dir) / f"temp-0{safe_suffix}"

        # Stream upload to disk in chunks while enforcing MAX_IMAGE_SIZE
        max_size = settings.MAX_IMAGE_SIZE
        chunk_size = 1024 * 1024  # 1 MB
        total_size = 0
        with temp_path.open("wb") as out_file:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_size:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File too large. Maximum size is {max_size // (1024 * 1024)}MB",
                    )
                out_file.write(chunk)

        # Validate it's a real image
        validate_image_file(file, temp_path)

        # Generate temp thumbnail for IQDB query
        create_thumbnail(temp_path, 0, temp_path.suffix.lstrip("."), temp_dir)
        thumb_path = FilePath(temp_dir) / "thumbs" / "temp-0.webp"

        if not thumb_path.exists():
            logger.warning("check_similar_thumbnail_failed", user_id=current_user.id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to generate thumbnail for similarity check",
            )

        # Query IQDB
        similar_results = await check_iqdb_similarity(thumb_path, db, threshold=threshold)

        if not similar_results:
            return SimilarImagesUploadResponse(similar_images=[])

        similar_images = await _hydrate_similar_images(similar_results, db)
        return SimilarImagesUploadResponse(similar_images=similar_images)

    finally:
        try:
            shutil.rmtree(temp_dir)
        except OSError:
            logger.warning("check_similar_cleanup_failed", temp_dir=temp_dir)


async def _open_report_image_ids(
    db: AsyncSession,
    image_ids: list[int | None],
    viewer: Users | None,
    redis_client: redis.Redis | None = None,  # type: ignore[type-arg]
) -> set[int]:
    """Subset of `image_ids` that have a PENDING report — only for REPORT_VIEW viewers.

    One query for the whole page (not N+1). Returns an empty set for non-mods so the
    has_open_report flag never leaks to regular users. `redis_client` routes the
    permission check through the TTL cache (this runs on the hot image-list path).
    """
    ids = [i for i in image_ids if i is not None]
    if not ids or viewer is None or viewer.user_id is None:
        return set()
    if not await has_any_permission(db, viewer.user_id, [Permission.REPORT_VIEW], redis_client):
        return set()
    rows = await db.execute(
        select(ImageReports.image_id)  # type: ignore[call-overload]
        .where(
            ImageReports.image_id.in_(ids),  # type: ignore[attr-defined]
            ImageReports.status == ReportStatus.PENDING,
        )
        .distinct()
    )
    return {r[0] for r in rows.all()}


@dataclass(frozen=True)
class _FeedFilters:
    """The content filters ``list_images`` can apply.

    The *bare* default feed is "every one of these empty" — the only shape the fast
    hidden-complement count is valid for. Adding a new content filter to ``list_images``
    means adding a field here and passing it in at the count site; this dataclass is the
    single source the fast-path consults, so the ``== _FeedFilters()`` check covers any
    new field automatically (unlike the old hand-listed per-field AND, where a forgotten
    line silently returned a wrong total).

    Mode/depth params (``tags_mode``, ``tag_depth``, ``*_mode``) are intentionally absent:
    they only refine an already-set filter and never narrow the bare feed on their own.
    """

    image_status: list[int] | None = None
    user_id: int | None = None
    favorited_by_user_id: int | None = None
    tags: str | None = None
    exclude_tags: str | None = None
    missing_tag_types: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    min_width: int | None = None
    max_width: int | None = None
    min_height: int | None = None
    max_height: int | None = None
    min_rating: float | None = None
    min_favorites: int | None = None
    min_num_ratings: int | None = None
    commenter: int | None = None
    commentsearch: str | None = None
    hascomments: bool | None = None
    reported: bool | None = None


async def _default_feed_total(
    db: AsyncSession,
    current_user: Users | None,
    redis_client: redis.Redis | None = None,  # type: ignore[type-arg]
) -> int:
    """Fast pagination total for the *bare* default feed (visibility filter only).

    The naive count is ``count(visible OR mine)``, where ``visible`` is ~99% of the
    table — no index can serve the OR, so it's a full clustered-index scan (seconds on
    a 1M-row table). Instead, count from the small *hidden* complement::

        total = count(all) - count(hidden AND not-mine)
              = count(visible) + count(viewer's own hidden images)

    The two global counts come from a short-TTL cache (they lag a mutation by at most the
    TTL); the per-viewer ``own hidden`` slice is tiny and queried live. Branches mirror
    the visibility filter in list_images exactly:
    show_all_images=1 => no filter; anonymous => public only; otherwise public + own.
    """
    total_all, hidden_count, repost_count = await get_feed_counts(db, redis_client)
    hide_reposts = current_user is not None and current_user.hide_reposts == 1

    # show_all_images=1: every image counts (minus reposts if the viewer hides them).
    # max(0, ...): the three cached counts are separate queries, so a concurrent mutation
    # between them could momentarily make repost_count exceed the base — clamp so the
    # pagination total never goes negative.
    if current_user is not None and current_user.show_all_images == 1:
        return max(0, total_all - (repost_count if hide_reposts else 0))

    visible_count = total_all - hidden_count  # count(PUBLIC) = active + spoiler + repost
    if hide_reposts:
        # All reposts are public/global, so this also removes the viewer's own reposts.
        visible_count = max(0, visible_count - repost_count)

    # Logged-in + show_all=0: also count the viewer's own (hidden) images. Equality on
    # user_id (not `!= me`) keeps NULL-owner rows correct.
    if current_user is not None and current_user.user_id is not None:
        my_hidden_count = (
            await db.execute(
                select(func.count())
                .select_from(Images)
                .where(
                    Images.status.notin_(PUBLIC_IMAGE_STATUSES),  # type: ignore[attr-defined]
                    Images.user_id == current_user.user_id,  # type: ignore[arg-type]
                )
            )
        ).scalar() or 0
        return visible_count + my_hidden_count

    # Anonymous: public statuses only (anonymous users have no hide_reposts).
    return visible_count


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
        list[int] | None,
        Query(
            description="Filter by status (1=active, 2=spoiler, etc). Repeat for multiple.",
            alias="status",
        ),
    ] = None,
    # Tag filtering
    tags: Annotated[
        str | None, Query(description="Comma-separated tag IDs (e.g., '1,2,3')")
    ] = None,
    tags_mode: Annotated[
        str, Query(pattern="^(any|all)$", description="Match ANY or ALL tags")
    ] = "any",
    tag_depth: Annotated[
        int | None,
        Query(
            ge=0,
            le=9,
            description="How many levels of child tags to include. "
            "0=exact tag only, 1=tag+direct children, ..., 9=up to 9 levels. "
            "Omit for full hierarchy.",
        ),
    ] = None,
    exclude_tags: Annotated[
        str | None, Query(description="Comma-separated tag IDs to exclude (e.g., '4,5,6')")
    ] = None,
    exclude_descendants: Annotated[
        bool,
        Query(
            description="When true, each excluded tag also excludes its child tags "
            "(its full hierarchy). Default false (exact-match exclusion)."
        ),
    ] = False,
    missing_tag_types: Annotated[
        str | None,
        Query(
            description="Comma-separated tag type IDs the image must be MISSING "
            "(1=Theme, 2=Source, 3=Artist, 4=Character)."
        ),
    ] = None,
    missing_tag_types_mode: Annotated[
        str, Query(pattern="^(any|all)$", description="Match ANY or ALL missing types")
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
    reported: Annotated[
        bool | None,
        Query(
            description="Filter to images with a pending report. Requires report_view; "
            "ignored for other viewers."
        ),
    ] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Users | None = Depends(get_optional_current_user),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> ImageDetailedListResponse:
    """
    Search and list images with comprehensive filtering.

    **Supports:**
    - Pagination (page, per_page)
    - Sorting by any field
    - Tag filtering (by ID, with ANY/ALL modes and tag exclusion)
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
    - `/images?tags=1&exclude_tags=2,3` - Images with tag 1 but NOT tags 2 or 3
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
        # Explicit status filter - always honor it (supports single or multiple values)
        query = query.where(Images.status.in_(image_status))  # type: ignore[attr-defined]
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

        # hide_reposts preference — applied in the no-explicit-status branch, outside the
        # show_all sub-block so it covers both show_all=0 and show_all=1. This is a global
        # exclusion (not ownership-aware), so the viewer's own reposts are dropped too.
        # An explicit ?status= takes the other branch and overrides this.
        if current_user is not None and current_user.hide_reposts == 1:
            query = query.where(Images.status != ImageStatus.REPOST)  # type: ignore[arg-type]

    # Tag filtering
    tag_ids: list[int] = []
    if tags:
        # isdecimal() (not isdigit()): int() rejects chars like '²' that isdigit() matches.
        tag_ids = [int(tid.strip()) for tid in tags.split(",") if tid.strip().isdecimal()]
        if len(tag_ids) > settings.MAX_SEARCH_TAGS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You can only search for up to {settings.MAX_SEARCH_TAGS} tags at a time.",
            )
        if tag_ids:
            # Map tag_depth to max_depth for the CTE query
            # tag_depth=0 → max_depth=1 (root only), tag_depth=1 → max_depth=2, etc.
            # None → default (10, full hierarchy)
            hierarchy_max_depth = tag_depth + 1 if tag_depth is not None else 10

            if tags_mode == "all":
                # Images must have ALL specified tags (including their descendants)
                for tag_id in tag_ids:
                    _, resolved_tag_id = await resolve_tag_alias(db, tag_id)
                    # Expand hierarchy to configured depth
                    hierarchy_ids = await get_tag_hierarchy(
                        db, resolved_tag_id, max_depth=hierarchy_max_depth
                    )
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
                    hierarchy_ids = await get_tag_hierarchy(
                        db, resolved_tag_id, max_depth=hierarchy_max_depth
                    )
                    all_hierarchy_ids.update(hierarchy_ids)
                query = query.where(
                    Images.image_id.in_(  # type: ignore[union-attr]
                        select(TagLinks.image_id).where(TagLinks.tag_id.in_(all_hierarchy_ids))  # type: ignore[call-overload,attr-defined]
                    )
                )

    # Exclude tag filtering. Exact match by default; with exclude_descendants the
    # excluded tag's whole subtree is removed too (mirrors the include-side hierarchy).
    if exclude_tags:
        # isdecimal() (not isdigit()): int() rejects chars like '²' that isdigit() matches.
        exclude_tag_ids = [
            int(tid.strip()) for tid in exclude_tags.split(",") if tid.strip().isdecimal()
        ]
        if exclude_tag_ids:
            # Enforce shared MAX_SEARCH_TAGS limit across include + exclude
            total_tag_count = len(tag_ids) + len(exclude_tag_ids)
            if total_tag_count > settings.MAX_SEARCH_TAGS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"You can only search for up to {settings.MAX_SEARCH_TAGS} tags at a time.",
                )

            # Resolve aliases, then optionally expand each to its full subtree.
            resolved_exclude_ids: set[int] = set()
            for etid in exclude_tag_ids:
                _, resolved_etid = await resolve_tag_alias(db, etid)
                if exclude_descendants:
                    resolved_exclude_ids.update(await get_tag_hierarchy(db, resolved_etid))
                else:
                    resolved_exclude_ids.add(resolved_etid)

            # Apply NOT IN subquery
            query = query.where(
                Images.image_id.notin_(  # type: ignore[union-attr]
                    select(TagLinks.image_id).where(TagLinks.tag_id.in_(resolved_exclude_ids))  # type: ignore[call-overload,attr-defined]
                )
            )

    # Missing tag-type filtering (images lacking a tag of the given type[s]).
    # Aliases are intentionally NOT resolved here: the applied tag's own `type` is authoritative.
    if missing_tag_types:
        # Reject any token that is not a valid type ID (non-digit or out of range), rather
        # than silently dropping it. Empty tokens are ignored (empty param = no filter).
        raw_tokens = [t.strip() for t in missing_tag_types.split(",") if t.strip()]
        # isdecimal() (not isdigit()): int() only parses decimal digits, while isdigit()
        # also matches chars like '²' that int() rejects (would otherwise 500 on int()).
        invalid = [t for t in raw_tokens if not (t.isdecimal() and int(t) in {1, 2, 3, 4})]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid tag type(s): {', '.join(invalid)}. "
                    "Valid types are 1=Theme, 2=Source, 3=Artist, 4=Character."
                ),
            )
        missing_type_ids = sorted({int(t) for t in raw_tokens})
        if missing_type_ids:
            type_column = {
                1: Images.has_theme,
                2: Images.has_source,
                3: Images.has_artist,
                4: Images.has_character,
            }
            # "missing type T" == that image's has_<type> flag is False.
            clauses = [
                type_column[t] == False  # noqa: E712
                for t in missing_type_ids
            ]
            if missing_tag_types_mode == "all":
                query = query.where(and_(*clauses))  # type: ignore[arg-type]  # missing every listed type
            else:
                query = query.where(or_(*clauses))  # type: ignore[arg-type]  # missing at least one listed type

    # Date filtering. date_from/date_to arrive as "YYYY-MM-DD" strings; the
    # date_added column is a tz-aware UtcDateTime, so the raw string must be
    # parsed to a tz-aware UTC datetime before comparison (otherwise the column's
    # bind hook raises "'str' object has no attribute 'tzinfo'" -> HTTP 500).
    # date_to is treated as inclusive of the whole day by using an exclusive
    # upper bound at the start of the following day.
    if date_from:
        try:
            from_dt = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="date_from must be in YYYY-MM-DD format.",
            ) from None
        query = query.where(Images.date_added >= from_dt)  # type: ignore[arg-type,operator]
    if date_to:
        try:
            to_dt = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="date_to must be in YYYY-MM-DD format.",
            ) from None
        query = query.where(Images.date_added < to_dt + timedelta(days=1))  # type: ignore[arg-type,operator]

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

    # Reported filtering: restrict to images that have a PENDING report. Mods only
    # (REPORT_VIEW) so it never leaks the triage queue — silently a no-op for everyone else.
    apply_reported = (
        bool(reported)
        and current_user is not None
        and current_user.user_id is not None
        and await has_any_permission(
            db, current_user.user_id, [Permission.REPORT_VIEW], redis_client
        )
    )
    if apply_reported:
        query = query.where(
            Images.image_id.in_(  # type: ignore[union-attr]
                select(ImageReports.image_id).where(  # type: ignore[call-overload]
                    ImageReports.status == ReportStatus.PENDING
                )
            )
        )

    # Count total results. For the *bare* default feed (no content filter — only the
    # implicit visibility filter), count(visible OR mine) is a full-table scan; use the
    # fast hidden-complement count instead. ANY explicit filter falls back to the exact
    # subquery count.
    active_filters = _FeedFilters(
        image_status=image_status,
        user_id=user_id,
        favorited_by_user_id=favorited_by_user_id,
        tags=tags or None,
        exclude_tags=exclude_tags or None,
        missing_tag_types=missing_tag_types or None,
        date_from=date_from,
        date_to=date_to,
        min_width=min_width,
        max_width=max_width,
        min_height=min_height,
        max_height=max_height,
        min_rating=min_rating,
        min_favorites=min_favorites,
        min_num_ratings=min_num_ratings,
        commenter=commenter,
        commentsearch=commentsearch,
        hascomments=hascomments,
        reported=apply_reported or None,
    )
    # Bare feed = every content filter empty. One comparison, so a field added to
    # _FeedFilters is covered automatically. Falsy strings (e.g. tags="") normalize to
    # None so an empty filter param still reads as "no filter", matching the old `not x`.
    is_bare_default_feed = active_filters == _FeedFilters()
    if is_bare_default_feed:
        total = await _default_feed_total(db, current_user, redis_client)
    else:
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

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

    # Mod-only open-report indicator (single query for the page).
    open_report_ids = await _open_report_image_ids(
        db, [img.image_id for img in images], current_user, redis_client
    )

    # Moderation reason visibility: mods (IMAGE_EDIT/REVIEW_VIEW) see every reason;
    # owners see their own. The permission check is cache-backed (hot path).
    viewer_can_moderate = (
        current_user is not None
        and current_user.user_id is not None
        and await has_any_permission(
            db, current_user.user_id, [Permission.IMAGE_EDIT, Permission.REVIEW_VIEW], redis_client
        )
    )

    # ML suggestion counts: one grouped query for the page, only for users who
    # hold IMAGE_TAG_ADD or are admins (same predicate as the review queue gate).
    # Anonymous users and plain users always get None (field default).
    # None means "not computed" (no permission); {} means "computed, all zero".
    pending_counts: dict[int, int] | None = None
    if (
        current_user is not None
        and current_user.user_id is not None
        and images
        and (
            current_user.admin
            or await has_permission(
                db, current_user.user_id, Permission.IMAGE_TAG_ADD, redis_client
            )
        )
    ):
        page_ids = [img.image_id for img in images]
        count_result = await db.execute(
            select(MlTagSuggestions.image_id, func.count().label("cnt"))  # type: ignore[call-overload]
            .where(
                MlTagSuggestions.image_id.in_(page_ids),  # type: ignore[attr-defined]
                MlTagSuggestions.status == "pending",
            )
            .group_by(MlTagSuggestions.image_id)
        )
        pending_counts = {row.image_id: row.cnt for row in count_result}

    # Build response items; assign ml_suggestion_count after construction since
    # from_db_model does not accept it as a parameter.
    response_items: list[ImageDetailedResponse] = []
    for img in images:
        item = ImageDetailedResponse.from_db_model(
            img,
            is_favorited=img.image_id in favorited_ids,
            has_open_report=img.image_id in open_report_ids,
            can_see_reason=viewer_can_moderate
            or (current_user is not None and img.user_id == current_user.user_id),
        )
        if pending_counts is not None:
            # Permitted user: set actual count (0 for images absent from grouped result).
            item.ml_suggestion_count = pending_counts.get(img.image_id, 0)  # type: ignore[arg-type]
        response_items.append(item)

    return ImageDetailedListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        images=response_items,
    )


@router.get("/random", include_in_schema=True)
async def random_images_page(
    per_page: Annotated[int | None, Query(ge=1, le=100, description="Items per page")] = None,
    current_user: Users | None = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Redirect to a random page of images.

    Respects user visibility settings (show_all_images).
    Uses the authenticated user's images_per_page preference when per_page is not specified.
    Returns 302 redirect to /?page=N.
    """
    if per_page is None:
        per_page = current_user.images_per_page if current_user else 20

    # Count visible images (same visibility logic as list_images with no filters)
    count_query = select(func.count()).select_from(Images)

    if current_user is not None and current_user.show_all_images == 1:
        pass  # No status filter -- see all images
    elif current_user is not None:
        count_query = count_query.where(
            or_(
                Images.status.in_(PUBLIC_IMAGE_STATUSES),  # type: ignore[attr-defined]
                Images.user_id == current_user.user_id,  # type: ignore[arg-type]
            )
        )
    else:
        count_query = count_query.where(
            Images.status.in_(PUBLIC_IMAGE_STATUSES)  # type: ignore[attr-defined]
        )

    if current_user is not None and current_user.hide_reposts == 1:
        count_query = count_query.where(Images.status != ImageStatus.REPOST)  # type: ignore[arg-type]

    result = await db.execute(count_query)
    total = result.scalar() or 0

    if total == 0:
        raise HTTPException(status_code=404, detail="No images found")

    total_pages = math.ceil(total / per_page)
    page = random.randint(1, total_pages)

    return RedirectResponse(
        url=f"/?page={page}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/{image_id}", response_model=ImageDetailedResponse)
async def get_image(
    image_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Users | None = Depends(get_optional_current_user),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
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

    open_report_ids = await _open_report_image_ids(db, [image_id], current_user, redis_client)
    # Moderation reason is owner + mods only (same rule as the status-history reason).
    can_see_reason = (
        current_user is not None
        and current_user.user_id is not None
        and (
            current_user.user_id == image.user_id
            or await has_any_permission(
                db,
                current_user.user_id,
                [Permission.IMAGE_EDIT, Permission.REVIEW_VIEW],
                redis_client,
            )
        )
    )
    return ImageDetailedResponse.from_db_model(
        image,
        is_favorited=is_favorited,
        user_rating=user_rating,
        prev_image_id=prev_image_id,
        next_image_id=next_image_id,
        has_open_report=image_id in open_report_ids,
        can_see_reason=can_see_reason,
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

    # Capture R2 metadata before DB delete
    prior_r2_location = image.r2_location
    prior_filename = image.filename
    prior_ext = image.ext
    prior_variants = ["fullsize", "thumbs"]
    if image.medium == VariantStatus.READY:
        prior_variants.append("medium")
    if image.large == VariantStatus.READY:
        prior_variants.append("large")

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

    if settings.R2_ENABLED and prior_r2_location != R2Location.NONE:
        await enqueue_job(
            "r2_delete_image_job",
            image_id=image_id,
            r2_location=int(prior_r2_location),
            filename=prior_filename,
            ext=prior_ext,
            variants=prior_variants,
        )


@router.patch("/{image_id}", response_model=ImageDetailedResponse)
async def update_image(
    image_id: Annotated[int, Path(description="Image ID")],
    image_data: ImageUpdate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> ImageDetailedResponse:
    """
    Update image metadata (caption, miscmeta) and/or owner status.

    Metadata can be updated by:
    - The image owner
    - Admin users
    - Users with IMAGE_EDIT_META permission

    Status can be set to SPOILER (2) or REPOST (-1) by:
    - The image owner (only when image is ACTIVE and not locked)

    Repost requires replacement_id (the original image ID).
    """
    from app.services.repost import migrate_repost_data

    update_fields = image_data.model_dump(exclude_unset=True)
    new_status = update_fields.pop("status", None)
    replacement_id = update_fields.pop("replacement_id", None)

    # replacement_id is only valid with status=REPOST
    if replacement_id is not None and new_status != ImageStatus.REPOST:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="replacement_id is only valid when marking as repost",
        )

    if not update_fields and new_status is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    # Fetch image
    result = await db.execute(
        select(Images).where(Images.image_id == image_id)  # type: ignore[arg-type]
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    # Check ownership, admin, or permission
    is_owner = image.user_id == current_user.id
    is_admin = current_user.admin
    has_edit_permission = False
    if not is_owner and not is_admin:
        has_edit_permission = await has_permission(
            db, current_user.id, Permission.IMAGE_EDIT_META, redis_client
        )

    # Metadata fields require owner, admin, or IMAGE_EDIT_META
    if update_fields and not is_owner and not is_admin and not has_edit_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to edit this image",
        )

    # Status changes require ownership
    if new_status is not None:
        if not is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to change this image's status",
            )

        # Owners can only set SPOILER or REPOST
        allowed_statuses = {ImageStatus.SPOILER, ImageStatus.REPOST}
        if new_status not in allowed_statuses:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Owners can only mark images as spoiler or repost",
            )

        # Image must be ACTIVE
        if image.status != ImageStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only active images can have their status changed",
            )

        # Image must not be locked
        if image.locked:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Locked images cannot have their status changed",
            )

        # Handle repost validation
        if new_status == ImageStatus.REPOST:
            if replacement_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="replacement_id is required when marking as repost",
                )
            if replacement_id == image_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="An image cannot be a repost of itself",
                )
            original_result = await db.execute(
                select(Images).where(Images.image_id == replacement_id)
            )
            if not original_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Original image not found",
                )
            image.replacement_id = replacement_id
            await migrate_repost_data(image_id, replacement_id, db)
        else:
            # Clear replacement_id when not a repost
            image.replacement_id = None

        previous_status = image.status
        image.status = new_status
        image.status_user_id = current_user.id
        image.status_updated = datetime.now(UTC)

        # Log to status history
        history = ImageStatusHistory(
            image_id=image_id,
            old_status=previous_status,
            new_status=new_status,
            user_id=current_user.id,
        )
        db.add(history)

    # Apply metadata updates
    for field, value in update_fields.items():
        setattr(image, field, value)

    await db.commit()

    if new_status is not None:
        await enqueue_r2_sync_on_status_change(
            image_id=image_id,
            old_status=previous_status,
            new_status=new_status,
        )

    # Recalculate ratings for the original image after repost migration
    if new_status == ImageStatus.REPOST and replacement_id:
        await recalculate_image_ratings(db, replacement_id)
        await db.commit()

    # Re-fetch with relationships for response
    result = await db.execute(
        select(Images)
        .options(
            selectinload(Images.user)  # type: ignore[arg-type]
            .selectinload(Users.user_groups)  # type: ignore[arg-type]
            .selectinload(UserGroups.group),  # type: ignore[arg-type]
            selectinload(Images.tag_links).selectinload(TagLinks.tag),  # type: ignore[arg-type]
        )
        .where(Images.image_id == image_id)  # type: ignore[arg-type]
    )
    image = result.scalar_one()

    return ImageDetailedResponse.from_db_model(image)


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


# Safety ceiling on tag_history rows loaded for a single image's history. The merge
# pages in memory, and per-image churn is tiny in practice (well under this), so this
# only guards a pathological image from loading an unbounded change log. If ever hit,
# the oldest history events beyond the cap are dropped (most-recent-first).
_IMAGE_TAG_HISTORY_CAP = 5000


@router.get("/{image_id}/tag-history", response_model=ImageTagHistoryListResponse)
async def get_image_tag_history(
    image_id: Annotated[int, Path(description="Image ID")],
    pagination: Annotated[PaginationParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> ImageTagHistoryListResponse:
    """
    Get tag history for an image.

    Returns a paginated, most-recent-first list of tag add/remove events. "Added"
    events are derived from the image's current tag_links — they carry who/when for
    every tag still on the image, including tags set at upload that were never
    written to tag_history. tag_history supplies removals and the adds of tags that
    are no longer linked, so removed tags keep their full story.
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    if not image_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Image not found")

    group_load = selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]

    def to_summary(user: Users | None) -> UserSummary | None:
        if user is None or user.user_id is None:
            return None
        return UserSummary(
            user_id=user.user_id,
            username=user.username,
            avatar=user.avatar,
            avatar_in_r2=user.avatar_in_r2,
            user_title=user.user_title,
            groups=user.groups,
        )

    # Current tag_links → "added" events. Authoritative for every tag still present.
    links_result = await db.execute(
        select(TagLinks, Tags, Users)
        .join(Tags, TagLinks.tag_id == Tags.tag_id)  # type: ignore[arg-type]
        .outerjoin(Users, TagLinks.user_id == Users.user_id)  # type: ignore[arg-type]
        .options(group_load)
        .where(TagLinks.image_id == image_id)  # type: ignore[arg-type]
    )
    link_rows = links_result.all()
    linked_tag_ids = {link.tag_id for link, _, _ in link_rows}

    # tag_history → removals, plus the adds of tags that are no longer linked. Adds
    # of currently-linked tags are skipped: tag_links already represents them.
    # Capped to the most-recent rows as a safety ceiling (see _IMAGE_TAG_HISTORY_CAP).
    hist_result = await db.execute(
        select(TagHistory, Tags, Users)
        .join(Tags, TagHistory.tag_id == Tags.tag_id)  # type: ignore[arg-type]
        .outerjoin(Users, TagHistory.user_id == Users.user_id)  # type: ignore[arg-type]
        .options(group_load)
        .where(TagHistory.image_id == image_id)  # type: ignore[arg-type]
        .order_by(
            desc(TagHistory.date),  # type: ignore[arg-type]
            desc(TagHistory.tag_history_id),  # type: ignore[arg-type]
        )
        .limit(_IMAGE_TAG_HISTORY_CAP)
    )
    hist_rows = hist_result.all()

    # Merge in memory and sort by date desc, then a deterministic tiebreak. The
    # row count loaded here is bounded by the image's own tag activity (its current
    # tag_links plus its tag_history rows), which is small in practice — the same
    # load-all-then-sort approach the user-history endpoint uses. The tiebreak is
    # (source, id): tag_id for link events vs tag_history_id for history events live
    # in separate ordinal lanes (0/1) so identical timestamps still order the same
    # way across requests (the two id spaces are unrelated and could collide).
    # Both date columns default to current_timestamp; the aware sentinel keeps the
    # sort total if one is ever null.
    epoch = datetime(1, 1, 1, tzinfo=UTC)
    events: list[tuple[datetime, int, int, ImageTagHistoryResponse]] = []

    for link, tag, user in link_rows:
        events.append(
            (
                link.date_linked or epoch,
                0,  # source lane: link events
                link.tag_id or 0,
                ImageTagHistoryResponse(
                    tag_history_id=None,
                    image_id=image_id,
                    tag_id=link.tag_id,
                    action="added",
                    user=to_summary(user),
                    date=link.date_linked,
                    tag=LinkedTag(tag_id=tag.tag_id, title=tag.title, type=tag.type),
                ),
            )
        )

    for history, tag, user in hist_rows:
        # Skip add-history for a tag that's currently linked — the tag_link above
        # already represents it. Edge case: for an add → remove → re-add tag (now
        # linked again), this drops the ORIGINAL add too, so the timeline shows the
        # current link's add + the removal but not the first add. Accepted as rare;
        # do not "fix" by removing this skip without also de-duping vs tag_links, or
        # currently-linked tags double-count.
        if history.action == "a" and history.tag_id in linked_tag_ids:
            continue
        events.append(
            (
                history.date or epoch,
                1,  # source lane: history events
                history.tag_history_id or 0,
                ImageTagHistoryResponse(
                    tag_history_id=history.tag_history_id,
                    image_id=history.image_id,
                    tag_id=history.tag_id,
                    action="added" if history.action == "a" else "removed",
                    user=to_summary(user),
                    date=history.date,
                    tag=LinkedTag(tag_id=tag.tag_id, title=tag.title, type=tag.type),
                ),
            )
        )

    events.sort(key=lambda e: (e[0], e[1], e[2]), reverse=True)

    total = len(events)
    start = pagination.offset
    items = [event[3] for event in events[start : start + pagination.per_page]]

    return ImageTagHistoryListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        items=items,
    )


@router.get("/{image_id}/status-history", response_model=ImageStatusHistoryListResponse)
async def get_image_status_history(
    image_id: Annotated[int, Path(description="Image ID")],
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[Users | None, Depends(get_optional_current_user)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> ImageStatusHistoryListResponse:
    """
    Get status history for an image.

    Returns paginated list of status changes.
    User info is shown only for public status changes (repost, spoiler, active).
    The free-text reason is shown only for public-destination transitions, or to
    the image owner / moderators; the reason_category is always shown.
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    owner_id = image.user_id

    is_privileged_viewer = False
    can_see_report_links = False
    if current_user is not None and current_user.user_id is not None:
        is_privileged_viewer = current_user.user_id == owner_id or await has_any_permission(
            db, current_user.user_id, [Permission.IMAGE_EDIT, Permission.REVIEW_VIEW], redis_client
        )
        # The originating report/review link is moderation context — REPORT_VIEW only
        # (independent of the reason gate, which follows the owner/visible-status rule).
        can_see_report_links = await has_any_permission(
            db, current_user.user_id, [Permission.REPORT_VIEW], redis_client
        )

    # Query status history with user info
    # Eager load user groups for UserSummary
    query = (
        select(ImageStatusHistory, Users)
        .outerjoin(Users, ImageStatusHistory.user_id == Users.user_id)  # type: ignore[arg-type]
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(ImageStatusHistory.image_id == image_id)  # type: ignore[arg-type]
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate and order by most recent first
    # Secondary sort by id for stable ordering when timestamps match
    query = (
        query.order_by(
            desc(ImageStatusHistory.created_at),  # type: ignore[arg-type]
            desc(ImageStatusHistory.id),  # type: ignore[arg-type]
        )
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )

    result = await db.execute(query)
    rows = result.all()

    items = []
    for history, user in rows:
        # Determine if user should be shown based on old/new status
        # Show user if EITHER old_status OR new_status is a visible status
        show_user = (
            history.old_status in ImageStatus.VISIBLE_USER_STATUSES
            or history.new_status in ImageStatus.VISIBLE_USER_STATUSES
        )

        user_summary = None
        if show_user and user:
            user_summary = UserSummary(
                user_id=user.user_id,
                username=user.username,
                avatar=user.avatar,
                avatar_in_r2=user.avatar_in_r2,
                user_title=user.user_title,
                groups=user.groups if user else [],
            )

        # The free-text reason is public only when BOTH endpoints are publicly
        # visible. Any transition touching a hidden state (deactivated/review) —
        # including un-hiding it — carries moderation rationale and stays
        # owner/mods-only. reason_category is always exposed.
        reason_is_public = (
            history.old_status in ImageStatus.VISIBLE_USER_STATUSES
            and history.new_status in ImageStatus.VISIBLE_USER_STATUSES
        )
        can_see_reason = reason_is_public or is_privileged_viewer

        items.append(
            ImageStatusHistoryResponse(
                id=history.id,
                image_id=history.image_id,
                old_status=history.old_status,
                old_status_label=ImageStatus.get_label(history.old_status),
                new_status=history.new_status,
                new_status_label=ImageStatus.get_label(history.new_status),
                reason_category=history.reason_category,
                reason=history.reason if can_see_reason else None,
                report_id=history.report_id if can_see_report_links else None,
                review_id=history.review_id if can_see_report_links else None,
                user=user_summary,
                created_at=history.created_at,
            )
        )

    return ImageStatusHistoryListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        items=items,
    )


def get_review_outcome_label(outcome: int) -> str:
    """Get human-readable label for review outcome."""
    outcome_labels = {
        ReviewOutcome.PENDING: "pending",
        ReviewOutcome.KEEP: "keep",
        ReviewOutcome.REMOVE: "remove",
    }
    return outcome_labels.get(outcome, "unknown")


@router.get("/{image_id}/reviews", response_model=ImageReviewListResponse)
async def get_image_reviews(
    image_id: Annotated[int, Path(description="Image ID")],
    pagination: Annotated[PaginationParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> ImageReviewListResponse:
    """
    Get closed review sessions for an image.

    Returns paginated list of completed review sessions.
    Only closed reviews are returned (open/in-progress reviews are excluded).
    Internal fields (initiated_by, votes) are hidden for privacy.
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    if not image_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Image not found")

    # Query only closed reviews
    query = select(ImageReviews).where(
        ImageReviews.image_id == image_id,  # type: ignore[arg-type]
        ImageReviews.status == ReviewStatus.CLOSED,  # type: ignore[arg-type]
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate and order by most recent first
    # Secondary sort by review_id for stable ordering when timestamps match
    query = (
        query.order_by(
            desc(ImageReviews.created_at),  # type: ignore[arg-type]
            desc(ImageReviews.review_id),  # type: ignore[arg-type]
        )
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )

    result = await db.execute(query)
    reviews = result.scalars().all()

    items = [
        ImageReviewPublicResponse(
            review_id=review.review_id or 0,
            reason_category=review.reason_category,
            reason_category_label=DeactivationReason.get_label(review.reason_category),
            outcome=review.outcome,
            outcome_label=get_review_outcome_label(review.outcome),
            created_at=review.created_at,  # type: ignore[arg-type]  # server_default ensures non-null
            closed_at=review.closed_at,
        )
        for review in reviews
    ]

    return ImageReviewListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        items=items,
    )


@router.get("/{image_id}/reports", response_model=ReportListResponse)
async def get_image_reports(
    image_id: Annotated[int, Path(description="Image ID")],
    pagination: Annotated[PaginationParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.REPORT_VIEW))],
    db: AsyncSession = Depends(get_db),
) -> ReportListResponse:
    """
    Get the reports filed against an image (pending + resolved), newest first.

    Mod-only (REPORT_VIEW) — powers the moderation activity timeline. Returns the
    reporter, category, reason, and status for each report. Resolution/tag-suggestion
    detail is intentionally omitted (the timeline shows the resulting status-change
    event alongside).
    """
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    if not image_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Image not found")

    base_query = select(ImageReports).where(ImageReports.image_id == image_id)  # type: ignore[arg-type]
    total = (
        await db.execute(select(func.count()).select_from(base_query.subquery()))
    ).scalar() or 0

    query = (
        select(ImageReports, Users)
        .outerjoin(Users, ImageReports.user_id == Users.user_id)  # type: ignore[arg-type]
        .options(selectinload(Users.user_groups).selectinload(UserGroups.group))  # type: ignore[arg-type]
        .where(ImageReports.image_id == image_id)  # type: ignore[arg-type]
        .order_by(
            desc(ImageReports.created_at),  # type: ignore[arg-type]
            desc(ImageReports.report_id),  # type: ignore[arg-type]
        )
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )

    items = []
    for report, user in (await db.execute(query)).all():
        reporter = None
        if user:
            reporter = UserSummary(
                user_id=user.user_id,
                username=user.username,
                avatar=user.avatar,
                avatar_in_r2=user.avatar_in_r2,
                user_title=user.user_title,
                groups=user.groups,
            )
        items.append(
            ReportResponse(
                report_id=report.report_id,
                image_id=report.image_id,
                user=reporter,
                category=report.category,
                reason_text=report.reason_text,
                status=report.status,
                created_at=report.created_at,
                reviewed_at=report.reviewed_at,
            )
        )

    return ReportListResponse(
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
        .options(
            selectinload(Images.user).load_only(  # type: ignore[arg-type]
                Users.user_id,  # type: ignore[arg-type]
                Users.username,  # type: ignore[arg-type]
                Users.avatar,  # type: ignore[arg-type]
                Users.avatar_in_r2,  # type: ignore[arg-type]
                Users.user_title,  # type: ignore[arg-type]
            )
        )
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

    # Get users who favorited the image; eager-load groups so UserResponse.groups is populated
    query = (
        select(Users)
        .join(Favorites)
        .where(Favorites.image_id == image_id)  # type: ignore[arg-type]
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group),  # type: ignore[arg-type]
        )
    )

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


@router.get("/{image_id}/ratings", response_model=ImageRatingsListResponse)
async def get_image_ratings_users(
    image_id: int,
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[ImageRatingsSortParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> ImageRatingsListResponse:
    """
    Get all users who have rated a specific image, along with their rating values.
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    if not image_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Image not found")

    # Count is independent of the user join — hits the fk_image_ratings_image_id index directly
    count_query = select(func.count()).where(ImageRatings.image_id == image_id)  # type: ignore[arg-type]
    total = (await db.execute(count_query)).scalar() or 0

    # Join Users with their ImageRatings row for this image; eager-load groups so
    # UserResponse.groups is populated for each rater.
    query = (
        select(
            Users,
            ImageRatings.rating.label("rating_value"),  # type: ignore[attr-defined]
            ImageRatings.date.label("rated_at"),  # type: ignore[union-attr]
        )
        .join(ImageRatings, ImageRatings.user_id == Users.user_id)  # type: ignore[arg-type]
        .where(ImageRatings.image_id == image_id)  # type: ignore[arg-type]
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group),  # type: ignore[arg-type]
        )
    )

    # Resolve sort column from the requested field (some live on Users, some on ImageRatings)
    sort_columns: dict[str, Any] = {
        "rating": ImageRatings.rating,
        "date": ImageRatings.date,
        "user_id": Users.user_id,
        "username": Users.username,
        "date_joined": Users.date_joined,
    }
    sort_column = sort_columns[sorting.sort_by]
    primary = desc(sort_column) if sorting.sort_order == "DESC" else asc(sort_column)
    # Tiebreaker on user_id keeps order stable when sorting by a non-unique column.
    # Skip it when the primary sort is already user_id (which is unique).
    tiebreaker: list[Any] = (
        [] if sorting.sort_by == "user_id" else [asc(Users.user_id)]  # type: ignore[arg-type]
    )
    query = (
        query.order_by(primary, *tiebreaker).offset(pagination.offset).limit(pagination.per_page)
    )

    result = await db.execute(query)
    rows = result.all()

    users_with_ratings = [
        UserWithRatingResponse(
            **UserResponse.model_validate(user).model_dump(),
            rating=rating,
            rated_at=rated_at,
        )
        for user, rating, rated_at in rows
    ]

    return ImageRatingsListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        users=users_with_ratings,
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
    # Get the image to find its hash (or thumbnail filename for fallback).
    result = await db.execute(
        select(Images).where(Images.image_id == image_id)  # type: ignore[arg-type]
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    if image.iqdb_hash:
        # Hash-based query — no bytes, no file dependency.
        similar_results = await check_iqdb_similarity_by_hash(image.iqdb_hash, threshold=threshold)
    else:
        # Transitional fallback for rows that pre-date the iqdb_hash
        # column. Removed once populate_iqdb.py --only-missing-hash
        # reports zero NULLs and we trust new uploads to populate live.
        thumb_path = FilePath(settings.STORAGE_PATH) / "thumbs" / f"{image.filename}.webp"
        if not thumb_path.exists():
            raise HTTPException(
                status_code=404,
                detail="Image thumbnail not found - cannot perform similarity search",
            )
        similar_results = await check_iqdb_similarity(thumb_path, db, threshold=threshold)

    # Filter out the query image itself (iqdb-rs returns it with high score).
    similar_results = [r for r in similar_results if r["image_id"] != image_id]

    if not similar_results:
        return SimilarImagesResponse(query_image_id=image_id, similar_images=[])

    similar_images = await _hydrate_similar_images(similar_results, db)
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
        .options(
            selectinload(Images.user).load_only(  # type: ignore[arg-type]
                Users.user_id,  # type: ignore[arg-type]
                Users.username,  # type: ignore[arg-type]
                Users.avatar,  # type: ignore[arg-type]
                Users.avatar_in_r2,  # type: ignore[arg-type]
                Users.user_title,  # type: ignore[arg-type]
            )
        )
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
    hide_reposts = current_user.hide_reposts == 1
    images_per_page = current_user.images_per_page or 15

    # Bookmark not visible under the user's settings -> page: null
    is_public = bookmark_image.status in PUBLIC_IMAGE_STATUSES
    is_own_image = bookmark_image.user_id == current_user.user_id
    is_repost = bookmark_image.status == ImageStatus.REPOST
    hidden_by_visibility = (not show_all) and (not is_public) and (not is_own_image)
    hidden_by_repost_pref = hide_reposts and is_repost
    if hidden_by_visibility or hidden_by_repost_pref:
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

    if hide_reposts:
        count_query = count_query.where(Images.status != ImageStatus.REPOST)  # type: ignore[arg-type]

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

    await refresh_image_tag_type_flags(db, image_id)
    await db.commit()

    # Re-fetch tag to get updated usage_count (maintained by DB trigger)
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == resolved_tag_id))  # type: ignore[arg-type]
    updated_tag = tag_result.scalar_one_or_none()
    if updated_tag:
        await sync_tag_to_search(updated_tag, db=db)

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
    await refresh_image_tag_type_flags(db, image_id)
    await db.commit()

    # Re-fetch tag to get updated usage_count (maintained by DB trigger)
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
    updated_tag = tag_result.scalar_one_or_none()
    if updated_tag:
        await sync_tag_to_search(updated_tag, db=db)


@router.post("/{image_id}/rating", status_code=status.HTTP_201_CREATED)
async def rate_image(
    image_id: Annotated[int, Path(description="Image ID")],
    rating: Annotated[int, Query(ge=1, le=10, description="Rating value (1-10)")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> dict[str, str | float | int]:
    """
    Rate an image (1-10 scale).

    Users can rate any image once. If they rate again, their previous rating is updated.
    Returns the recalculated rating statistics for immediate UI update.

    Args:
        image_id: The image to rate
        rating: Rating value from 1 to 10

    Returns:
        Message and updated rating stats (average_rating, bayesian_rating, num_ratings)
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

    # Flush so the recalculation query sees the new/updated rating
    await db.flush()

    # Recalculate and persist updated stats (global stats cached in Redis)
    stats = await recalculate_image_ratings(db, image_id, redis_client)
    await db.commit()

    return {
        "message": message,
        "average_rating": stats.average_rating,
        "bayesian_rating": stats.bayesian_rating,
        "num_ratings": stats.num_ratings,
    }


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


@router.post(
    "/upload",
    response_model=ImageUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {
            "model": ImageUploadSimilarResponse | ImageUploadDuplicateResponse,
            "description": (
                "Near-duplicate images found (confirm to proceed), "
                "or an exact duplicate already exists on the board"
            ),
        }
    },
)
async def upload_image(
    request: Request,
    current_user: VerifiedUser,
    file: Annotated[UploadFile, File(description="Image file to upload")],
    caption: Annotated[str, Form(max_length=35)] = "",
    miscmeta: Annotated[str | None, Form(max_length=255)] = None,
    tag_ids: Annotated[str, Form(description="Comma-separated tag IDs (e.g., '1,2,3')")] = "",
    confirm_similar: Annotated[
        bool, Form(description="Set to true to bypass IQDB similarity check")
    ] = False,
    db: AsyncSession = Depends(get_db),
) -> ImageUploadResponse | JSONResponse:
    """
    Upload a new image with metadata and tags.

    Requires authentication.

    Process:
    1. Create temporary image record to get image_id
    2. Validate image file (type, size, extension) using PIL
    3. Check for duplicate (MD5 hash)
    4. Save image to storage (filename: YYYY-MM-DD-{image_id}.{ext})
    5. Extract image dimensions
    6. Check IQDB for near-duplicate images (unless confirm_similar=true)
       - If matches found (>= IQDB_UPLOAD_THRESHOLD), return 409 with similar images
       - Frontend displays matches for user confirmation, then retries with confirm_similar=true
    7. Update image record with metadata
    8. Link tags to image
    9. Schedule thumbnail generation (background)
    10. Return created image details

    Security:
    - Requires authentication
    - Validates file is actually an image using PIL (prevents malicious uploads)
    - Validates file type, size, and extension
    - Prevents duplicate images (MD5 check)
    - Detects near-duplicate images via IQDB similarity (409 with confirmation flow)

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

    # Check upload rate limit and daily limit (skip for admins)
    if not current_user.admin:
        await check_upload_rate_limit(current_user.id, db, maximgperday=current_user.maximgperday)

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
            # Capture the id before rollback expires the instance.
            existing_image_id: int = existing_image.image_id  # type: ignore[assignment]
            logger.warning(
                "duplicate_image_detected",
                image_id=image_id,
                duplicate_of=existing_image_id,
                md5_hash=md5_hash,
            )
            # Clean up the temp record and saved file before returning 409,
            # mirroring the IQDB near-duplicate path below.
            await db.rollback()
            if file_path:
                file_path.unlink(missing_ok=True)
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=ImageUploadDuplicateResponse(
                    detail=f"Image already exists with ID {existing_image_id}",
                    existing_image_id=existing_image_id,
                ).model_dump(mode="json"),
            )

        # Get image dimensions and update record
        width, height = get_image_dimensions(file_path)
        filesize = file_path.stat().st_size

        # Calculate total pixels (in megapixels)
        total_pixels = Decimal((width * height) / 1_000_000)

        # Derive from the actually-saved fullsize path so DB.filename matches
        # the on-disk name even when upload straddles a local-midnight boundary
        # (datetime.now() recomputed here can land on the next day).
        filename = file_path.stem  # e.g. "2026-05-18-1116164"

        # Check IQDB for near-duplicate images unless user confirmed
        if not confirm_similar:
            iqdb_results = await check_iqdb_similarity(
                file_path, db, threshold=settings.IQDB_UPLOAD_THRESHOLD
            )
            if iqdb_results:
                # Hydrate IQDB results with full image data
                similar = await _hydrate_similar_images(iqdb_results, db)
                # Clean up temp record and file before returning 409
                await db.rollback()
                if file_path and file_path.exists():
                    file_path.unlink()
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content=ImageUploadSimilarResponse(
                        message="Similar images found. Please confirm to proceed with upload.",
                        similar_images=similar,
                    ).model_dump(mode="json"),
                )

        # Determine variant generation status for medium/large
        medium_status = (
            VariantStatus.PENDING
            if (width > settings.MEDIUM_EDGE or height > settings.MEDIUM_EDGE)
            else VariantStatus.NONE
        )
        large_status = (
            VariantStatus.PENDING
            if (width > settings.LARGE_EDGE or height > settings.LARGE_EDGE)
            else VariantStatus.NONE
        )

        # Update temporary record with actual data
        temp_image.filename = filename
        temp_image.ext = ext
        temp_image.md5_hash = md5_hash
        temp_image.filesize = filesize
        temp_image.width = width
        temp_image.height = height
        temp_image.total_pixels = total_pixels
        temp_image.medium = medium_status
        temp_image.large = large_status
        temp_image.caption = caption
        temp_image.miscmeta = miscmeta

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
        if medium_status:
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
        if large_status:
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
        thumb_path = FilePath(settings.STORAGE_PATH) / "thumbs" / f"{filename}.webp"
        await enqueue_job(
            "add_to_iqdb_job",
            image_id=image_id,
            thumb_path=str(thumb_path),
            _defer_by=5.0,  # Wait 5 seconds for thumbnail to complete
        )

        if settings.R2_ENABLED:
            await enqueue_job(
                "r2_finalize_upload_job",
                image_id=image_id,
                _defer_by=90,
            )

        if settings.ML_TAG_SUGGESTIONS_ENABLED:
            # Run as soon as the worker is free — inference reads the fullsize,
            # which is already saved above, so no defer is needed. Failure must
            # never fail the upload itself.
            try:
                await enqueue_job(
                    "generate_ml_tag_suggestions",
                    image_id=image_id,
                )
                logger.debug("ml_tag_suggestion_job_enqueued", image_id=image_id)
            except Exception as e:
                logger.error(
                    "ml_tag_suggestion_enqueue_failed",
                    image_id=image_id,
                    error=str(e),
                    error_type=type(e).__name__,
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
            miscmeta=temp_image.miscmeta,
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
            detail="Failed to upload image",
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

    # Build response - fetch user with groups for UserSummary
    user_result = await db.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Users.user_id == current_user.id)  # type: ignore[arg-type]
    )
    reporter = user_result.scalar_one()
    reporter_summary = UserSummary(
        user_id=reporter.id,
        username=reporter.username,
        avatar=reporter.avatar,
        avatar_in_r2=reporter.avatar_in_r2,
        user_title=reporter.user_title,
        groups=reporter.groups,
    )
    response = ReportResponse.model_validate(new_report)
    response.user = reporter_summary

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
