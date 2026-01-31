"""
Pydantic schemas for image reporting and review system.

These schemas handle:
- User-submitted image reports
- Admin report triage (dismiss/action/escalate)
- Review sessions (voting process)
- Admin actions audit log
"""

from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import ReportCategory
from app.schemas.base import UTCDatetime, UTCDatetimeOptional
from app.schemas.comment_report import CommentReportListItem

# ===== Tag Suggestion Schemas =====


class TagSuggestion(BaseModel):
    """Schema for a tag suggestion in a report response."""

    suggestion_id: int
    tag_id: int
    tag_name: str
    tag_type: int | None = None
    suggestion_type: int = 1  # 1=add, 2=remove
    accepted: bool | None = None  # NULL=pending, True=approved, False=rejected

    model_config = {"from_attributes": True}


class SkippedTagsInfo(BaseModel):
    """Feedback about tags that were skipped during report creation."""

    already_on_image: list[int] = []  # Addition skipped: tag already present
    not_on_image: list[int] = []  # Removal skipped: tag not on image
    invalid_tag_ids: list[int] = []  # Tag ID doesn't exist


# ===== Report Schemas =====


class ReportCreate(BaseModel):
    """Schema for creating a new image report."""

    category: int = Field(
        ...,
        description="Report category (1=repost, 2=inappropriate, 3=spam, 4=tag_suggestions, 127=other)",
    )
    reason_text: str | None = Field(None, max_length=1000, description="Optional explanation")
    suggested_tag_ids_add: list[int] | None = Field(
        None,
        description="Tag IDs to suggest adding (only for TAG_SUGGESTIONS category)",
    )
    suggested_tag_ids_remove: list[int] | None = Field(
        None,
        description="Tag IDs to suggest removing (only for TAG_SUGGESTIONS category)",
    )

    @field_validator("reason_text")
    @classmethod
    def sanitize_reason_text(cls, v: str | None) -> str | None:
        """Sanitize report reason."""
        if v is None:
            return v
        return v.strip()

    @field_validator("suggested_tag_ids_add", "suggested_tag_ids_remove")
    @classmethod
    def dedupe_tag_ids(cls, v: list[int] | None) -> list[int] | None:
        """Remove duplicate tag IDs while preserving order."""
        if not v:
            return None
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def validate_tag_suggestions(self) -> "ReportCreate":
        """Validate tag suggestions are only for TAG_SUGGESTIONS category."""
        has_suggestions = self.suggested_tag_ids_add or self.suggested_tag_ids_remove
        if has_suggestions and self.category != ReportCategory.TAG_SUGGESTIONS:
            raise ValueError("Tag suggestions only allowed for TAG_SUGGESTIONS reports")
        return self


class ReportResponse(BaseModel):
    """Response schema for a report."""

    report_id: int
    image_id: int
    user_id: int
    username: str | None = None
    category: int | None
    category_label: str | None = None
    reason_text: str | None
    status: int
    status_label: str | None = None
    created_at: UTCDatetime
    reviewed_by: int | None
    reviewed_at: UTCDatetimeOptional = None
    admin_notes: str | None = None
    suggested_tags: list[TagSuggestion] | None = None
    skipped_tags: SkippedTagsInfo | None = None  # Only in create response

    model_config = {"from_attributes": True}

    def model_post_init(self, __context: object) -> None:
        """Set computed label fields."""
        # Category label
        if self.category is not None:
            self.category_label = ReportCategory.LABELS.get(self.category, "Unknown")
        # Status label
        status_labels = {0: "Pending", 1: "Reviewed", 2: "Dismissed"}
        self.status_label = status_labels.get(self.status, "Unknown")


class ReportListResponse(BaseModel):
    """Response schema for listing reports."""

    total: int
    page: int
    per_page: int
    items: list[ReportResponse]


class UnifiedReportListResponse(BaseModel):
    """Response schema for unified report listing (both image and comment reports)."""

    image_reports: list[ReportResponse]
    comment_reports: list[CommentReportListItem]
    total: int
    page: int
    per_page: int


class ReportDismissRequest(BaseModel):
    """Schema for dismissing a report with optional notes."""

    admin_notes: str | None = Field(None, max_length=2000, description="Optional admin notes")


class ReportActionRequest(BaseModel):
    """Schema for taking action on a report (changing image status)."""

    new_status: int = Field(
        ...,
        description="New image status (-4=review, -3=low_quality, -2=inappropriate, -1=repost, 1=active)",
    )


