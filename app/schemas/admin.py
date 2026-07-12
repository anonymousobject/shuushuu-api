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

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from app.schemas.base import UTCDatetime, UTCDatetimeOptional

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


# ===== User Suspension Schemas =====


class SuspendUserRequest(BaseModel):
    """Request schema for suspending or warning a user."""

    action: Literal["suspended", "warning"] = Field(
        "suspended",
        description="Action type: 'suspended' to suspend the account, 'warning' for verbal warning only",
    )
    suspended_until: AwareDatetime | None = Field(
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
    actioned_at: UTCDatetime
    suspended_until: UTCDatetimeOptional = None
    reason: str | None
    acknowledged_at: UTCDatetimeOptional = None

    model_config = {"from_attributes": True}


class SuspensionListResponse(BaseModel):
    """Response schema for listing user suspension history."""

    user_id: int
    username: str
    total: int
    suspensions: list[SuspensionResponse]


# ===== Image Status Schemas =====


class ImageStatusUpdate(BaseModel):
    """Request schema for changing image status and/or locked state."""

    status: int | None = Field(
        None,
        description="New status: 0=Deactivated, -4=Review, -1=Repost, 1=Active, 2=Spoiler",
    )
    replacement_id: int | None = Field(
        None,
        description="Original image ID when marking as repost (required when status=-1)",
    )
    reason_category: int | None = Field(
        None,
        description=(
            "Deactivation reason (required when status=0): "
            "1=Inappropriate, 2=Low Quality, 3=Spam, 4=Other"
        ),
    )
    reason: str | None = Field(
        None,
        max_length=1000,
        description=(
            "Free-text reason. Required when status=0 (deactivate). Also required "
            "when restoring a currently-hidden image to any visible status "
            "(active/spoiler/repost) — enforced server-side since the schema can't "
            "see the image's current status."
        ),
    )
    locked: bool | None = Field(
        None,
        description="Lock comments on the image (True=locked, False=unlocked)",
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: int | None) -> int | None:
        """Validate status is one of the settable ImageStatus constants.

        Legacy INAPPROPRIATE(-2)/LOW_QUALITY(-3) are no longer settable — they
        collapse into DEACTIVATED(0) + a reason_category.
        """
        if v is None:
            return v

        from app.config import ImageStatus

        # REVIEW is intentionally NOT settable here: a review must be created via
        # the review flow (POST /admin/images/{id}/review or /reports/{id}/escalate),
        # which writes the ImageReviews row. A direct PATCH to REVIEW would orphan the
        # image (hidden-as-REVIEW with no voting record, unresolvable).
        settable = {
            ImageStatus.DEACTIVATED,
            ImageStatus.REPOST,
            ImageStatus.ACTIVE,
            ImageStatus.SPOILER,
        }
        if v not in settable:
            raise ValueError(
                f"Invalid status: {v}. Must be one of: "
                "0=Deactivated, -1=Repost, 1=Active, 2=Spoiler"
            )
        return v

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return v.strip() or None

    @model_validator(mode="after")
    def validate_combination(self) -> ImageStatusUpdate:
        """Require status or locked; require category+reason for deactivation."""
        from app.config import DeactivationReason, ImageStatus

        if self.status is None and self.locked is None:
            raise ValueError("At least one of 'status' or 'locked' must be provided")

        if self.status == ImageStatus.DEACTIVATED:
            if self.reason_category not in DeactivationReason.VALID:
                raise ValueError("reason_category is required and must be valid when deactivating")
            if not self.reason:
                raise ValueError("reason is required when deactivating")
        else:
            if self.reason_category is not None:
                raise ValueError("reason_category is only valid when status is Deactivated")
            if self.status is None and self.reason is not None:
                raise ValueError("reason requires a status change")
        return self


class ImageStatusResponse(BaseModel):
    """Response schema for image status change."""

    image_id: int
    status: int
    locked: int
    replacement_id: int | None
    reason_category: int | None = None
    status_reason: str | None = None
    status_user_id: int | None
    status_updated: UTCDatetimeOptional = None

    model_config = {"from_attributes": True}


# ===== Admin Suspension List Schemas =====


class AdminSuspensionItem(BaseModel):
    """Schema for a suspension item in the admin list."""

    suspension_id: int
    user_id: int
    username: str
    action: str
    is_active: bool
    actioned_at: UTCDatetime
    actioned_by_id: int | None
    actioned_by_username: str | None
    reason: str | None
    suspended_until: UTCDatetimeOptional = None


class AdminSuspensionListResponse(BaseModel):
    """Response schema for listing all suspensions across users."""

    items: list[AdminSuspensionItem]
    total: int
    page: int
    per_page: int
    active_count: int
