"""
Pydantic schemas for User endpoints
"""

from pydantic import BaseModel, EmailStr, computed_field, field_validator

from app.config import settings
from app.models.user import UserBase
from app.schemas.base import UTCDatetime, UTCDatetimeOptional


class UserCreate(BaseModel):
    """Schema for creating a new user"""

    username: str
    email: EmailStr  # Required for user creation
    password: str

    # Honeypot field (should always be empty for legitimate users)
    # Optional with default to avoid breaking existing Users() instantiations
    # Field name looks legitimate to bots (not "honeypot" or "trap")
    website_url: str = ""

    # Cloudflare Turnstile token (required)
    turnstile_token: str


class UserUpdate(BaseModel):
    """Schema for updating a user profile - all fields optional

    Note: Avatar updates are handled via dedicated /users/{id}/avatar routes,
    not through this schema.
    """

    location: str | None = None
    website: str | None = None
    interests: str | None = None
    user_title: str | None = None
    gender: str | None = None
    email: EmailStr | None = None
    password: str | None = None
    email_pm_pref: int | None = None

    # User settings
    show_all_images: int | None = None
    spoiler_warning_pref: int | None = None

    # Display preferences
    thumb_layout: int | None = None  # 0=list view, 1=grid view
    sorting_pref: str | None = None  # ImageSortBy enum value
    sorting_pref_order: str | None = None  # ASC or DESC
    images_per_page: int | None = None  # 1-100

    # Navigation
    bookmark: int | None = None  # Bookmarked image_id

    # Admin-only fields (requires USER_EDIT_PROFILE permission)
    maximgperday: int | None = None  # Max images per day upload limit

    @field_validator("location", "website", "interests", "user_title", "gender")
    @classmethod
    def sanitize_text_fields(cls, v: str | None) -> str | None:
        """
        Sanitize free-form text fields.

        Just trims whitespace - HTML escaping is handled by Svelte's
        safe template interpolation on the frontend.
        """
        if v is None:
            return v
        return v.strip()

    @field_validator("gender")
    @classmethod
    def validate_gender_length(cls, v: str | None) -> str | None:
        """Validate gender is within max length (50 chars)."""
        if v is not None and len(v) > 50:
            raise ValueError("Gender must be 50 characters or less")
        return v

    @field_validator("email_pm_pref", "show_all_images", "spoiler_warning_pref", "thumb_layout")
    @classmethod
    def validate_boolean_prefs(cls, v: int | None) -> int | None:
        """Validate boolean preference fields are 0 or 1"""
        if v is not None and v not in [0, 1]:
            raise ValueError("Value must be 0 or 1")
        return v

    @field_validator("sorting_pref")
    @classmethod
    def validate_sorting_pref(cls, v: str | None) -> str | None:
        """Validate sorting_pref is a valid ImageSortBy value"""
        if v is None:
            return v
        from app.models.image import ImageSortBy

        valid_values = [e.value for e in ImageSortBy]
        if v not in valid_values:
            raise ValueError(f"sorting_pref must be one of: {', '.join(valid_values)}")
        return v

    @field_validator("sorting_pref_order")
    @classmethod
    def validate_sorting_pref_order(cls, v: str | None) -> str | None:
        """Validate and normalize sorting_pref_order to uppercase ASC or DESC"""
        if v is None:
            return v
        v_upper = v.upper()
        if v_upper not in ["ASC", "DESC"]:
            raise ValueError("sorting_pref_order must be 'ASC' or 'DESC'")
        return v_upper

    @field_validator("images_per_page")
    @classmethod
    def validate_images_per_page(cls, v: int | None) -> int | None:
        """Validate images_per_page is between 1 and 100"""
        if v is None:
            return v
        if v < 1 or v > 100:
            raise ValueError("images_per_page must be between 1 and 100")
        return v

    @field_validator("bookmark")
    @classmethod
    def validate_bookmark(cls, v: int | None) -> int | None:
        """Validate bookmark is a positive integer when set"""
        if v is not None and v < 1:
            raise ValueError("bookmark must be a positive integer")
        return v

    @field_validator("maximgperday")
    @classmethod
    def validate_maximgperday(cls, v: int | None) -> int | None:
        """Validate maximgperday is a positive integer when set"""
        if v is not None and v < 1:
            raise ValueError("maximgperday must be a positive integer")
        return v


