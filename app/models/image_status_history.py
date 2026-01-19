"""
SQLModel-based ImageStatusHistory model for tracking image status changes.

This is a public audit table (separate from AdminActions) that tracks
all image status changes for public visibility.

Visibility rules:
- User shown for: REPOST (-1), SPOILER (2), ACTIVE (1)
- User hidden for: REVIEW (-4), LOW_QUALITY (-3), INAPPROPRIATE (-2), OTHER (0)
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class ImageStatusHistoryBase(SQLModel):
    """
    Base model with shared fields for ImageStatusHistory.
    """

    image_id: int
    old_status: int
    new_status: int


class ImageStatusHistory(ImageStatusHistoryBase, table=True):
    """
    Database table for image status history.

    Tracks all status changes for public audit trail.
    """

    __tablename__ = "image_status_history"

    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_status_history_image_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_image_status_history_user_id",
        ),
        Index("idx_image_status_history_image_id", "image_id"),
        Index("idx_image_status_history_user_id", "user_id"),
        Index("idx_image_status_history_created_at", "created_at"),
    )

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # User who made the change (nullable for system actions)
    user_id: int | None = Field(default=None)

    # Timestamp
    created_at: datetime | None = Field(
        default=None,
        sa_column_kwargs={"server_default": text("current_timestamp()")},
    )
