"""
Pydantic schemas for API responses and requests
"""
from app.schemas.image import (
    ImageBase,
    ImageCreate,
    ImageListResponse,
    ImageResponse,
    ImageSearchParams,
    ImageUpdate,
)
from app.schemas.tag import (
    TagBase,
    TagCreate,
    TagListResponse,
    TagResponse,
    TagUpdate,
    TagWithStats,
)
from app.schemas.user import (
    UserBase,
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)

__all__ = [
    # Image schemas
    "ImageBase",
    "ImageCreate",
    "ImageUpdate",
    "ImageResponse",
    "ImageListResponse",
    "ImageSearchParams",
    # Tag schemas
    "TagBase",
    "TagCreate",
    "TagUpdate",
    "TagResponse",
    "TagWithStats",
    "TagListResponse",
    # User schemas
    "UserBase",
    "UserCreate",
    "UserUpdate",
    "UserResponse",
    "UserListResponse",
]
