"""
Pydantic schemas for image reporting and review system.

These schemas handle:
- User-submitted image reports
- Admin report triage (dismiss/action/escalate)
- Review sessions (voting process)
- Admin actions audit log
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import DeactivationReason, ReportCategory
from app.schemas.base import UTCDatetime, UTCDatetimeOptional
from app.schemas.comment_report import CommentReportListItem
from app.schemas.common import UserSummary

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
    def validate_tag_suggestions(self) -> ReportCreate:
        """Validate tag suggestions are only for TAG_SUGGESTIONS category."""
        has_suggestions = self.suggested_tag_ids_add or self.suggested_tag_ids_remove
        if has_suggestions and self.category != ReportCategory.TAG_SUGGESTIONS:
            raise ValueError("Tag suggestions only allowed for TAG_SUGGESTIONS reports")
        return self


class ReportResponse(BaseModel):
    """Response schema for a report."""

    report_id: int
    image_id: int
    user: UserSummary | None = None
    category: int | None
    category_label: str | None = None
    reason_text: str | None
    status: int
    status_label: str | None = None
    created_at: UTCDatetime
    reviewed_by_user: UserSummary | None = None
    reviewed_at: UTCDatetimeOptional = None
    admin_notes: str | None = None
    suggested_tags: list[TagSuggestion] | None = None
    skipped_tags: SkippedTagsInfo | None = None  # Only in create response
    # Resolution, derived from the audit log for reviewed/dismissed reports.
    # "action" = image status changed; "tags" = tag suggestions applied (no status
    # change); "escalated" = sent to review; "dismissed" = no action.
    resolution_kind: Literal["action", "tags", "escalated", "dismissed"] | None = None
    resolution_status: int | None = None
    resolution_status_label: str | None = None
    resolution_reason: str | None = None

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
    """Schema for taking action on a report — mirrors the deactivate contract.

    Legacy INAPPROPRIATE(-2)/LOW_QUALITY(-3)/REVIEW(-4) are no longer settable here:
    inappropriate/low-quality/spam all become DEACTIVATED + a reason_category, and
    escalation to review is a separate endpoint.
    """

    new_status: int = Field(..., description="0=Deactivated, -1=Repost, 1=Active, 2=Spoiler")
    replacement_id: int | None = Field(None, description="Required when new_status=-1 (repost)")
    reason_category: int | None = Field(
        None, description="Required when new_status=0: 1=Inappropriate,2=Low Quality,3=Spam,4=Other"
    )
    reason: str | None = Field(None, max_length=1000, description="Required when new_status=0")

    @field_validator("new_status")
    @classmethod
    def validate_status(cls, v: int) -> int:
        from app.config import ImageStatus

        settable = {
            ImageStatus.DEACTIVATED,
            ImageStatus.REPOST,
            ImageStatus.ACTIVE,
            ImageStatus.SPOILER,
        }
        if v not in settable:
            raise ValueError(
                "new_status must be one of: 0=Deactivated, -1=Repost, 1=Active, 2=Spoiler"
            )
        return v

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return v.strip() or None

    @model_validator(mode="after")
    def validate_combination(self) -> ReportActionRequest:
        from app.config import DeactivationReason, ImageStatus

        if self.new_status == ImageStatus.DEACTIVATED:
            if self.reason_category not in DeactivationReason.VALID:
                raise ValueError("reason_category is required and must be valid when deactivating")
            if not self.reason:
                raise ValueError("reason is required when deactivating")
        elif self.reason_category is not None:
            raise ValueError("reason_category is only valid when deactivating")
        return self


class ReportEscalateRequest(BaseModel):
    """Schema for escalating a report to a review."""

    deadline_days: int | None = Field(
        None, ge=1, le=30, description="Days until voting deadline (default: 7)"
    )
    reason_category: int = Field(..., description="1=Inappropriate, 2=Low Quality, 3=Spam, 4=Other")
    reason: str = Field(..., min_length=1, max_length=1000, description="Reason for the review")

    @field_validator("reason_category")
    @classmethod
    def validate_reason_category(cls, v: int) -> int:
        if v not in DeactivationReason.VALID:
            raise ValueError("reason_category must be one of: 1, 2, 3, 4")
        return v

    @field_validator("reason")
    @classmethod
    def sanitize_reason(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("reason must not be empty")
        return stripped


# ===== Review Schemas =====


class ReviewCreate(BaseModel):
    """Schema for creating a new review directly on an image."""

    deadline_days: int | None = Field(
        None, ge=1, le=30, description="Days until voting deadline (default: 7)"
    )
    reason_category: int = Field(..., description="1=Inappropriate, 2=Low Quality, 3=Spam, 4=Other")
    reason: str = Field(
        ..., min_length=1, max_length=1000, description="Reason for starting the review"
    )

    @field_validator("reason_category")
    @classmethod
    def validate_reason_category(cls, v: int) -> int:
        if v not in DeactivationReason.VALID:
            raise ValueError("reason_category must be one of: 1, 2, 3, 4")
        return v

    @field_validator("reason")
    @classmethod
    def sanitize_reason(cls, v: str) -> str:
        """Sanitize review reason."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("reason must not be empty")
        return stripped


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
    user: UserSummary | None = None
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
    reason: str | None = None
    initiated_by_user: UserSummary | None = None
    closed_by_user: UserSummary | None = None
    reason_category: int
    reason_category_label: str | None = None
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
        # Reason category
        self.reason_category_label = DeactivationReason.get_label(self.reason_category)
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
