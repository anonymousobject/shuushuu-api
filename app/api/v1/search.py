"""Search endpoint powered by Meilisearch."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from starlette import status

from app.api.dependencies import SortOrder, TagSortBy
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.tag import Tags
from app.schemas.search import SearchResponse, TagSearchHit
from app.services.artist_identity import parse_identity_query, resolve_identity
from app.services.search import SearchService

logger = get_logger(__name__)

router = APIRouter(prefix="/search", tags=["search"])


def get_search_service() -> SearchService:
    """Get the search service instance.

    This is overridden at startup once Meilisearch is initialized.
    Returns 503 if Meilisearch is not available.
    """
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Search service is not available",
    )


async def _identity_hit_already_on_first_page(
    search_service: SearchService,
    tag_id: int,
    q: str,
    *,
    limit: int,
    type_id: int | None,
    exclude_aliases: bool,
    sort: list[str] | None,
) -> bool:
    """Whether Meilisearch's first page (offset=0) for this query already has tag_id.

    Used only when the caller requested a later page, so the exact-identity
    layer's "already found by Meilisearch" check has a stable, page-
    independent answer — see the comment in `search()`. Best-effort: a
    failure here shouldn't 503 an otherwise-successful request, so it's
    treated as "already found" (no `total` inflation) rather than raising.
    """
    try:
        first_page = await search_service.search_tags(
            q,
            limit=limit,
            offset=0,
            type_filter=type_id,
            exclude_aliases=exclude_aliases,
            sort=sort,
        )
    except Exception:
        logger.warning("meilisearch_identity_first_page_check_failed", query=q, exc_info=True)
        return True
    return tag_id in first_page.tag_ids


@router.get("", response_model=SearchResponse)
async def search(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[
        str,
        Query(
            max_length=200, description="Search query (empty = list all, filter+sort still apply)"
        ),
    ] = "",
    type_id: Annotated[int | None, Query(description="Filter by tag type", alias="type")] = None,
    exclude_aliases: Annotated[bool, Query(description="Exclude alias tags")] = False,
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 20,
    offset: Annotated[int, Query(ge=0, le=500_000, description="Results to skip")] = 0,
    sort_by: Annotated[
        TagSortBy | None,
        Query(description="Sort field (omit for relevance ranking)"),
    ] = None,
    sort_order: Annotated[SortOrder, Query(description="Sort order")] = "DESC",
    search_service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    """Search across entities using Meilisearch.

    Currently supports tag search. Returns results in relevance order unless
    sort_by is provided, in which case the user's sort dominates.
    """
    sort = [f"{sort_by}:{sort_order.lower()}"] if sort_by is not None else None

    try:
        result = await search_service.search_tags(
            q,
            limit=limit,
            offset=offset,
            type_filter=type_id,
            exclude_aliases=exclude_aliases,
            sort=sort,
        )
    except Exception:
        logger.warning("meilisearch_search_failed", query=q, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search service is temporarily unavailable",
        ) from None

    # Fetch full tag records from MySQL, preserving Meilisearch order.
    # Outerjoin a self-aliased Tags so alias hits include the parent's title
    # as alias_of_name — same pattern as list_tags in app/api/v1/tags.py.
    hits: list[TagSearchHit] = []
    if result.tag_ids:
        AliasedTag = aliased(Tags)
        query = (
            select(
                Tags,
                AliasedTag.title.label("alias_of_name"),  # type: ignore[union-attr]
                AliasedTag.usage_count.label("alias_of_usage_count"),  # type: ignore[attr-defined]
            )
            .outerjoin(AliasedTag, Tags.alias_of == AliasedTag.tag_id)  # type: ignore[arg-type]
            .where(Tags.tag_id.in_(result.tag_ids))  # type: ignore[union-attr]
        )
        db_result = await db.execute(query)
        rows_by_id = {
            tag.tag_id: (tag, alias_of_name, alias_of_usage_count)
            for tag, alias_of_name, alias_of_usage_count in db_result.all()
        }

        for tag_id in result.tag_ids:
            row = rows_by_id.get(tag_id)
            if row:
                tag, alias_of_name, alias_of_usage_count = row
                hit = TagSearchHit.model_validate(tag)
                hit.alias_of_name = alias_of_name
                hit.alias_of_usage_count = alias_of_usage_count
                hits.append(hit)

    total = result.total

    # Exact artist-identity layer: if the query names a specific external
    # identity (bare ID, "pixiv <id>", or a profile URL), prepend the tag that
    # owns it — even if Meilisearch's fuzzy match missed or ranked it lower.
    # Runs after the Meilisearch call above, so Meilisearch downtime still
    # 503s exactly as before this layer existed.
    #
    # The owning tag must still satisfy the request's own `type`/
    # `exclude_aliases` filters before it's surfaced — this layer supplements
    # Meilisearch's ranking, it doesn't bypass the filters Meilisearch itself
    # enforced. Skipping this check would leak the tag into type-filtered or
    # alias-excluded result sets it doesn't belong in.
    #
    # Pagination semantics: the hit is only ever injected/reflagged into the
    # FIRST page (offset == 0). Doing this on later pages too would make the
    # tag appear to "jump" into view on every page as a user paginates, and
    # — the actual bug this comment replaces — checking for it against a
    # later page's own (unrelated) `hits` almost never finds it there, so
    # every page would independently decide it's "new" and re-increment
    # `total`, giving each page a different, ever-growing total for the same
    # query. To keep `total` identical no matter which page is requested,
    # "already found by Meilisearch" is always resolved against Meilisearch's
    # first page for this query — reusing this request's own result when
    # offset is already 0, otherwise issuing one extra lookup at offset=0.
    identity = parse_identity_query(q) if q else None
    if identity is not None:
        exact_tag = await resolve_identity(db, identity)
        if (
            exact_tag is not None
            and (type_id is None or exact_tag.type == type_id)
            and not (exclude_aliases and exact_tag.alias_of is not None)
        ):
            label = f"{identity.site} {identity.external_id}"
            if offset == 0:
                already_found = exact_tag.tag_id in result.tag_ids
            else:
                already_found = await _identity_hit_already_on_first_page(
                    search_service,
                    exact_tag.tag_id,  # type: ignore[arg-type]
                    q,
                    limit=limit,
                    type_id=type_id,
                    exclude_aliases=exclude_aliases,
                    sort=sort,
                )
            if not already_found:
                total += 1

            if offset == 0:
                existing = next((h for h in hits if h.tag_id == exact_tag.tag_id), None)
                if existing is not None:
                    hits.remove(existing)
                    existing.matched_identity = label
                    hits.insert(0, existing)
                else:
                    exact_hit = TagSearchHit.model_validate(exact_tag)
                    exact_hit.matched_identity = label
                    hits.insert(0, exact_hit)
                    # Prepending onto an already-full page would exceed the
                    # requested page size; drop the lowest-ranked meili hit
                    # to keep the limit contract.
                    hits = hits[:limit]

    return SearchResponse(
        query=q,
        entity="tags",
        hits=hits,
        total=total,
        limit=limit,
        offset=offset,
    )