class UserResponse(UserBase):
    """Schema for user response - what API returns"""

    user_id: int
    date_joined: UTCDatetime
    last_login: UTCDatetimeOptional = None
    last_active: UTCDatetimeOptional = None
    active: bool
    admin: bool
    groups: list[str] = []  # Group names for username coloring (e.g., ["mods", "admins"])
    maximgperday: int | None = None  # Upload limit - only visible to self or admins

    # Allow Pydantic to read from SQLAlchemy model attributes (not just dicts)
    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avatar_url(self) -> str | None:
        """Generate avatar URL from avatar field"""
        if self.avatar:
            return f"{settings.IMAGE_BASE_URL}/images/avatars/{self.avatar}"
        return None

    @field_validator("active", "admin", mode="before")
    @classmethod
    def convert_int_to_bool(cls, v: int | bool) -> bool:
        """Convert database int (0/1) to boolean"""
        if isinstance(v, bool):
            return v
        return bool(v)

    # NOTE: No normalization/escaping for interests, location, website, user_title.
    # These fields are stored as plain text (trimmed on input) and HTML escaping
    # is handled by Svelte's safe template interpolation on the frontend.
    # Legacy data: Run scripts/normalize_db_text.py to decode HTML entities.


class UserPrivateResponse(UserResponse):
    """
    Schema for authenticated user's own profile - includes private settings.

    This schema includes sensitive fields that should only be returned when
    the user is viewing their own profile (via /users/me endpoints).

    Note: When viewing other users' profiles, use UserResponse instead.
    """

    email: EmailStr  # User's own email (private)
    email_verified: bool  # Email verification status
    email_pm_pref: int  # PM email notification preference (0=disabled, 1=enabled)
    permissions: list[
        str
    ] = []  # List of permission strings (e.g., ["image_tag_add", "tag_create"])
    unread_pm_count: int = 0  # Number of unread private messages

    # Override maximgperday from UserResponse - always include for self
    maximgperday: int  # Max images allowed to upload per day

    # User settings
    show_all_images: int  # Show disabled/pending images (0=no, 1=yes)
    spoiler_warning_pref: int  # Show spoiler warnings (0=disabled, 1=enabled)

    # Display preferences
    thumb_layout: int  # 0=list view, 1=grid view
    sorting_pref: str  # ImageSortBy enum value (e.g., "image_id", "favorites")
    sorting_pref_order: str  # "ASC" or "DESC"
    images_per_page: int  # 1-100

    # Navigation
    bookmark: int | None  # Bookmarked image_id (for "continue where I left off")

    # Computed at request time
    uploads_remaining_today: int = 0  # How many more images the user can upload today

    @field_validator("email_verified", mode="before")
    @classmethod
    def convert_email_verified_to_bool(cls, v: int | bool) -> bool:
        """Convert database int (0/1) to boolean"""
        if isinstance(v, bool):
            return v
        return bool(v)


class UserCreateResponse(UserBase):
    """Schema for user creation response"""

    user_id: int
    username: str
    email: EmailStr


class UserListResponse(BaseModel):
    """Schema for paginated user list"""

    total: int
    page: int
    per_page: int
    users: list[UserResponse]


# ===== User Warnings Schemas =====


class UserWarningResponse(BaseModel):
    """Response schema for a warning/suspension shown to the user."""

    suspension_id: int
    action: str  # "warning" or "suspended"
    actioned_at: UTCDatetime
    suspended_until: UTCDatetimeOptional = None
    reason: str | None

    model_config = {"from_attributes": True}


class UserWarningsResponse(BaseModel):
    """Response schema for listing unacknowledged warnings/suspensions."""

    items: list[UserWarningResponse]
    count: int


class AcknowledgeWarningsResponse(BaseModel):
    """Response schema for acknowledging warnings."""

    acknowledged_count: int
    message: str
