"""
Pydantic schemas for API responses and requests
"""

from app.models.comment import CommentBase  # Re-export from models
from app.models.image import ImageBase  # Re-export from models
from app.models.tag import TagBase  # Re-export from models
from app.models.user import UserBase  # Re-export from models
from app.schemas.banner import BannerListResponse, BannerResponse
from app.schemas.comment import (
    CommentCreate,
    CommentListResponse,
    CommentResponse,
    CommentSearchParams,
    CommentStatsResponse,
    CommentUpdate,
)
from app.schemas.common import UserSummary
from app.schemas.image import (
    ImageCreate,
    ImageHashSearchResponse,
    ImageListResponse,
    ImageResponse,
    ImageSearchParams,
    ImageStatsResponse,
    ImageTagItem,
    ImageTagsResponse,
    ImageUpdate,
)
from app.schemas.privmsg import (
    PrivmsgCreate,
    PrivmsgMessage,
    PrivmsgMessages,
)
from app.schemas.tag import (
    TagCreate,
    TagListResponse,
    TagResponse,
    TagUpdate,
    TagWithStats,
)
from app.schemas.user import (
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)

__all__ = [
    # Common schemas
    "UserSummary",
    # Image schemas
    "ImageBase",
    "ImageCreate",
    "ImageUpdate",
    "ImageResponse",
    "ImageListResponse",
    "ImageSearchParams",
    "ImageTagItem",
    "ImageTagsResponse",
    "ImageHashSearchResponse",
    "ImageStatsResponse",
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
    # Comment schemas
    "CommentBase",
    "CommentCreate",
    "CommentUpdate",
    "CommentResponse",
    "CommentListResponse",
    "CommentSearchParams",
    "CommentStatsResponse",
    # Privmsg schemas
    "PrivmsgCreate",
    "PrivmsgMessage",
    "PrivmsgMessages",
    # Banner schemas
    "BannerResponse",
    "BannerListResponse",
    "BannerListResponse",
]
