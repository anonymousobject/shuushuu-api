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

    return SearchResponse(
        query=q,
        entity="tags",
        hits=hits,
        total=result.total,
        limit=limit,
        offset=offset,
    )
