"""Pydantic schemas for the search endpoint."""

from pydantic import BaseModel, Field

from app.schemas.tag import TagResponse


class SearchRequest(BaseModel):
    """Query parameters for the search endpoint."""

    q: str = Field(min_length=1, max_length=200, description="Search query")
    entity: str = Field(default="tags", pattern="^(tags)$", description="Entity type to search")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum results to return")
    offset: int = Field(default=0, ge=0, description="Number of results to skip")


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
