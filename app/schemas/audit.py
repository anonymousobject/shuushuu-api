"""
Pydantic schemas for audit trail endpoints.

Provides response schemas for:
- Tag audit log (tag metadata changes: rename, type, alias, parent, source links)
- Tag history (tag add/remove on images)
- Image status history (status changes)
- Image reviews (public review outcomes)
- User history (combined activity feed)
"""

from typing import Literal

from pydantic import BaseModel

from app.schemas.base import UTCDatetime
from app.schemas.common import UserSummary
from app.schemas.tag import LinkedTag

# =============================================================================
# Tag Audit Log (tag metadata changes)
# =============================================================================


class TagAuditLogResponse(BaseModel):
    """
    Response schema for a tag audit log entry.

    Tracks metadata changes to tags including renames, type changes,
    alias changes, parent changes, and character-source link changes.
    """

    id: int
    tag_id: int
    action_type: str

    # Rename fields
    old_title: str | None = None
    new_title: str | None = None

    # Type change fields
    old_type: int | None = None
    new_type: int | None = None

    # Alias change fields
    old_alias_of: int | None = None
    new_alias_of: int | None = None

    # Parent/inheritance change fields
    old_parent_id: int | None = None
    new_parent_id: int | None = None

    # Character-source link fields (with resolved tag info)
    character_tag: LinkedTag | None = None
    source_tag: LinkedTag | None = None

    # Who made the change
    user: UserSummary | None = None

    # When
    created_at: UTCDatetime

    model_config = {"from_attributes": True}


class TagAuditLogListResponse(BaseModel):
    """Paginated list of tag audit log entries."""

    total: int
    page: int
    per_page: int
    items: list[TagAuditLogResponse]


# =============================================================================
# Tag History (tag add/remove on images)
# =============================================================================


class TagHistoryResponse(BaseModel):
    """
    Response schema for a tag history entry.

    Tracks tag additions and removals on images.
    """

    tag_history_id: int
    image_id: int | None = None
    tag_id: int | None = None

    # Action: 'a' for add, 'r' for remove
    action: str | None = None

    # Who made the change
    user: UserSummary | None = None

    # When
    date: UTCDatetime

    model_config = {"from_attributes": True}


class TagHistoryListResponse(BaseModel):
    """Paginated list of tag history entries."""

    total: int
    page: int
    per_page: int
    items: list[TagHistoryResponse]


class ImageTagHistoryResponse(TagHistoryResponse):
    """
    Tag history entry with tag info included.

    Used when viewing an image's tag history where tag details are needed.
    """

    tag: LinkedTag | None = None


class ImageTagHistoryListResponse(BaseModel):
    """Paginated list of image tag history entries."""

    total: int
    page: int
    per_page: int
    items: list[ImageTagHistoryResponse]


# =============================================================================
# Image Status History
# =============================================================================


class ImageStatusHistoryResponse(BaseModel):
    """
    Response schema for an image status history entry.

    Tracks status changes on images. User may be hidden for certain
    status transitions (review, low_quality, inappropriate, other).
    """

    id: int
    image_id: int
    old_status: int
    old_status_label: str
    new_status: int
    new_status_label: str

    # Who made the change (may be null for hidden statuses)
    user: UserSummary | None = None

    # When
    created_at: UTCDatetime

    model_config = {"from_attributes": True}


class ImageStatusHistoryListResponse(BaseModel):
    """Paginated list of image status history entries."""

    total: int
    page: int
    per_page: int
    items: list[ImageStatusHistoryResponse]


# =============================================================================
# Image Reviews (public outcomes)
# =============================================================================


class ImageReviewPublicResponse(BaseModel):
    """Schema for public review outcome (hides votes and initiator)."""

    review_id: int
    review_type: int
    review_type_label: str
    outcome: int
    outcome_label: str
    created_at: UTCDatetime
    closed_at: UTCDatetime | None = None

    model_config = {"from_attributes": True}


class ImageReviewListResponse(BaseModel):
    """Paginated list of image reviews."""

    total: int
    page: int
    per_page: int
    items: list[ImageReviewPublicResponse]


# =============================================================================
# User History (combined activity feed)
# =============================================================================


class UserHistoryItem(BaseModel):
    """
    Polymorphic history item for user activity feed.

    Combines different history types into a unified format.
    """

    type: Literal["tag_metadata", "tag_usage", "status_change"]

    # Common timestamp fields (different types use different fields)
    created_at: UTCDatetime | None = None  # For tag_metadata and status_change
    date: UTCDatetime | None = None  # For tag_usage (uses 'date' field)

    # Common fields
    image_id: int | None = None
    tag_id: int | None = None

    # For tag add/remove
    tag_title: str | None = None

    # For status changes
    old_status: int | None = None
    new_status: int | None = None
    old_status_label: str | None = None
    new_status_label: str | None = None

    # For tag metadata changes
    action_type: str | None = None
    old_value: str | None = None
    new_value: str | None = None


class UserHistoryListResponse(BaseModel):
    """Paginated list of user history items."""

    total: int
    page: int
    per_page: int
    items: list[UserHistoryItem]
