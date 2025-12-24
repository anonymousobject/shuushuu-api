"""
Pydantic schemas for User endpoints
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, computed_field, field_validator

from app.config import settings
from app.models.user import UserBase


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

    @field_validator("location", "website", "interests", "user_title")
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
    def validate_gender(cls, v: str | None) -> str | None:
        """Validate gender is one of the allowed values"""
        if v is not None and v not in ["", "M", "F", "O"]:
            raise ValueError("Gender must be 'M', 'F', 'O', or empty")
        return v

    @field_validator("email_pm_pref")
    @classmethod
    def validate_email_pm_pref(cls, v: int | None) -> int | None:
        """Validate email_pm_pref is 0 or 1"""
        if v is not None and v not in [0, 1]:
            raise ValueError("email_pm_pref must be 0 or 1")
        return v


class UserResponse(UserBase):
    """Schema for user response - what API returns"""

    user_id: int
    date_joined: datetime | None = None
    last_login: datetime | None = None
    active: bool
    admin: bool

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
