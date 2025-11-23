"""
Pydantic schemas for image reporting and review system.

These schemas handle:
- User-submitted image reports
- Admin report triage (dismiss/action/escalate)
- Review sessions (voting process)
- Admin actions audit log
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.config import ReportCategory

# ===== Report Schemas =====


class ReportCreate(BaseModel):
    """Schema for creating a new image report."""

    category: int = Field(
        ...,
        description="Report category (1=repost, 2=inappropriate, 3=spam, 4=missing_tags, 127=other)",
    )
    reason_text: str | None = Field(None, max_length=1000, description="Optional explanation")


class ReportResponse(BaseModel):
    """Response schema for a report."""

    report_id: int
    image_id: int
    user_id: int
    category: int | None
    category_label: str | None = None
    reason_text: str | None
    status: int
    status_label: str | None = None
    created_at: datetime | None
    reviewed_by: int | None
    reviewed_at: datetime | None

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
    created_at: datetime | None

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
    initiated_by: int | None
    initiated_by_username: str | None = None
    review_type: int
    review_type_label: str | None = None
    deadline: datetime | None
    extension_used: int
    status: int
    status_label: str | None = None
    outcome: int
    outcome_label: str | None = None
    created_at: datetime | None
    closed_at: datetime | None
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


# ===== Simple Response =====


class MessageResponse(BaseModel):
    """Simple message response for success operations."""

    message: str
