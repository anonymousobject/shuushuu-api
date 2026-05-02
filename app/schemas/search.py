"""Pydantic schemas for the search endpoint."""

from pydantic import BaseModel

from app.schemas.tag import TagResponse


class TagSearchHit(TagResponse):
    """A tag search result from Meilisearch, extending the standard tag response."""

    model_config = {"from_attributes": True}


class SearchResponse(BaseModel):
    """Response from the search endpoint."""

    query: str
    entity: str
    hits: list[TagSearchHit]
    total: int
    limit: int
    offset: int
