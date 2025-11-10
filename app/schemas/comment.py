"""
Pydantic schemas for Comment endpoints
"""

from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.comment import CommentBase


class CommentCreate(CommentBase):
    """Schema for creating a new comment"""

    user_id: int


class CommentUpdate(BaseModel):
    """Schema for updating a comment - all fields optional"""

    post_text: str | None = None


class CommentResponse(CommentBase):
    """
    Schema for comment response - what API returns.

    Inherits public fields from CommentBase and adds additional public metadata.
    Does NOT include internal fields like IP, user agent, etc.
    """

    post_id: int
    user_id: int
    date: datetime
    update_count: int
    last_updated: datetime | None = None
    last_updated_user_id: int | None = None


class CommentListResponse(BaseModel):
    """Schema for paginated comment list"""

    total: int
    page: int
    per_page: int
    comments: list[CommentResponse]


class CommentSearchParams(BaseModel):
    """Schema for comment search parameters"""

    image_id: int | None = None
    user_id: int | None = None
    search_text: str | None = None
    sort_by: str = "date"
    sort_order: str = "DESC"
    page: int = 1
    per_page: int = 20

    @field_validator("sort_by")
    @classmethod
    def validate_sort_by(cls, v: str) -> str:
        """Validate sort_by field"""
        allowed_fields = ["date", "post_id", "update_count"]
        if v not in allowed_fields:
            raise ValueError(f"sort_by must be one of: {', '.join(allowed_fields)}")
        return v

    @field_validator("sort_order")
    @classmethod
    def validate_sort_order(cls, v: str) -> str:
        """Validate sort_order field"""
        v = v.upper()
        if v not in ["ASC", "DESC"]:
            raise ValueError("sort_order must be ASC or DESC")
        return v


class CommentStatsResponse(BaseModel):
    """Schema for comment statistics response"""

    total_comments: int
    total_images_with_comments: int
    average_comments_per_image: float
