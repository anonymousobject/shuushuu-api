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

    # Description change fields
    old_desc: str | None = None
    new_desc: str | None = None

    # Alias change fields
    old_alias_of: int | None = None
    new_alias_of: int | None = None
    alias_tag: LinkedTag | None = None  # Resolved tag for alias changes

    # Parent/inheritance change fields
    old_parent_id: int | None = None
    new_parent_id: int | None = None
    parent_tag: LinkedTag | None = None  # Resolved tag for parent changes

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

    # "Added" events for an image are derived from tag_links (which carry who/when
    # for every current tag, including those set at upload that were never written
    # to tag_history). Those synthesized events have no tag_history row, so the id
    # is nullable here (deliberately widening the base's non-null int).
    # TODO: make TagHistoryResponse.tag_history_id `int | None` at the base and
    # narrow it back in the callers that require it (tags + user-history endpoints),
    # so this override can drop the type-ignore.
    tag_history_id: int | None = None  # type: ignore[assignment]
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

    # Deactivation reason category (shown to everyone); free-text reason is
    # owner/mod-only for hidden-status transitions (gated by the endpoint).
    reason_category: int | None = None
    reason: str | None = None

    # Originating report/review for this transition — exposed to REPORT_VIEW
    # mods only (gated by the endpoint); NULL for everyone else.
    report_id: int | None = None
    review_id: int | None = None

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
    reason_category: int
    reason_category_label: str
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
    The exact fields present depend on the `type` field:

    - tag_metadata: action_type, tag, old_title/new_title (for rename),
      old_type/new_type (for type_change), old_desc/new_desc (for
      description_change), alias_tag/parent_tag/source_tag/character_tag
      (for the corresponding link/unlink actions), created_at
    - tag_usage: action, tag, image_id, date
    - status_change: image_id, old_status, new_status, new_status_label, created_at
    """

    type: Literal["tag_metadata", "tag_usage", "status_change"]

    # Common timestamp fields (different types use different fields)
    created_at: UTCDatetime | None = None  # For tag_metadata and status_change
    date: UTCDatetime | None = None  # For tag_usage (uses 'date' field)

    # Common fields
    image_id: int | None = None

    # Tag info object (for tag_metadata and tag_usage types)
    tag: LinkedTag | None = None

    # For tag_usage: "added" or "removed"
    action: str | None = None

    # For status changes
    old_status: int | None = None
    new_status: int | None = None
    new_status_label: str | None = None

    # For tag_metadata: rename action
    action_type: str | None = None
    old_title: str | None = None
    new_title: str | None = None

    # For tag_metadata: type_change action
    old_type: int | None = None
    new_type: int | None = None

    # For tag_metadata: description_change action
    old_desc: str | None = None
    new_desc: str | None = None

    # For tag_metadata: the *other* tag involved in the action. Populated per
    # action_type — alias_set/alias_removed → alias_tag, parent_set/parent_removed
    # → parent_tag, source_linked/source_unlinked → source_tag + character_tag.
    # Naming mirrors TagAuditLogResponse so the frontend can reuse its
    # getLinkedTag() helper.
    alias_tag: LinkedTag | None = None
    parent_tag: LinkedTag | None = None
    source_tag: LinkedTag | None = None
    character_tag: LinkedTag | None = None


class UserHistoryListResponse(BaseModel):
    """Paginated list of user history items."""

    total: int
    page: int
    per_page: int
    items: list[UserHistoryItem]