class ReportEscalateRequest(BaseModel):
    """Schema for escalating a report to a review."""

    deadline_days: int | None = Field(
        None, ge=1, le=30, description="Days until voting deadline (default: 7)"
    )


# ===== Review Schemas =====


class ReviewCreate(BaseModel):
    """Schema for creating a new review directly on an image."""

    deadline_days: int | None = Field(
        None, ge=1, le=30, description="Days until voting deadline (default: 7)"
    )


class ReviewVoteRequest(BaseModel):
    """Schema for casting a vote on a review."""

    vote: int = Field(..., ge=0, le=1, description="Vote: 0=remove, 1=keep")
    comment: str | None = Field(None, max_length=1000, description="Optional reasoning")

    @field_validator("comment")
    @classmethod
    def sanitize_comment(cls, v: str | None) -> str | None:
        """
        Sanitize vote comment.

        Just trims whitespace - HTML escaping is handled by Svelte's
        safe template interpolation on the frontend.
        """
        if v is None:
            return v
        return v.strip()


class ReviewCloseRequest(BaseModel):
    """Schema for closing a review early."""

    outcome: int = Field(..., ge=1, le=2, description="Outcome: 1=keep, 2=remove")


class ReviewExtendRequest(BaseModel):
    """Schema for extending a review deadline."""

    days: int | None = Field(None, ge=1, le=14, description="Days to extend (default: 3)")


class VoteResponse(BaseModel):
    """Response schema for a vote."""

    vote_id: int
    review_id: int | None
    user_id: int | None
    username: str | None = None
    vote: int | None
    vote_label: str | None = None
    comment: str | None
    created_at: UTCDatetime

    model_config = {"from_attributes": True}

    def model_post_init(self, __context: object) -> None:
        """Set computed label fields."""
        if self.vote is not None:
            self.vote_label = "Keep" if self.vote == 1 else "Remove"


class ReviewResponse(BaseModel):
    """Response schema for a review."""

    review_id: int
    image_id: int
    source_report_id: int | None
    source_report_category: int | None = None
    source_report_category_label: str | None = None
    source_report_reason: str | None = None
    initiated_by: int | None
    initiated_by_username: str | None = None
    review_type: int
    review_type_label: str | None = None
    deadline: UTCDatetime
    extension_used: int
    status: int
    status_label: str | None = None
    outcome: int
    outcome_label: str | None = None
    created_at: UTCDatetime
    closed_at: UTCDatetimeOptional = None
    # Vote summary (populated by endpoint)
    vote_count: int = 0
    keep_votes: int = 0
    remove_votes: int = 0

    model_config = {"from_attributes": True}

    def model_post_init(self, __context: object) -> None:
        """Set computed label fields."""
        # Review type
        type_labels = {1: "Appropriateness"}
        self.review_type_label = type_labels.get(self.review_type, "Unknown")
        # Status
        status_labels = {0: "Open", 1: "Closed"}
        self.status_label = status_labels.get(self.status, "Unknown")
        # Outcome
        outcome_labels = {0: "Pending", 1: "Keep", 2: "Remove"}
        self.outcome_label = outcome_labels.get(self.outcome, "Unknown")


class ReviewDetailResponse(ReviewResponse):
    """Detailed review response including all votes."""

    votes: list[VoteResponse] = []


class ReviewListResponse(BaseModel):
    """Response schema for listing reviews."""

    total: int
    page: int
    per_page: int
    items: list[ReviewResponse]


# ===== Tag Suggestion Admin Schemas =====


class ApplyTagSuggestionsRequest(BaseModel):
    """Request schema for applying tag suggestions."""

    approved_suggestion_ids: list[int] = Field(..., description="IDs of suggestions to approve")
    admin_notes: str | None = Field(None, max_length=2000, description="Optional admin notes")


class ApplyTagSuggestionsResponse(BaseModel):
    """Response schema for apply tag suggestions endpoint."""

    message: str
    applied_tags: list[int]  # Tag IDs added to image
    removed_tags: list[int] = []  # Tag IDs removed from image
    already_present: list[int] = []  # Additions skipped (already on image)
    already_absent: list[int] = []  # Removals skipped (not on image)


# ===== Simple Response =====


class MessageResponse(BaseModel):
    """Simple message response for success operations."""

    message: str
