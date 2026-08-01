"""Pydantic schemas for the search endpoint."""

from pydantic import BaseModel

from app.schemas.tag import TagResponse


class TagSearchHit(TagResponse):
    """A tag search result from Meilisearch, extending the standard tag response."""

    model_config = {"from_attributes": True}

    # Set when this hit was resolved by the exact artist-identity layer rather
    # than (or in addition to) Meilisearch's fuzzy match. Format: "{site}
    # {external_id}", e.g. "pixiv 21412050". None for ordinary fuzzy hits.
    matched_identity: str | None = None


class SearchResponse(BaseModel):
    """Response from the search endpoint."""

    query: str
    entity: str
    hits: list[TagSearchHit]
    total: int
    limit: int
    offset: int
