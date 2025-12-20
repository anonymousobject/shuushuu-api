"""
Pydantic schemas for Comment endpoints
"""

from datetime import datetime

from pydantic import BaseModel, Field, computed_field, field_validator

from app.models.comment import CommentBase
from app.utils.markdown import normalize_legacy_entities, parse_markdown


class UserSummary(BaseModel):
    """
    Minimal user information for embedding in comment responses.

    Used to avoid N+1 queries when clients need basic user info
    without fetching the full user profile.
    """

    user_id: int
    username: str
    avatar: str | None = None

    model_config = {"from_attributes": True}


class CommentCreate(BaseModel):
    """Schema for creating a new comment"""

    image_id: int = Field(description="ID of image to comment on")
    post_text: str = Field(min_length=1, description="Comment text (markdown supported)")
    parent_comment_id: int | None = Field(
        default=None,
        description="Parent comment ID for replies (null = top-level comment)",
    )

    @field_validator("post_text")
    @classmethod
    def sanitize_post_text(cls, v: str) -> str:
        """
        Sanitize markdown post text.

        For markdown fields, we store raw user input and let parse_markdown()
        handle HTML escaping at render time. We only trim whitespace here.
        """
        return v.strip()


class CommentUpdate(BaseModel):
    """Schema for updating a comment"""

    post_text: str = Field(min_length=1, description="Comment text (markdown supported)")

    @field_validator("post_text")
    @classmethod
    def sanitize_post_text(cls, v: str) -> str:
        """
        Sanitize markdown post text.

        For markdown fields, we store raw user input and let parse_markdown()
        handle HTML escaping at render time. We only trim whitespace here.
        """
        return v.strip()


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
    user: UserSummary  # Embedded user data to avoid N+1 queries

    @field_validator("post_text", mode="before")
    @classmethod
    def normalize_db_post_text(cls, v: str | None) -> str | None:
        """
        Normalize post text from database for legacy PHP data.

        Handles comments created in the old PHP codebase which stored data
        as HTML-encoded entities. New comments store raw text.
        """
        return normalize_legacy_entities(v)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def post_text_html(self) -> str:
        """Rendered HTML from markdown post_text"""
        return parse_markdown(self.post_text)


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
