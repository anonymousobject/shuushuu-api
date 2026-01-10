"""
Pydantic schemas for admin API endpoints.

These schemas handle:
- Group management (CRUD)
- Group membership
- Group permissions
- Direct user permissions
- Permission listing
- User suspensions
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ===== Group Schemas =====


class GroupCreate(BaseModel):
    """Schema for creating a new group."""

    title: str = Field(..., max_length=50, description="Group name")
    desc: str | None = Field(None, max_length=75, description="Group description")

    @field_validator("title", "desc")
    @classmethod
    def sanitize_text_fields(cls, v: str | None) -> str | None:
        """
        Sanitize group text fields.

        Just trims whitespace - HTML escaping is handled by Svelte's
        safe template interpolation on the frontend.
        """
        if v is None:
            return v
        return v.strip()


class GroupUpdate(BaseModel):
    """Schema for updating a group."""

    title: str | None = Field(None, max_length=50, description="Group name")
    desc: str | None = Field(None, max_length=75, description="Group description")

    @field_validator("title", "desc")
    @classmethod
    def sanitize_text_fields(cls, v: str | None) -> str | None:
        """
        Sanitize group text fields.

        Just trims whitespace - HTML escaping is handled by Svelte's
        safe template interpolation on the frontend.
        """
        if v is None:
            return v
        return v.strip()


class GroupResponse(BaseModel):
    """Response schema for a group."""

    group_id: int
    title: str | None
    desc: str | None

    model_config = {"from_attributes": True}


class GroupListResponse(BaseModel):
    """Response schema for listing groups."""

    total: int
    groups: list[GroupResponse]


# ===== Permission Schemas =====


class PermResponse(BaseModel):
    """Response schema for a permission."""

    perm_id: int
    title: str | None
    desc: str | None

    model_config = {"from_attributes": True}


class PermListResponse(BaseModel):
    """Response schema for listing permissions."""

    total: int
    permissions: list[PermResponse]


# ===== Group Membership Schemas =====


class GroupMemberItem(BaseModel):
    """Schema for a single group member."""

    user_id: int
    username: str


class GroupMembersResponse(BaseModel):
    """Response schema for listing group members."""

    group_id: int
    group_title: str | None
    total: int
    members: list[GroupMemberItem]


# ===== Group Permission Schemas =====


class GroupPermItem(BaseModel):
    """Schema for a permission assigned to a group."""

    perm_id: int
    title: str | None
    desc: str | None
    permvalue: int | None


class GroupPermsResponse(BaseModel):
    """Response schema for listing group permissions."""

    group_id: int
    group_title: str | None
    total: int
    permissions: list[GroupPermItem]


# ===== User Permission Schemas =====


class UserPermItem(BaseModel):
    """Schema for a permission assigned to a user."""

    perm_id: int
    title: str | None
    desc: str | None
    permvalue: int


class UserPermsResponse(BaseModel):
    """Response schema for listing user permissions."""

    user_id: int
    username: str
    total: int
    permissions: list[UserPermItem]


# ===== User Group Schemas =====


class UserGroupItem(BaseModel):
    """Schema for a group a user belongs to."""

    group_id: int
    title: str | None
    desc: str | None


class UserGroupsResponse(BaseModel):
    """Response schema for listing user's groups."""

    user_id: int
    username: str
    total: int
    groups: list[UserGroupItem]


# ===== Simple Message Response =====


class MessageResponse(BaseModel):
    """Simple message response for success operations."""

    message: str


# ===== User Suspension Schemas =====


class SuspendUserRequest(BaseModel):
    """Request schema for suspending or warning a user."""

    action: Literal["suspended", "warning"] = Field(
        "suspended",
        description="Action type: 'suspended' to suspend the account, 'warning' for verbal warning only",
    )
    suspended_until: datetime | None = Field(
        None,
        description="When the suspension expires (None = permanent). Ignored for warnings.",
    )
    reason: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Reason shown to the user (minimum 3 characters)",
    )

    @field_validator("reason")
    @classmethod
    def sanitize_reason(cls, v: str) -> str:
        """
        Sanitize reason text.

        Just trims whitespace - HTML escaping is handled by Svelte's
        safe template interpolation on the frontend.
        """
        return v.strip()


class SuspensionResponse(BaseModel):
    """Response schema for a suspension record."""

    suspension_id: int
    user_id: int
    action: str
    actioned_by: int | None
    actioned_at: datetime
    suspended_until: datetime | None
    reason: str | None
    acknowledged_at: datetime | None

    model_config = {"from_attributes": True}


class SuspensionListResponse(BaseModel):
    """Response schema for listing user suspension history."""

    user_id: int
    username: str
    total: int
    suspensions: list[SuspensionResponse]


# ===== Image Status Schemas =====


class ImageStatusUpdate(BaseModel):
    """Request schema for changing image status directly."""

    status: int = Field(
        ...,
        description="New status: -4=Review, -2=Inappropriate, -1=Repost, 0=Other, 1=Active, 2=Spoiler",
    )
    replacement_id: int | None = Field(
        None,
        description="Original image ID when marking as repost (required when status=-1)",
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: int) -> int:
        """Validate status is one of the allowed ImageStatus constants."""
        from app.config import ImageStatus

        valid_statuses = {
            ImageStatus.REVIEW,
            ImageStatus.INAPPROPRIATE,
            ImageStatus.REPOST,
            ImageStatus.OTHER,
            ImageStatus.ACTIVE,
            ImageStatus.SPOILER,
        }
        if v not in valid_statuses:
            raise ValueError(
                f"Invalid status: {v}. Must be one of: "
                "-4=Review, -2=Inappropriate, -1=Repost, 0=Other, 1=Active, 2=Spoiler"
            )
        return v


class ImageStatusResponse(BaseModel):
    """Response schema for image status change."""

    image_id: int
    status: int
    replacement_id: int | None
    status_user_id: int | None
    status_updated: datetime | None

    model_config = {"from_attributes": True}
