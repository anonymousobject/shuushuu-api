"""
SQLModel-based ReviewVote models with inheritance for security

This module defines the ReviewVotes database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

ReviewVoteBase (shared public fields)
    ├─> ReviewVotes (database table)
    └─> ReviewVoteCreate/ReviewVoteResponse (API schemas, defined in app/schemas)

Note: This table was originally named `image_reviews` and stored simple approval votes.
It has been renamed to `review_votes` to better reflect its purpose as individual votes
on review sessions. The actual review sessions are now in the `image_reviews` table.
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKey, Index, Integer, text
from sqlmodel import Field, SQLModel


class ReviewVoteBase(SQLModel):
    """
    Base model with shared public fields for ReviewVotes.

    These fields are safe to expose via the API and are shared between:
    - The database table (ReviewVotes)
    - API response schemas (ReviewVoteResponse)
    - API request schemas (ReviewVoteCreate)
    """

    # Vote: 1=keep/approve, 0=remove/reject
    vote: int | None = Field(default=None)

    # Optional reasoning for the vote
    comment: str | None = Field(default=None)


class ReviewVotes(ReviewVoteBase, table=True):
    """
    Database table for review votes.

    Extends ReviewVoteBase with:
    - Primary key
    - Foreign key relationships
    - Timestamp

    This table stores individual admin votes on review sessions. Legacy votes
    (before the review session system) may have review_id=NULL and only image_id set.

    Constraints:
    - Unique on (review_id, user_id) WHERE review_id IS NOT NULL - for new votes
    - Unique on (image_id, user_id) - for legacy votes (existing constraint)
    """

    __tablename__ = "review_votes"

    __table_args__ = (
        # Note: ForeignKeyConstraints are defined directly on columns using sa_column
        # with ForeignKey() to ensure CASCADE behavior is properly applied when
        # tables are created via SQLModel.metadata.create_all()
        Index("fk_review_votes_user_id", "user_id"),
        Index("fk_review_votes_review_id", "review_id"),
        # Legacy unique constraint on (image_id, user_id)
        Index("idx_review_votes_image_user", "image_id", "user_id", unique=True),
        # Note: Partial unique index on (review_id, user_id) WHERE review_id IS NOT NULL
        # must be created in migration as SQLModel doesn't support partial indexes
    )

    # Primary key
    vote_id: int | None = Field(default=None, primary_key=True)

    # References - all with CASCADE delete behavior
    image_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("images.image_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=True,
        ),
    )
    user_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("users.user_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=True,
        ),
    )
    review_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("image_reviews.review_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=True,
        ),
    )

    # Timestamp
    created_at: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
