"""Pydantic schemas for the cross-image ML suggestion review queue.

These back the ``/ml-suggestions`` router (worklist counts, per-tag paginated
grid, and cross-image bulk review). They are distinct from the per-image
schemas in ``app/schemas/ml_tag_suggestion.py``.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.image import ImageResponse, TagSummary


class SuggestionTagWorklistItem(BaseModel):
    """One tag's pending-suggestion count for the review worklist."""

    tag_id: int = Field(description="ID of the tag")
    title: str | None = Field(default=None, description="Tag title")
    type: int = Field(description="Tag type id (e.g. 1=theme, 4=character)")
    pending_count: int = Field(description="Number of pending suggestions for this tag")


class SuggestionTagWorklistResponse(BaseModel):
    """Paginated response for the detected-tags worklist."""

    items: list[SuggestionTagWorklistItem] = Field(
        description="Tags with pending suggestions for this page, ordered by pending_count DESC"
    )
    total: int = Field(description="Total number of distinct tags with pending suggestions")
    page: int = Field(description="1-based page number for this result set")


class SuggestionGridItem(BaseModel):
    """A single pending suggestion in the per-tag review grid.

    Embeds the full ``ImageResponse`` so the frontend gets the computed
    ``thumbnail_url`` (and other URL fields) rather than a raw filename.
    Also carries the image's currently-applied tags so the frontend can
    spot redundant suggestions without a second request.
    """

    suggestion_id: int = Field(description="ID of the suggestion")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score from the ML model (0.0 to 1.0)",
    )
    image: ImageResponse = Field(description="The image this suggestion is for")
    tags: list[TagSummary] = Field(
        default_factory=list,
        description="Tags currently applied to the image (for redundancy detection)",
    )


class SuggestionGridResponse(BaseModel):
    """A page of pending suggestions for one tag, sorted by confidence DESC."""

    items: list[SuggestionGridItem] = Field(
        description="Pending suggestions for this page, ordered by confidence DESC"
    )
    total: int = Field(description="Total pending suggestions matching tag + min_confidence")
    page: int = Field(description="1-based page number for this result set")
    tag: TagSummary | None = Field(
        default=None,
        description="Summary of the tag being reviewed (tag_id, title, type_id)",
    )


class BulkReviewItem(BaseModel):
    """A single approve/reject decision in a cross-image bulk review request."""

    suggestion_id: int = Field(description="ID of the suggestion to review")
    action: Literal["approve", "reject"] = Field(
        description="Action to take: 'approve' applies the tag, 'reject' dismisses it"
    )


class BulkReviewResult(BaseModel):
    """Outcome of a cross-image bulk review."""

    approved: int = Field(description="Number of suggestions approved")
    rejected: int = Field(description="Number of suggestions rejected")
    errors: list[str] = Field(
        default_factory=list,
        description="Messages for suggestions that could not be processed",
    )
