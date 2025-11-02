"""
Pydantic schemas for User endpoints
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserBase(BaseModel):
    """Base schema for User - shared fields"""
    username: str
    location: str | None = None
    website: str | None = None
    avatar: str | None = None


class UserCreate(UserBase):
    """Schema for creating a new user"""
    email: EmailStr  # Required for user creation
    password: str


class UserUpdate(BaseModel):
    """Schema for updating a user profile - all fields optional"""
    username: str | None = None
    location: str | None = None
    website: str | None = None
    avatar: str | None = None
    email: EmailStr | None = None
    password: str | None = None


class UserResponse(UserBase):
    """Schema for user response - what API returns"""
    user_id: int
    date_joined: datetime | None = None
    active: bool
    admin: bool
    posts: int
    image_posts: int
    favorites: int

    model_config = ConfigDict(from_attributes=True)


class UserListResponse(BaseModel):
    """Schema for paginated user list"""
    total: int
    page: int
    per_page: int
    users: list[UserResponse]
