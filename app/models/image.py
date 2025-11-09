"""
SQLModel-based Image models with inheritance for security

This module defines the Images database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

ImageBase (shared public fields)
    ├─> Images (database table, adds internal fields)
    └─> ImagePublic/ImageCreate/ImageUpdate (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.
"""
from datetime import datetime
from decimal import Decimal
from enum import Enum

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.user import Users


class ImageSortBy(str, Enum):
    """
    Allowed sort fields for image queries.

    These fields have been selected for performance (indexed columns) and
    usefulness in the API. Any route that allows sorting images should use
    this enum to validate the sort_by parameter.
    """
    image_id = "image_id"           # Primary sort, essentially same as date_added
    last_updated = "last_updated"   # Last modification date
    last_post = "last_post"         # Last post activity
    total_pixels = "total_pixels"   # Image size (width × height)
    bayesian_rating = "bayesian_rating" # Calculated rating
    favorites = "favorites"         # Popularity metric


class ImageBase(SQLModel):
    """
    Base model with shared public fields for Images.

    These fields are safe to expose via the API and are shared between:
    - The database table (Images)
    - API response schemas (ImagePublic)
    - API request schemas (ImageCreate, ImageUpdate)
    """
    # File information
    filename: str | None = Field(default=None, max_length=120)
    ext: str = Field(max_length=10)
    original_filename: str | None = Field(default=None, max_length=120)
    md5_hash: str = Field(default="", max_length=32)

    # Dimensions and file info
    filesize: int = Field(default=0)
    width: int = Field(default=0)
    height: int = Field(default=0)

    # Metadata
    caption: str = Field(default="", max_length=35)
    image_source: str | None = Field(default=None, max_length=255)
    artist: str | None = Field(default=None, max_length=200)
    characters: str | None = Field(default=None)

    # Rating
    rating: float = Field(default=0.0)


class Images(ImageBase, table=True):
    """
    Database table for images with internal fields.

    Extends ImageBase with:
    - Primary key and foreign keys
    - Internal tracking fields (IP, user agent, etc.)
    - Status and moderation fields
    - Computed/derived fields (bayesian rating, etc.)
    - Relationships to other tables

    Internal fields (should NOT be exposed via public API):
    - useragent, ip: Privacy-sensitive tracking
    - status_user_id, status_updated, last_updated, last_post: Internal moderation
    - medium, large, reviewed, change_id: Internal flags
    - total_pixels, miscmeta: Internal metadata
    - replacement_id: Internal reference
    """
    __tablename__ = 'images'

    # NOTE: __table_args__ is partially redundant with Field(foreign_key=...) declarations below.
    # However, it's kept for explicit CASCADE behavior and named constraints that SQLModel's
    # Field() cannot express. Be aware: if using Alembic migrations to manage schema changes,
    # these definitions may drift from the actual database structure over time. When in doubt,
    # treat Alembic migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(['replacement_id'], ['images.image_id'], ondelete='SET NULL', onupdate='CASCADE', name='fk_images_replacement_id'),
        ForeignKeyConstraint(['status_user_id'], ['users.user_id'], ondelete='SET NULL', onupdate='CASCADE', name='fk_images_status_user_id'),
        ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE', onupdate='CASCADE', name='fk_images_user_id'),
        Index('change_id', 'change_id'),
        Index('fk_images_replacement_id', 'replacement_id'),
        Index('fk_images_status_user_id', 'status_user_id'),
        Index('fk_images_user_id', 'user_id'),
        Index('idx_bayesian_rating', 'bayesian_rating'),
        Index('idx_favorites', 'favorites'),
        Index('idx_filename', 'filename'),
        Index('idx_last_post', 'last_post'),
        Index('idx_status', 'status'),
        Index('idx_top_images', 'num_ratings'),
        Index('idx_total_pixels', 'total_pixels'),
    )

    # Primary key
    image_id: int | None = Field(default=None, primary_key=True)

    # User reference (public)
    user_id: int = Field(foreign_key="users.user_id")

    # Public status/stats fields
    status: int = Field(default=1)
    locked: int = Field(default=0)
    posts: int = Field(default=0)
    favorites: int = Field(default=0)
    bayesian_rating: float = Field(default=0.0)
    num_ratings: int = Field(default=0)

    # Public timestamp
    date_added: datetime | None = Field(default=None, sa_column_kwargs={"server_default": text('current_timestamp()')})

    # Internal tracking fields (privacy-sensitive)
    useragent: str = Field(default="", max_length=255)
    ip: str = Field(default="", max_length=15)

    # Internal flags and metadata
    medium: int = Field(default=0)
    large: int = Field(default=0)
    reviewed: int = Field(default=0)
    change_id: int = Field(default=0)

    # Internal moderation fields
    status_user_id: int | None = Field(default=None, foreign_key="users.user_id")
    status_updated: datetime | None = Field(default=None)
    last_updated: datetime | None = Field(default=None)
    last_post: datetime | None = Field(default=None)

    # Internal metadata
    total_pixels: Decimal | None = Field(default=None)
    miscmeta: str | None = Field(default=None, max_length=255)
    replacement_id: int | None = Field(default=None, foreign_key="images.image_id")

    # Relationships - Example implementation
    # This demonstrates how to add relationships when needed. Key points:
    # - Use TYPE_CHECKING import to avoid circular imports
    # - Set sa_relationship_kwargs={"lazy": "joined"} for commonly-needed data
    # - Or use "selectin" for better performance with multiple objects
    # - The relationship will NOT auto-serialize in Pydantic schemas (SQLModel behavior)
    user: "Users" = Relationship(
        sa_relationship_kwargs={
            "lazy": "joined",  # Eagerly load user data with image
            "foreign_keys": "[Images.user_id]"
        }
    )
