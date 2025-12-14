"""
Pydantic schemas for User endpoints
"""

from datetime import datetime
from html import unescape

from pydantic import BaseModel, EmailStr, computed_field, field_validator

from app.config import settings
from app.models.user import UserBase


class UserCreate(BaseModel):
    """Schema for creating a new user"""

    username: str
    email: EmailStr  # Required for user creation
    password: str


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

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v: str | None) -> str | None:
        """Validate gender is one of the allowed values"""
        if v is not None and v not in ["", "M", "F", "O"]:
            raise ValueError("Gender must be 'M', 'F', 'O', or empty")
        return v


class UserResponse(UserBase):
    """Schema for user response - what API returns"""

    user_id: int
    date_joined: datetime | None = None
    active: bool
    admin: bool
    posts: int  # Comments posted by user
    favorites: int  # Images favorited by user
    image_posts: int  # Images uploaded by user

    # Allow Pydantic to read from SQLAlchemy model attributes (not just dicts)
    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avatar_url(self) -> str | None:
        """Generate avatar URL from avatar field"""
        if self.avatar:
            return f"{settings.IMAGE_BASE_URL}/storage/avatars/{self.avatar}"
        return None

    @field_validator("active", "admin", mode="before")
    @classmethod
    def convert_int_to_bool(cls, v: int | bool) -> bool:
        """Convert database int (0/1) to boolean"""
        if isinstance(v, bool):
            return v
        return bool(v)

    @field_validator("interests", "location", "website", "user_title", mode="before")
    @classmethod
    def decode_html_entities(cls, v: str | None) -> str | None:
        """Decode HTML entities from legacy PHP data"""
        if v:
            return unescape(v)
        return v


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
