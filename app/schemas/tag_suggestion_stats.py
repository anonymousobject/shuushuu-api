"""
Pydantic schemas for tag suggestion statistics.
"""

from pydantic import BaseModel


class TagSuggestionUserStats(BaseModel):
    """Per-user tag suggestion statistics."""

    user_id: int
    username: str
    total_suggestions: int
    accepted_count: int
    rejected_count: int
    pending_count: int
    acceptance_rate: float | None  # None when no decided suggestions
    add_count: int
    remove_count: int


class TagSuggestionStatsResponse(BaseModel):
    """Response for tag suggestion stats endpoint."""

    items: list[TagSuggestionUserStats]
    total: int
    page: int
    per_page: int
