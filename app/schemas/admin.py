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

from pydantic import BaseModel, Field

# ===== Group Schemas =====


class GroupCreate(BaseModel):
    """Schema for creating a new group."""

    title: str = Field(..., max_length=50, description="Group name")
    desc: str | None = Field(None, max_length=75, description="Group description")


class GroupUpdate(BaseModel):
    """Schema for updating a group."""

    title: str | None = Field(None, max_length=50, description="Group name")
    desc: str | None = Field(None, max_length=75, description="Group description")


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
    """Request schema for suspending a user."""

    suspended_until: datetime | None = Field(
        None,
        description="When the suspension expires (None = indefinite/permanent suspension). Provide datetime in UTC.",
    )
    reason: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Reason shown to the user (minimum 3 characters)",
    )


class SuspensionResponse(BaseModel):
    """Response schema for a suspension record."""

    suspension_id: int
    user_id: int
    action: str
    actioned_by: int | None
    actioned_at: datetime
    suspended_until: datetime | None
    reason: str | None

    model_config = {"from_attributes": True}


class SuspensionListResponse(BaseModel):
    """Response schema for listing user suspension history."""

    user_id: int
    username: str
    total: int
    suspensions: list[SuspensionResponse]
