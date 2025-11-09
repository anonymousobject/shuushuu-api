"""
SQLModel-based ImageReview models with inheritance for security

This module defines the ImageReviews database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

ImageReviewBase (shared public fields)
    ├─> ImageReviews (database table)
    └─> ImageReviewCreate/ImageReviewResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.

Note: ImageReviews track moderator approval/rejection votes for pending images.
"""
from sqlalchemy import ForeignKeyConstraint, Index
from sqlmodel import Field, SQLModel


class ImageReviewBase(SQLModel):
    """
    Base model with shared public fields for ImageReviews.

    These fields are safe to expose via the API and are shared between:
    - The database table (ImageReviews)
    - API response schemas (ImageReviewResponse)
    - API request schemas (ImageReviewCreate)
    """
    # References
    image_id: int | None = Field(default=None)
    user_id: int | None = Field(default=None)

    # Vote: 1 for approve, 0 for reject, or similar convention
    vote: int | None = Field(default=None)


class ImageReviews(ImageReviewBase, table=True):
    """
    Database table for image reviews.

    Extends ImageReviewBase with:
    - Primary key
    - Foreign key relationships
    - Unique constraint on (image_id, user_id) to prevent duplicate votes
    """
    __tablename__ = 'image_reviews'

    # NOTE: __table_args__ is partially redundant with Field(foreign_key=...) declarations below.
    # However, it's kept for explicit CASCADE behavior and named constraints that SQLModel's
    # Field() cannot express. Be aware: if using Alembic migrations to manage schema changes,
    # these definitions may drift from the actual database structure over time. When in doubt,
    # treat Alembic migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(['image_id'], ['images.image_id'], ondelete='CASCADE', onupdate='CASCADE', name='fk_image_reviews_image_id'),
        ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE', onupdate='CASCADE', name='fk_image_reviews_user_id'),
        Index('fk_image_reviews_user_id', 'user_id'),
        Index('image_id', 'image_id', 'user_id', unique=True)
    )

    # Primary key
    image_review_id: int | None = Field(default=None, primary_key=True)

    # Override to add foreign keys
    image_id: int | None = Field(default=None, foreign_key="images.image_id")
    user_id: int | None = Field(default=None, foreign_key="users.user_id")

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
