"""
SQLModel-based ImageRating models with inheritance for security

This module defines the ImageRatings database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

ImageRatingBase (shared public fields)
    ├─> ImageRatings (database table)
    └─> ImageRatingCreate/ImageRatingResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.

Note: ImageRatings is a junction table with composite primary key (user_id, image_id).
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class ImageRatingBase(SQLModel):
    """
    Base model with shared public fields for ImageRatings.

    These fields are safe to expose via the API and are shared between:
    - The database table (ImageRatings)
    - API response schemas (ImageRatingResponse)
    - API request schemas (ImageRatingCreate)
    """

    # Composite primary key
    user_id: int = Field(foreign_key="users.user_id", primary_key=True)
    image_id: int = Field(foreign_key="images.image_id", primary_key=True)

    # Rating value (0-10 or similar scale)
    rating: int = Field(default=0)


class ImageRatings(ImageRatingBase, table=True):
    """
    Database table for image ratings.

    Extends ImageRatingBase with:
    - Composite primary key (user_id, image_id)
    - Timestamp for when rating was given

    All fields are public as this is user-generated content.
    """

    __tablename__ = "image_ratings"  # type: ignore[assignment]

    # NOTE: __table_args__ is partially redundant with Field(foreign_key=...) declarations below.
    # However, it's kept for explicit CASCADE behavior and named constraints that SQLModel's
    # Field() cannot express. Be aware: if using Alembic migrations to manage schema changes,
    # these definitions may drift from the actual database structure over time. When in doubt,
    # treat Alembic migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_ratings_image_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_ratings_user_id",
        ),
        Index("fk_image_ratings_image_id", "image_id"),
    )

    # Public timestamp
    date: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
