"""
Common query parameter models for API endpoints.

These Pydantic models are used with FastAPI's Depends() to provide reusable
query parameter sets, reducing code duplication across routes.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, computed_field

from app.models.image import ImageSortBy


def _normalize_sort_order(v: str) -> str:
    """Normalize sort order to uppercase for case-insensitive matching."""
    return v.upper() if isinstance(v, str) else v


SortOrder = Annotated[Literal["ASC", "DESC"], BeforeValidator(_normalize_sort_order)]


class PaginationParams(BaseModel):
    """Common pagination query parameters."""

    page: int = Field(default=1, ge=1, description="Page number")
    per_page: int = Field(default=20, ge=1, le=100, description="Items per page")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def offset(self) -> int:
        """Calculate offset from page and per_page."""
        return (self.page - 1) * self.per_page


class ImageSortParams(BaseModel):
    """Common sorting parameters for image queries."""

    sort_by: ImageSortBy = Field(default=ImageSortBy.image_id, description="Sort field")
    sort_order: SortOrder = Field(default="DESC", description="Sort order")


class CommentSortParams(BaseModel):
    """Common sorting parameters for comment queries."""

    sort_by: Literal["post_id", "date", "update_count"] = Field(
        default="date", description="Sort field"
    )
    sort_order: SortOrder = Field(default="DESC", description="Sort order")


class UserSortParams(BaseModel):
    """Common sorting parameters for user queries."""

    sort_by: Literal[
        "user_id", "username", "date_joined", "last_login", "image_posts", "posts", "favorites"
    ] = Field(default="user_id", description="Sort field")
    sort_order: SortOrder = Field(default="DESC", description="Sort order")


class TagSortParams(BaseModel):
    """Common sorting parameters for tag queries."""

    sort_by: Literal["usage_count", "title", "date_added", "tag_id", "type"] = Field(
        default="usage_count", description="Sort field"
    )
    sort_order: SortOrder = Field(default="DESC", description="Sort order")
