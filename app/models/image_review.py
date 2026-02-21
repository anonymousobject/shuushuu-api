"""
SQLModel-based ImageReview models with inheritance for security

This module defines the ImageReviews database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

ImageReviewBase (shared public fields)
    ├─> ImageReviews (database table)
    └─> ImageReviewCreate/ImageReviewResponse (API schemas, defined in app/schemas)

Note: This is a NEW table for review sessions. The original `image_reviews` table
(which stored individual votes) has been renamed to `review_votes`.

ImageReviews represent voting sessions for appropriateness decisions, with:
- A deadline for voting
- Extension capability (one extension allowed)
- Status tracking (open/closed)
- Outcome recording (pending/keep/remove)
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKey, Index, Integer, text
from sqlmodel import Field, SQLModel

from app.config import ReviewOutcome, ReviewStatus, ReviewType


class ImageReviewBase(SQLModel):
    """
    Base model with shared public fields for ImageReviews.

    These fields are safe to expose via the API and are shared between:
    - The database table (ImageReviews)
    - API response schemas (ImageReviewResponse)
    - API request schemas (ImageReviewCreate)
    """

    # Review type (extensible for future types)
    review_type: int = Field(default=ReviewType.APPROPRIATENESS)

    # Status: 0=open, 1=closed
    status: int = Field(default=ReviewStatus.OPEN)

    # Outcome: 0=pending, 1=keep, 2=remove
    outcome: int = Field(default=ReviewOutcome.PENDING)

    # Whether the deadline has been extended
    extension_used: int = Field(default=0)

    # Admin who closed the review early (NULL = automatic/deadline close)
    closed_by: int | None = Field(default=None)


class ImageReviews(ImageReviewBase, table=True):
    """
    Database table for image review sessions.

    Extends ImageReviewBase with:
    - Primary key
    - Foreign key relationships
    - Timestamps and deadline

    This table represents voting sessions where admins vote on whether to
    keep or remove an image. Individual votes are stored in `review_votes`.

    Constraints:
    - Only one open review per image at a time (enforced at application level)
    """

    __tablename__ = "image_reviews"

    __table_args__ = (
        # Note: ForeignKeyConstraints are defined directly on columns using sa_column
        # with ForeignKey() to ensure CASCADE behavior is properly applied when
        # tables are created via SQLModel.metadata.create_all()
        Index("fk_image_reviews_image_id", "image_id"),
        Index("fk_image_reviews_source_report_id", "source_report_id"),
        Index("fk_image_reviews_initiated_by", "initiated_by"),
        Index("fk_image_reviews_closed_by", "closed_by"),
        Index("idx_image_reviews_status", "status"),
        Index("idx_image_reviews_deadline", "deadline"),
    )

    # Primary key
    review_id: int | None = Field(default=None, primary_key=True)

    # Image under review - CASCADE delete when image is deleted
    image_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("images.image_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )

    # Report that triggered this review (null if initiated directly)
    source_report_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("image_reports.report_id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
    )

    # Admin who started the review
    initiated_by: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("users.user_id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
    )

    # Admin who closed the review early (overrides base with FK)
    closed_by: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("users.user_id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
    )

    # Voting deadline
    deadline: datetime | None = Field(default=None)

    # Timestamps
    created_at: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )
    closed_at: datetime | None = Field(default=None)

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
