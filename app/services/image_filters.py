"""The image listing's filters, shared so every view of a listing builds the same query.

``list_images`` (GET /images) takes `ImageFilterParams` as a dependency and builds its
filtered query with `build_image_filter_query`. Any endpoint that must agree with the
listing on which images match takes the same pair.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import redis.asyncio as redis
from fastapi import HTTPException, Query, status
from sqlalchemy import ColumnElement, Select, and_, bindparam, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, ReportStatus, settings
from app.core.permissions import Permission, has_any_permission
from app.models import Comments, Favorites, ImageReports, Images, TagLinks, Users
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES
from app.services.tag_hierarchy import get_tag_hierarchy, resolve_tag_alias
from app.utils.comment_search import apply_comment_text_search, reject_unindexable_comment_search

# Bind parameters a seekable query bounds its link-table subqueries with (_seek_bounds).
SEEK_LOWER_PARAM = "seek_lower_image_id"
SEEK_UPPER_PARAM = "seek_upper_image_id"
# image_id is a Postgres integer (int4) on images and on every link table, so the int4
# limits leave a bound always true, and fit the int4 parameter asyncpg infers for it.
SEEK_UNBOUNDED_LOWER = -(2**31)
SEEK_UNBOUNDED_UPPER = 2**31 - 1


@dataclass
class ImageFilterParams:
    """GET /images' filter query parameters, as a FastAPI dependency.

    Use as ``Annotated[ImageFilterParams, Depends()]``: each field is one query
    parameter. Pagination, sorting and ``include_comments`` are not filters and stay on
    the endpoint. A new filter also needs a ``_FeedFilters`` field in
    app/api/v1/images.py, or the bare-feed fast count ignores it.
    """

    # Basic filters
    user_id: Annotated[int | None, Query(description="Filter by uploader user ID")] = None
    favorited_by_user_id: Annotated[
        int | None, Query(description="Filter by user who favorited the image")
    ] = None
    exclude_user_id: Annotated[
        str | None,
        Query(description="Comma-separated user IDs whose uploads to exclude (e.g., '5,6')"),
    ] = None
    exclude_favorited_by_user_id: Annotated[
        str | None,
        Query(description="Comma-separated user IDs; exclude images any of them favorited"),
    ] = None
    image_status: Annotated[
        list[int] | None,
        Query(
            description="Filter by status (1=active, 2=spoiler, etc). Repeat for multiple.",
            alias="status",
        ),
    ] = None
    # Tag filtering
    tags: Annotated[str | None, Query(description="Comma-separated tag IDs (e.g., '1,2,3')")] = None
    tags_mode: Annotated[str, Query(pattern="^(any|all)$", description="Match ANY or ALL tags")] = (
        "any"
    )
    tag_depth: Annotated[
        int | None,
        Query(
            ge=0,
            le=9,
            description="How many levels of child tags to include. "
            "0=exact tag only, 1=tag+direct children, ..., 9=up to 9 levels. "
            "Omit for full hierarchy.",
        ),
    ] = None
    exclude_tags: Annotated[
        str | None, Query(description="Comma-separated tag IDs to exclude (e.g., '4,5,6')")
    ] = None
    exclude_descendants: Annotated[
        bool,
        Query(
            description="When true, each excluded tag also excludes its child tags "
            "(its full hierarchy). Default false (exact-match exclusion)."
        ),
    ] = False
    missing_tag_types: Annotated[
        str | None,
        Query(
            description="Comma-separated tag type IDs the image must be MISSING "
            "(1=Theme, 2=Source, 3=Artist, 4=Character)."
        ),
    ] = None
    missing_tag_types_mode: Annotated[
        str, Query(pattern="^(any|all)$", description="Match ANY or ALL missing types")
    ] = "any"
    # Date filtering
    date_from: Annotated[str | None, Query(description="Start date (YYYY-MM-DD)")] = None
    date_to: Annotated[str | None, Query(description="End date (YYYY-MM-DD)")] = None
    # Size filtering
    min_width: Annotated[int | None, Query(ge=1, description="Minimum width in pixels")] = None
    max_width: Annotated[int | None, Query(ge=1, description="Maximum width in pixels")] = None
    min_height: Annotated[int | None, Query(ge=1, description="Minimum height in pixels")] = None
    max_height: Annotated[int | None, Query(ge=1, description="Maximum height in pixels")] = None
    # Rating filtering
    min_rating: Annotated[float | None, Query(ge=1, le=10, description="Minimum rating (1-10)")] = (
        None
    )
    min_favorites: Annotated[int | None, Query(ge=0, description="Minimum favorite count")] = None
    min_num_ratings: Annotated[int | None, Query(ge=0, description="Minimum number of ratings")] = (
        None
    )
    # Comment filtering
    commenter: Annotated[
        int | None, Query(description="Filter by user who commented on the image")
    ] = None
    exclude_commenter: Annotated[
        str | None,
        Query(description="Comma-separated user IDs; exclude images any of them commented on"),
    ] = None
    commentsearch: Annotated[str | None, Query(description="Substring search in comment text")] = (
        None
    )
    commentsearch_mode: Annotated[
        str | None,
        Query(
            pattern="^(all_words|natural|boolean|like)$",
            description=(
                "Search mode: all_words (default, every term required), "
                "like (whole string as one substring); natural and boolean "
                "are accepted and behave as all_words"
            ),
        ),
    ] = None
    hascomments: Annotated[
        bool | None,
        Query(description="Filter to images that have comments (true) or no comments (false)"),
    ] = None
    reported: Annotated[
        bool | None,
        Query(
            description="Filter to images with a pending report. Requires report_view; "
            "ignored for other viewers."
        ),
    ] = None

    def __post_init__(self) -> None:
        # A blank/whitespace-only commentsearch means "not searching," not "search for
        # nothing" -- normalize it to None up front so it reads as absent everywhere
        # after: the comment-filter EXISTS guard in build_image_filter_query and the
        # _FeedFilters entry in list_images both key off `commentsearch is not None`.
        if self.commentsearch is not None and not self.commentsearch.strip():
            self.commentsearch = None
        # Rejected here, while FastAPI resolves dependencies, so the 400 precedes
        # list_images' anonymous response-cache lookup.
        if self.commentsearch is not None:
            reject_unindexable_comment_search(self.commentsearch, self.commentsearch_mode)


@dataclass
class ImageFilterQuery:
    """`build_image_filter_query`'s result.

    ``decompose_count`` marks the default logged-in feed, whose count splits into a
    shared public term and a per-viewer own-hidden term. ``apply_reported`` is whether
    the ``reported`` filter ran (REPORT_VIEW viewers only).
    """

    content_query: Select[Any]
    visibility_clauses: list[ColumnElement[bool]]
    decompose_count: bool
    apply_reported: bool

    @property
    def query(self) -> Select[Any]:
        """`content_query` with every visibility clause applied."""
        query = self.content_query
        for clause in self.visibility_clauses:
            query = query.where(clause)
        return query


def _parse_user_id_list(raw: str | None, param: str) -> list[int]:
    """Parse a comma-separated user-id list param (``exclude_user_id`` etc.).

    isdecimal() (not isdigit()): int() rejects chars like '²' that isdigit() matches.
    Capped at MAX_SEARCH_USERS to bound the NOT IN list, mirroring the tags cap.
    """
    if not raw:
        return []
    ids = [int(tok.strip()) for tok in raw.split(",") if tok.strip().isdecimal()]
    if len(ids) > settings.MAX_SEARCH_USERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You can only exclude up to {settings.MAX_SEARCH_USERS} users at a time"
            f" ({param}).",
        )
    return ids


def _linked_to(link_model: type[Any], *predicates: ColumnElement[bool]) -> ColumnElement[bool]:
    """EXISTS semi-join: images with at least one `link_model` row matching `predicates`.

    ``correlate(Images)`` pins correlation to just the outer ``Images`` row: some
    callers also JOIN `link_model` for an include-side filter in the same query
    (e.g. ``favorited_by_user_id`` + ``exclude_favorited_by_user_id``), and without
    this, SQLAlchemy's auto-correlation sees `link_model` in both places and drops
    it from this subquery's FROM entirely, producing an empty, table-less EXISTS.
    """
    return (
        select(link_model.image_id)
        .where(link_model.image_id == Images.image_id, *predicates)
        .correlate(Images)
        .exists()
    )


def _not_linked_to(link_model: type[Any], *predicates: ColumnElement[bool]) -> ColumnElement[bool]:
    """NOT EXISTS anti-join: images with no `link_model` row matching `predicates`.

    Postgres can only hash a NOT IN (subquery) when it fits in work_mem (4MB); a
    popular tag's tag_links rows (717k for one tag) overflow that and the planner
    falls back to a per-row Materialize scan that never finishes (#394). NOT EXISTS
    always plans as a hash anti-join regardless of subquery size.

    See `_linked_to` for why the EXISTS is correlated explicitly.
    """
    return ~_linked_to(link_model, *predicates)


def _seek_bounds(link_image_id: Any, seekable: bool) -> list[ColumnElement[bool]]:
    """The seek position's bounds on a link table's image_id; none for the listing.

    A neighbours seek restricts images.image_id to a range, but Postgres carries only
    equalities across ``images.image_id = link.image_id``, so the semi-join would read
    the link table from the far end of its index. Repeating the range on the link
    column lets that read start at the seek position.

    This is correct only while every statement sets both parameters to exactly its own
    range on images.image_id, as find_neighbors does: a joined or EXISTS link row
    carries the outer row's image_id, so the repeated range drops nothing the outer
    bound keeps. A narrower range would drop matches, and let a NOT EXISTS exclusion
    pass rows it should exclude.
    """
    if not seekable:
        return []
    return [
        link_image_id >= bindparam(SEEK_LOWER_PARAM),
        link_image_id <= bindparam(SEEK_UPPER_PARAM),
    ]


async def build_image_filter_query(  # noqa: C901, PLR0912, PLR0915
    db: AsyncSession,
    filters: ImageFilterParams,
    current_user: Users | None,
    redis_client: redis.Redis | None,  # type: ignore[type-arg]
    *,
    seekable: bool = False,
) -> ImageFilterQuery:
    """Build the listing's filtered query: content filters plus the viewer's visibility.

    With ``seekable=True`` every link-table subquery is bounded by SEEK_LOWER_PARAM and
    SEEK_UPPER_PARAM (see `_seek_bounds`), which each statement run from the query must
    set. The listing leaves it False and gets exactly the query it always had.

    Raises HTTPException(400) for an invalid tag list, user list, tag type or date.
    """
    # Build base query
    query = select(Images)

    # Apply basic filters
    if filters.user_id is not None:
        query = query.where(Images.user_id == filters.user_id)  # type: ignore[arg-type]
    exclude_user_ids = _parse_user_id_list(filters.exclude_user_id, "exclude_user_id")
    if exclude_user_ids:
        query = query.where(Images.user_id.notin_(exclude_user_ids))  # type: ignore[attr-defined]
    if filters.favorited_by_user_id is not None:
        # Join with Favorites table to filter by user who favorited
        query = query.join(Favorites).where(
            Favorites.user_id == filters.favorited_by_user_id,  # type: ignore[arg-type]
            *_seek_bounds(Favorites.image_id, seekable),
        )
    exclude_favorited_ids = _parse_user_id_list(
        filters.exclude_favorited_by_user_id, "exclude_favorited_by_user_id"
    )
    if exclude_favorited_ids:
        query = query.where(
            _not_linked_to(
                Favorites,
                Favorites.user_id.in_(exclude_favorited_ids),  # type: ignore[attr-defined]
                *_seek_bounds(Favorites.image_id, seekable),
            )
        )
    # Status filtering: explicit param overrides, otherwise use user's show_all_images
    # setting. Visibility clauses are COLLECTED here, not applied: list_images' count
    # path decomposes the default logged-in feed into a shared public term plus a
    # per-viewer own-hidden term, which needs the content filters without the
    # visibility WHERE. ImageFilterQuery.query applies them.
    visibility_clauses = []
    decompose_count = False
    if filters.image_status is not None:
        # Explicit status filter - always honor it (supports single or multiple values)
        query = query.where(Images.status.in_(filters.image_status))  # type: ignore[attr-defined]
        if current_user is None:
            # Anonymous users may never see non-public statuses, even when requested
            # explicitly — otherwise ?status=0/-2/-4 enumerates deactivated /
            # inappropriate / review-queue image metadata. Authenticated users retain
            # full explicit-status access (policy decision 2026-07-12).
            visibility_clauses.append(Images.status.in_(PUBLIC_IMAGE_STATUSES))  # type: ignore[attr-defined]
    else:
        # No explicit filter - apply default based on user's show_all_images setting
        # Anonymous users or users with show_all_images=0 see only public statuses
        show_all = current_user is not None and current_user.show_all_images == 1
        if not show_all:
            if current_user is not None:
                # Logged in: show public statuses OR user's own images (any status)
                visibility_clauses.append(
                    or_(
                        Images.status.in_(PUBLIC_IMAGE_STATUSES),  # type: ignore[attr-defined]
                        Images.user_id == current_user.user_id,  # type: ignore[arg-type]
                    )
                )
                decompose_count = current_user.user_id is not None
            else:
                # Anonymous: only public statuses
                visibility_clauses.append(Images.status.in_(PUBLIC_IMAGE_STATUSES))  # type: ignore[attr-defined]

        # hide_reposts preference — applied in the no-explicit-status branch, outside the
        # show_all sub-block so it covers both show_all=0 and show_all=1. This is a global
        # exclusion (not ownership-aware), so the viewer's own reposts are dropped too.
        # An explicit ?status= takes the other branch and overrides this.
        if current_user is not None and current_user.hide_reposts == 1:
            visibility_clauses.append(Images.status != ImageStatus.REPOST)

    # Tag filtering
    tag_ids: list[int] = []
    if filters.tags:
        # isdecimal() (not isdigit()): int() rejects chars like '²' that isdigit() matches.
        tag_ids = [int(tid.strip()) for tid in filters.tags.split(",") if tid.strip().isdecimal()]
        if len(tag_ids) > settings.MAX_SEARCH_TAGS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You can only search for up to {settings.MAX_SEARCH_TAGS} tags at a time.",
            )
        if tag_ids:
            # Map tag_depth to max_depth for the CTE query
            # tag_depth=0 → max_depth=1 (root only), tag_depth=1 → max_depth=2, etc.
            # None → default (10, full hierarchy)
            hierarchy_max_depth = filters.tag_depth + 1 if filters.tag_depth is not None else 10

            if filters.tags_mode == "all":
                # Images must have ALL specified tags (including their descendants)
                for tag_id in tag_ids:
                    _, resolved_tag_id = await resolve_tag_alias(db, tag_id)
                    # Expand hierarchy to configured depth
                    hierarchy_ids = await get_tag_hierarchy(
                        db, resolved_tag_id, max_depth=hierarchy_max_depth
                    )
                    query = query.where(
                        Images.image_id.in_(  # type: ignore[union-attr]
                            select(TagLinks.image_id).where(  # type: ignore[call-overload]
                                TagLinks.tag_id.in_(hierarchy_ids),  # type: ignore[attr-defined]
                                *_seek_bounds(TagLinks.image_id, seekable),
                            )
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
                        select(TagLinks.image_id).where(  # type: ignore[call-overload]
                            TagLinks.tag_id.in_(all_hierarchy_ids),  # type: ignore[attr-defined]
                            *_seek_bounds(TagLinks.image_id, seekable),
                        )
                    )
                )

    # Exclude tag filtering. Exact match by default; with exclude_descendants the
    # excluded tag's whole subtree is removed too (mirrors the include-side hierarchy).
    if filters.exclude_tags:
        # isdecimal() (not isdigit()): int() rejects chars like '²' that isdigit() matches.
        exclude_tag_ids = [
            int(tid.strip()) for tid in filters.exclude_tags.split(",") if tid.strip().isdecimal()
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
                if filters.exclude_descendants:
                    resolved_exclude_ids.update(await get_tag_hierarchy(db, resolved_etid))
                else:
                    resolved_exclude_ids.add(resolved_etid)

            # NOT EXISTS anti-join, not NOT IN (subquery): Postgres can only hash a
            # NOT IN subquery when it fits in work_mem, and a popular tag's tag_links
            # rows overflow that and never finish (#394). NOT EXISTS always plans as
            # a hash anti-join.
            query = query.where(
                _not_linked_to(
                    TagLinks,
                    TagLinks.tag_id.in_(resolved_exclude_ids),  # type: ignore[attr-defined]
                    *_seek_bounds(TagLinks.image_id, seekable),
                )
            )

    # Missing tag-type filtering (images lacking a tag of the given type[s]).
    # Aliases are intentionally NOT resolved here: the applied tag's own `type` is authoritative.
    if filters.missing_tag_types:
        # Reject any token that is not a valid type ID (non-digit or out of range), rather
        # than silently dropping it. Empty tokens are ignored (empty param = no filter).
        raw_tokens = [t.strip() for t in filters.missing_tag_types.split(",") if t.strip()]
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
            if filters.missing_tag_types_mode == "all":
                query = query.where(and_(*clauses))  # type: ignore[arg-type]  # missing every listed type
            else:
                query = query.where(or_(*clauses))  # type: ignore[arg-type]  # missing at least one listed type

    # Date filtering. date_from/date_to arrive as "YYYY-MM-DD" strings; the
    # date_added column is a tz-aware UtcDateTime, so the raw string must be
    # parsed to a tz-aware UTC datetime before comparison (otherwise the column's
    # bind hook raises "'str' object has no attribute 'tzinfo'" -> HTTP 500).
    # date_to is treated as inclusive of the whole day by using an exclusive
    # upper bound at the start of the following day.
    if filters.date_from:
        try:
            from_dt = datetime.strptime(filters.date_from, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="date_from must be in YYYY-MM-DD format.",
            ) from None
        query = query.where(Images.date_added >= from_dt)  # type: ignore[arg-type,operator]
    if filters.date_to:
        try:
            to_dt = datetime.strptime(filters.date_to, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="date_to must be in YYYY-MM-DD format.",
            ) from None
        query = query.where(Images.date_added < to_dt + timedelta(days=1))  # type: ignore[arg-type,operator]

    # Size filtering
    if filters.min_width:
        query = query.where(Images.width >= filters.min_width)  # type: ignore[arg-type]
    if filters.max_width:
        query = query.where(Images.width <= filters.max_width)  # type: ignore[arg-type]
    if filters.min_height:
        query = query.where(Images.height >= filters.min_height)  # type: ignore[arg-type]
    if filters.max_height:
        query = query.where(Images.height <= filters.max_height)  # type: ignore[arg-type]

    # Rating filtering
    if filters.min_rating is not None:
        query = query.where(Images.bayesian_rating >= filters.min_rating)  # type: ignore[arg-type]
    if filters.min_favorites is not None:
        query = query.where(Images.favorites >= filters.min_favorites)  # type: ignore[arg-type]
    if filters.min_num_ratings is not None:
        query = query.where(Images.num_ratings >= filters.min_num_ratings)  # type: ignore[arg-type]

    # Comment filtering: correlated EXISTS so an image with several matching
    # comments still surfaces once, with no DISTINCT needed downstream (#366).
    # Built by hand rather than via `_linked_to` because `apply_comment_text_search`
    # needs a `Select` to add its predicates to (see its docstring), not a
    # ColumnElement predicate list; the EXISTS/correlate shape mirrors `_linked_to`.
    if filters.commenter is not None or filters.commentsearch is not None:
        # Soft-deleted comments are excluded here so this agrees with /comments,
        # which filters them everywhere. Without it an image can match on a comment
        # the comment endpoint will never return, producing a result whose match is
        # invisible.
        comment_match = select(Comments.image_id).where(  # type: ignore[call-overload]
            Comments.deleted == False  # noqa: E712
        )
        if filters.commenter is not None:
            comment_match = comment_match.where(Comments.user_id == filters.commenter)
        if filters.commentsearch is not None:
            comment_match = apply_comment_text_search(
                comment_match, filters.commentsearch, filters.commentsearch_mode
            )
        query = query.where(
            comment_match.where(
                Comments.image_id == Images.image_id,
                *_seek_bounds(Comments.image_id, seekable),
            )
            .correlate(Images)
            .exists()
        )
    elif filters.hascomments is True:
        # Use posts counter field (fast indexed lookup)
        query = query.where(Images.posts > 0)  # type: ignore[arg-type]
    elif filters.hascomments is False:
        # Filter to images WITHOUT comments using posts counter
        query = query.where(Images.posts == 0)  # type: ignore[arg-type]

    # Exclude commenter filter: anti-join to exclude images commented on by specified
    # users. NOT EXISTS (not NOT IN (subquery) — see _not_linked_to) also sidesteps the
    # legacy posts table's nullable image_id for free: the correlation
    # `Comments.image_id == Images.image_id` can never match a NULL row, unlike SQL's
    # NOT IN, which returns UNKNOWN (not TRUE) for every row when the subquery yields
    # a NULL and would silently empty the whole search.
    exclude_commenter_ids = _parse_user_id_list(filters.exclude_commenter, "exclude_commenter")
    if exclude_commenter_ids:
        query = query.where(
            _not_linked_to(
                Comments,
                Comments.user_id.in_(exclude_commenter_ids),  # type: ignore[attr-defined]
                # Deleted comments must not hide an image, for the same reason
                # they must not surface one: /comments never shows them.
                Comments.deleted == False,  # type: ignore[arg-type]  # noqa: E712
                *_seek_bounds(Comments.image_id, seekable),
            )
        )

    # Reported filtering: restrict to images that have a PENDING report. Mods only
    # (REPORT_VIEW) so it never leaks the triage queue — silently a no-op for everyone else.
    apply_reported = (
        bool(filters.reported)
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
                    ImageReports.status == ReportStatus.PENDING,
                    *_seek_bounds(ImageReports.image_id, seekable),
                )
            )
        )

    # Content filters end here. `content_query` (no visibility WHERE) feeds the
    # decomposed count's shared public term; `query` gains the visibility clauses and
    # serves the page fetch and every other count path.
    return ImageFilterQuery(
        content_query=query,
        visibility_clauses=visibility_clauses,
        decompose_count=decompose_count,
        apply_reported=apply_reported,
    )
