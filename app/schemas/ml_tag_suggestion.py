"""
ML Tag Suggestion Pydantic Schemas

Request/response schemas for the ML tag suggestion API endpoints.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UserSummary
from app.schemas.tag import TagResponse


class MlTagSuggestionResponse(BaseModel):
    """
    Response schema for a single ML tag suggestion.

    Represents an ML-generated tag suggestion for an image, including the suggested tag,
    confidence score, model version, and review status.
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
                "model_version": "wd-swinv2-tagger-v3",
                "status": "pending",
                "created_at": "2025-12-04T12:00:00Z",
                "reviewed_at": None,
                "reviewed_by": None,
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
    model_version: str = Field(description="Version of the ML model that generated this suggestion")
    status: Literal["pending", "approved", "rejected"] = Field(
        description="Review status: pending (not reviewed), approved (applied to image), rejected (dismissed)"
    )
    created_at: datetime = Field(description="When this suggestion was generated")
    reviewed_at: datetime | None = Field(
        default=None, description="When this suggestion was reviewed (null if not yet reviewed)"
    )
    reviewed_by: UserSummary | None = Field(
        default=None,
        description=(
            "Who reviewed this suggestion. Null for unreviewed rows and for "
            "system resolutions (backfill, repost tag migration)."
        ),
    )
    superseded_suggestion_ids: list[int] = Field(
        default_factory=list,
        description=(
            "suggestion_ids of OTHER pending suggestions on this image that "
            "approving this suggestion would cascade-delete (its ancestor tags "
            "via Tags.inheritedfrom_id). Always empty for reviewed suggestions."
        ),
    )


class MlTagSuggestionsListResponse(BaseModel):
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
                        "model_version": "wd-swinv2-tagger-v3",
                        "status": "pending",
                        "created_at": "2025-12-04T12:00:00Z",
                        "reviewed_at": None,
                        "reviewed_by": None,
                    },
                    {
                        "suggestion_id": 2,
                        "tag": {"tag_id": 161, "title": "blue eyes", "type": 1},
                        "confidence": 0.88,
                        "model_version": "wd-swinv2-tagger-v3",
                        "status": "pending",
                        "created_at": "2025-12-04T12:00:00Z",
                        "reviewed_at": None,
                        "reviewed_by": None,
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
    suggestions: list[MlTagSuggestionResponse] = Field(
        description="List of tag suggestions (may be filtered by status)"
    )
    total: int = Field(description="Total number of suggestions in the filtered list")
    pending: int = Field(
        description="Count of pending (not yet reviewed) suggestions for this image"
    )
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
    removed_suggestion_ids: list[int] = Field(
        default_factory=list,
        description=(
            "suggestion_ids of PENDING ancestor suggestions cascade-deleted because a "
            "more specific descendant tag was applied during this review"
        ),
    )


class GenerateSuggestionsResponse(BaseModel):
    """
    Response schema for triggering tag suggestion generation.

    Returned when a user requests ML tag suggestions to be generated
    for an existing image. Response varies based on sync parameter:
    - sync=false (default): Returns job_id for background processing
    - sync=true: Returns suggestions_created count after inline processing
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Tag suggestion generation queued",
                "image_id": 12345,
                "job_id": "arq:generate-12345",
                "suggestions_created": None,
            }
        }
    )

    message: str = Field(description="Status message")
    image_id: int = Field(description="ID of the image for which suggestions are being generated")
    job_id: str | None = Field(
        default=None, description="Background job ID for tracking (async mode only)"
    )
    suggestions_created: int | None = Field(
        default=None, description="Number of suggestions created (sync mode only)"
    )
