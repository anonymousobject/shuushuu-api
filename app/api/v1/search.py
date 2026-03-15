"""Search endpoint powered by Meilisearch."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core.database import get_db
from app.models.tag import Tags
from app.schemas.search import SearchResponse, TagSearchHit
from app.services.search import SearchService

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
    q: Annotated[str, Query(min_length=1, max_length=200, description="Search query")],
    db: Annotated[AsyncSession, Depends(get_db)],
    type_id: Annotated[int | None, Query(description="Filter by tag type", alias="type")] = None,
    exclude_aliases: Annotated[bool, Query(description="Exclude alias tags")] = False,
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 20,
    offset: Annotated[int, Query(ge=0, description="Results to skip")] = 0,
    search_service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    """Search across entities using Meilisearch.

    Currently supports tag search. Returns results in relevance order.
    """
    result = await search_service.search_tags(
        q,
        limit=limit,
        offset=offset,
        type_filter=type_id,
        exclude_aliases=exclude_aliases,
    )

    # Fetch full tag records from MySQL, preserving Meilisearch order
    hits: list[TagSearchHit] = []
    if result.tag_ids:
        query = select(Tags).where(Tags.tag_id.in_(result.tag_ids))  # type: ignore[union-attr]
        db_result = await db.execute(query)
        tags_by_id = {tag.tag_id: tag for tag in db_result.scalars().all()}

        for tag_id in result.tag_ids:
            tag = tags_by_id.get(tag_id)
            if tag:
                hits.append(TagSearchHit.model_validate(tag))

    return SearchResponse(
        query=q,
        entity="tags",
        hits=hits,
        total=result.total,
        limit=limit,
        offset=offset,
    )
