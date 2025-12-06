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
    """
    Response schema for a single tag suggestion.

    Represents an ML-generated tag suggestion for an image, including the suggested tag,
    confidence score, model source, and review status.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "suggestion_id": 1,
                "tag": {
                    "tag_id": 46,
                    "title": "long hair",
                    "type": 1,
                },
                "confidence": 0.92,
                "model_source": "custom_theme",
                "status": "pending",
                "created_at": "2025-12-04T12:00:00Z",
                "reviewed_at": None,
            }
        },
    )

    suggestion_id: int = Field(description="Unique identifier for this suggestion")
    tag: TagResponse = Field(description="The suggested tag with details")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score from ML model (0.0 to 1.0). Higher = more confident.",
    )
    model_source: Literal["custom_theme", "danbooru"] = Field(
        description="Which ML model generated this suggestion"
    )
    status: Literal["pending", "approved", "rejected"] = Field(
        description="Review status: pending (not reviewed), approved (applied to image), rejected (dismissed)"
    )
    created_at: datetime = Field(description="When this suggestion was generated")
    reviewed_at: datetime | None = Field(
        default=None, description="When this suggestion was reviewed (null if not yet reviewed)"
    )


class TagSuggestionsListResponse(BaseModel):
    """
    Response schema for list of suggestions for an image.

    Contains all suggestions for a specific image along with status counts,
    useful for displaying review interfaces and tracking review progress.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "image_id": 12345,
                "suggestions": [
                    {
                        "suggestion_id": 1,
                        "tag": {"tag_id": 46, "title": "long hair", "type": 1},
                        "confidence": 0.92,
                        "model_source": "custom_theme",
                        "status": "pending",
                        "created_at": "2025-12-04T12:00:00Z",
                        "reviewed_at": None,
                    },
                    {
                        "suggestion_id": 2,
                        "tag": {"tag_id": 161, "title": "blue eyes", "type": 1},
                        "confidence": 0.88,
                        "model_source": "custom_theme",
                        "status": "pending",
                        "created_at": "2025-12-04T12:00:00Z",
                        "reviewed_at": None,
                    },
                ],
                "total": 2,
                "pending": 2,
                "approved": 0,
                "rejected": 0,
            }
        }
    )

    image_id: int = Field(description="ID of the image these suggestions belong to")
    suggestions: list[TagSuggestionResponse] = Field(
        description="List of tag suggestions (may be filtered by status)"
    )
    total: int = Field(description="Total number of suggestions in the filtered list")
    pending: int = Field(description="Count of pending (not yet reviewed) suggestions for this image")
    approved: int = Field(description="Count of approved suggestions for this image")
    rejected: int = Field(description="Count of rejected suggestions for this image")


class ReviewSuggestionRequest(BaseModel):
    """
    Request schema for reviewing a single suggestion.

    Used as part of a batch review request to approve or reject one suggestion.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "suggestion_id": 1,
                "action": "approve",
            }
        }
    )

    suggestion_id: int = Field(description="ID of the suggestion to review")
    action: Literal["approve", "reject"] = Field(
        description="Action to take: 'approve' applies tag to image, 'reject' dismisses suggestion"
    )


class ReviewSuggestionsRequest(BaseModel):
    """
    Request schema for reviewing multiple suggestions in batch.

    Allows reviewing multiple suggestions for an image in a single request,
    each with its own approve/reject action.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "suggestions": [
                    {"suggestion_id": 1, "action": "approve"},
                    {"suggestion_id": 2, "action": "approve"},
                    {"suggestion_id": 3, "action": "reject"},
                ]
            }
        }
    )

    suggestions: list[ReviewSuggestionRequest] = Field(
        description="List of suggestions to review with their actions"
    )


class ReviewSuggestionsResponse(BaseModel):
    """
    Response schema for review action.

    Reports the outcome of a batch review operation, including counts
    of approved/rejected suggestions and any errors encountered.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "approved": 2,
                "rejected": 1,
                "errors": [],
            }
        }
    )

    approved: int = Field(description="Number of suggestions successfully approved")
    rejected: int = Field(description="Number of suggestions successfully rejected")
    errors: list[str] = Field(
        default_factory=list,
        description="List of error messages for suggestions that failed to process",
    )


class TagSuggestionStatsResponse(BaseModel):
    """
    Response schema for tag suggestion statistics.

    Provides system-wide analytics about tag suggestion performance,
    useful for monitoring ML model quality and user engagement.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total_suggestions": 50000,
                "pending": 12000,
                "approved": 35000,
                "rejected": 3000,
                "approval_rate": 0.92,
                "by_model": {
                    "custom_theme": {"pending": 5000, "approved": 20000, "rejected": 1500},
                    "danbooru": {"pending": 7000, "approved": 15000, "rejected": 1500},
                },
                "top_suggested_tags": [
                    {"tag_id": 46, "title": "long hair", "count": 15000},
                    {"tag_id": 161, "title": "blue eyes", "count": 12000},
                ],
            }
        }
    )

    total_suggestions: int = Field(description="Total number of suggestions generated")
    pending: int = Field(description="Number of suggestions awaiting review")
    approved: int = Field(description="Number of approved suggestions")
    rejected: int = Field(description="Number of rejected suggestions")
    approval_rate: float = Field(
        description="Ratio of approved suggestions to total reviewed (0.0 to 1.0)"
    )
    by_model: dict[str, dict[str, int]] = Field(
        description="Breakdown of suggestions by model source with status counts"
    )
    top_suggested_tags: list[dict[str, int | str]] = Field(
        description="Most frequently suggested tags with counts"
    )


class SuggestionStatusResponse(BaseModel):
    """
    Response schema for suggestion generation status.

    Used to check the status of the background job that generates
    tag suggestions after an image is uploaded.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "completed",
                "pending_count": 8,
                "job_id": "arq:12345-67890-abcdef",
            }
        }
    )

    status: Literal["queued", "processing", "completed", "failed"] = Field(
        description="Current status of the suggestion generation job"
    )
    pending_count: int = Field(description="Number of pending suggestions generated (0 if not completed)")
    job_id: str | None = Field(
        default=None, description="Background job ID (if available) for status tracking"
    )
