"""
Pydantic schemas for comment reporting.
"""

from pydantic import BaseModel, Field, field_validator

from app.config import CommentReportCategory
from app.schemas.base import UTCDatetime, UTCDatetimeOptional
from app.schemas.common import UserSummary


class CommentReportCreate(BaseModel):
    """Schema for creating a new comment report."""

    category: int = Field(
        ...,
        description="Report category (1=rule_violation, 2=spam, 127=other)",
    )
    reason_text: str | None = Field(None, max_length=1000, description="Optional explanation")

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: int) -> int:
        """Validate category is a valid CommentReportCategory."""
        valid = {
            CommentReportCategory.RULE_VIOLATION,
            CommentReportCategory.SPAM,
            CommentReportCategory.OTHER,
        }
        if v not in valid:
            raise ValueError(f"Invalid category. Must be one of: {valid}")
        return v

    @field_validator("reason_text")
    @classmethod
    def sanitize_reason_text(cls, v: str | None) -> str | None:
        """Sanitize report reason."""
        if v is None:
            return v
        return v.strip()


class CommentReportResponse(BaseModel):
    """Response schema for a comment report."""

    report_id: int
    comment_id: int
    image_id: int | None = None  # Denormalized for convenience
    user_id: int
    username: str | None = None
    category: int | None
    category_label: str | None = None
    reason_text: str | None
    status: int
    status_label: str | None = None
    created_at: UTCDatetime
    reviewed_by: int | None = None
    reviewed_at: UTCDatetimeOptional = None
    admin_notes: str | None = None

    model_config = {"from_attributes": True}

    def model_post_init(self, __context: object) -> None:
        """Set computed label fields."""
        if self.category is not None:
            self.category_label = CommentReportCategory.LABELS.get(self.category, "Unknown")
        status_labels = {0: "Pending", 1: "Reviewed", 2: "Dismissed"}
        self.status_label = status_labels.get(self.status, "Unknown")


class CommentReportListItem(CommentReportResponse):
    """Extended response for admin listing."""

    comment_author: UserSummary | None = None
    comment_preview: str | None = None  # First 100 chars of comment
    comment_deleted: bool | None = None


class CommentReportDismissRequest(BaseModel):
    """Schema for dismissing a comment report."""

    admin_notes: str | None = Field(None, max_length=2000, description="Optional admin notes")


class CommentReportDeleteRequest(BaseModel):
    """Schema for deleting a reported comment."""

    admin_notes: str | None = Field(None, max_length=2000, description="Optional admin notes")
