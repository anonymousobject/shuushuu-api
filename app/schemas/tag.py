"""
Pydantic schemas for Tag endpoints
"""

from datetime import datetime

from pydantic import BaseModel, model_validator

from app.models.tag import TagBase


class TagCreate(TagBase):
    """Schema for creating a new tag"""

    inheritedfrom_id: int | None = None
    alias_of: int | None = None
    desc: str | None = None


class TagUpdate(BaseModel):
    """Schema for updating a tag - all fields optional"""

    title: str | None = None
    type: int | None = None


class TagCreator(BaseModel):
    """Schema for tag creator user info"""

    user_id: int
    username: str


class TagResponse(TagBase):
    """Schema for tag response - what API returns"""

    tag_id: int
    alias_of: int | None = None
    is_alias: bool = False

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
