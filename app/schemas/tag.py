"""
Pydantic schemas for Tag endpoints
"""

from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator

from app.models.tag import TagBase
from app.schemas.common import UserSummary


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


class TagResponse(TagBase):
    """Schema for tag response - what API returns"""

    tag_id: int
    alias_of: int | None = None
    alias_of_name: str | None = None
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


class LinkedTag(BaseModel):
    """Minimal tag info for linked sources/characters"""

    tag_id: int
    title: str | None


class TagWithStats(TagResponse):
    """Schema for tag response with usage statistics"""

    image_count: int
    is_alias: bool = False
    aliased_tag_id: int | None = None  # The actual tag this aliases (if is_alias=True)
    parent_tag_id: int | None = None  # The parent tag in hierarchy (inheritedfrom_id)
    child_count: int = 0  # Number of child tags that inherit from this tag
    created_by: UserSummary | None = None  # User who created the tag
    date_added: datetime  # When the tag was created
    links: list[str] = []  # External URLs associated with this tag
    # Character-source links
    sources: list[LinkedTag] = []  # For character tags: linked sources
    characters: list[LinkedTag] = []  # For source tags: linked characters


class TagListResponse(BaseModel):
    """Schema for paginated tag list"""

    total: int
    page: int
    per_page: int
    tags: list[TagResponse]
    invalid_ids: list[str] | None = None  # IDs that were invalid and filtered out


class TagExternalLinkCreate(BaseModel):
    """Schema for adding a new external link to a tag"""

    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL has http/https protocol and trim whitespace."""
        v = v.strip()
        if not v:
            raise ValueError("URL cannot be empty")
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if len(v) > 2000:
            raise ValueError("URL exceeds maximum length of 2000 characters")
        return v


class TagExternalLinkResponse(BaseModel):
    """Schema for tag external link response"""

    link_id: int
    url: str
    date_added: datetime

    model_config = {"from_attributes": True}


class CharacterSourceLinkCreate(BaseModel):
    """Schema for creating a character-source link"""

    character_tag_id: int
    source_tag_id: int


class CharacterSourceLinkResponse(BaseModel):
    """Schema for character-source link response"""

    id: int
    character_tag_id: int
    source_tag_id: int
    created_at: datetime
    created_by_user_id: int | None = None

    model_config = {"from_attributes": True}


class CharacterSourceLinkListResponse(BaseModel):
    """Schema for paginated character-source link list"""

    total: int
    page: int
    per_page: int
    links: list[CharacterSourceLinkResponse]


class CharacterSourceLinkWithTitles(CharacterSourceLinkResponse):
    """Link response with tag titles included"""

    character_title: str | None = None
    source_title: str | None = None
