"""
Pydantic schemas for Tag endpoints
"""

from pydantic import BaseModel

from app.models.tag import TagBase


class TagCreate(TagBase):
    """Schema for creating a new tag"""

    inheritedfrom_id: int | None = None
    alias: int | None = None
    desc: str | None = None


class TagUpdate(BaseModel):
    """Schema for updating a tag - all fields optional"""

    title: str | None = None
    type: int | None = None


class TagResponse(TagBase):
    """Schema for tag response - what API returns"""

    tag_id: int


class TagWithStats(TagResponse):
    """Schema for tag response with usage statistics"""

    image_count: int
    is_alias: bool = False
    aliased_tag_id: int | None = None  # The actual tag this aliases (if is_alias=True)
    parent_tag_id: int | None = None  # The parent tag in hierarchy (inheritedfrom_id)
    child_count: int = 0  # Number of child tags that inherit from this tag


class TagListResponse(BaseModel):
    """Schema for paginated tag list"""

    total: int
    page: int
    per_page: int
    tags: list[TagResponse]
    invalid_ids: list[str] | None = None  # IDs that were invalid and filtered out
