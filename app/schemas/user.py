"""
Pydantic schemas for User endpoints
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from app.models.user import UserBase


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

    @field_validator('active', 'admin', mode='before')
    @classmethod
    def convert_int_to_bool(cls, v: int | bool) -> bool:
        """Convert database int (0/1) to boolean"""
        if isinstance(v, bool):
            return v
        return bool(v)


class UserListResponse(BaseModel):
    """Schema for paginated user list"""
    total: int
    page: int
    per_page: int
    users: list[UserResponse]
