"""
Pydantic schemas for Tag endpoints
"""

from datetime import datetime

from pydantic import BaseModel, computed_field, field_validator, model_validator

from app.config import settings
from app.models.tag import TagBase


class TagCreate(TagBase):
    """Schema for creating a new tag"""

    inheritedfrom_id: int | None = None
    alias_of: int | None = None
    desc: str | None = None

    @field_validator("title", "desc")
    @classmethod
    def sanitize_fields(cls, v: str | None) -> str | None:
        """
        Sanitize title and description.

        Just trims whitespace - HTML escaping is handled by Svelte's
        safe template interpolation on the frontend.
        """
        if v is None:
            return v
        return v.strip()


class TagUpdate(BaseModel):
    """Schema for updating a tag - all fields optional"""

    title: str | None = None
    type: int | None = None

    @field_validator("title")
    @classmethod
    def sanitize_title(cls, v: str | None) -> str | None:
        """Trim whitespace from title."""
        if v is None:
            return v
        return v.strip()


class TagCreator(BaseModel):
    """Schema for tag creator user info"""

    user_id: int
    username: str
    avatar: str | None = None

    # Allow reading from SQLAlchemy model attributes (not just dicts)
    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avatar_url(self) -> str | None:
        if self.avatar:
            return f"{settings.IMAGE_BASE_URL}/storage/avatars/{self.avatar}"
        return None


class TagResponse(TagBase):
    """Schema for tag response - what API returns"""

    tag_id: int
    alias_of: int | None = None
    is_alias: bool = False

    # NOTE: No normalization/escaping for title and desc.
    # These fields are stored as plain text (trimmed on input) and HTML escaping
    # is handled by Svelte's safe template interpolation on the frontend.
    # Legacy data: Run scripts/normalize_db_text.py to decode HTML entities.

    @model_validator(mode="after")
    def set_is_alias(self) -> "TagResponse":
        if self.alias_of is not None:
            self.is_alias = True
        return self


class TagWithStats(TagResponse):
    """Schema for tag response with usage statistics"""

    image_count: int
    is_alias: bool = False
    aliased_tag_id: int | None = None  # The actual tag this aliases (if is_alias=True)
    parent_tag_id: int | None = None  # The parent tag in hierarchy (inheritedfrom_id)
    child_count: int = 0  # Number of child tags that inherit from this tag
    created_by: TagCreator | None = None  # User who created the tag
    date_added: datetime  # When the tag was created


class TagListResponse(BaseModel):
    """Schema for paginated tag list"""

    total: int
    page: int
    per_page: int
    tags: list[TagResponse]
    invalid_ids: list[str] | None = None  # IDs that were invalid and filtered out
