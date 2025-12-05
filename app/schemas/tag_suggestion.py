# app/schemas/tag_suggestion.py

"""
Tag Suggestion Pydantic Schemas

Request/response schemas for tag suggestion API endpoints.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.tag import TagResponse


class TagSuggestionResponse(BaseModel):
    """Response schema for a single tag suggestion"""

    model_config = ConfigDict(from_attributes=True)

    suggestion_id: int
    tag: TagResponse
    confidence: float = Field(ge=0.0, le=1.0)
    model_source: Literal["custom_theme", "danbooru"]
    status: Literal["pending", "approved", "rejected"]
    created_at: datetime
    reviewed_at: datetime | None = None


class TagSuggestionsListResponse(BaseModel):
    """Response schema for list of suggestions for an image"""

    image_id: int
    suggestions: list[TagSuggestionResponse]
    total: int
    pending: int
    approved: int
    rejected: int


class ReviewSuggestionRequest(BaseModel):
    """Request schema for reviewing a single suggestion"""

    suggestion_id: int
    action: Literal["approve", "reject"]


class ReviewSuggestionsRequest(BaseModel):
    """Request schema for reviewing multiple suggestions"""

    suggestions: list[ReviewSuggestionRequest]


class ReviewSuggestionsResponse(BaseModel):
    """Response schema for review action"""

    approved: int
    rejected: int
    errors: list[str] = []


class TagSuggestionStatsResponse(BaseModel):
    """Response schema for tag suggestion statistics"""

    total_suggestions: int
    pending: int
    approved: int
    rejected: int
    approval_rate: float
    by_model: dict[str, dict[str, int]]
    top_suggested_tags: list[dict[str, int | str]]


class SuggestionStatusResponse(BaseModel):
    """Response schema for suggestion generation status"""

    status: Literal["queued", "processing", "completed", "failed"]
    pending_count: int
    job_id: str | None = None
