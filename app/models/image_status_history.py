"""
SQLModel-based ImageStatusHistory model for tracking image status changes.

This is a public audit table (separate from AdminActions) that tracks
all image status changes for public visibility.

Visibility rules:
- User shown for: REPOST (-1), SPOILER (2), ACTIVE (1)
- User hidden for: REVIEW (-4), LOW_QUALITY (-3), INAPPROPRIATE (-2), OTHER (0)
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


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
        ForeignKeyConstraint(
            ["report_id"],
            ["image_reports.report_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_image_status_history_report_id",
        ),
        ForeignKeyConstraint(
            ["review_id"],
            ["image_reviews.review_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_image_status_history_review_id",
        ),
        Index("idx_image_status_history_image_id", "image_id"),
        Index("idx_image_status_history_user_id", "user_id"),
        Index("idx_image_status_history_created_at", "created_at"),
        Index("idx_image_status_history_report_id", "report_id"),
        Index("idx_image_status_history_review_id", "review_id"),
    )

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # User who made the change (nullable for system actions)
    user_id: int | None = Field(default=None)

    # Reason metadata for this transition (mirrors images.reason_category / status_reason
    # at the time of the change). Nullable: legacy rows and non-deactivation transitions.
    reason_category: int | None = Field(default=None)
    reason: str | None = Field(default=None, max_length=1000)

    # Originating report/review for this transition (set on the triage/review-close
    # paths; NULL for direct mod changes and legacy rows). Exposed mods-only in the API.
    #
    # NOTE: the migration (1cdaf1ec0250) creates these as INT UNSIGNED to match the
    # legacy-unsigned image_reports/image_reviews PKs. Those PKs are still declared
    # *signed* in their models, so we keep these annotations signed too — otherwise
    # create_all can't form the FK (signed PK <- unsigned FK). Unsigning the whole
    # report_id/review_id family to match the DB is a separate schema-sync cleanup.
    report_id: int | None = Field(default=None)
    review_id: int | None = Field(default=None)

    # Timestamp
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(UtcDateTime, nullable=True, server_default=text("current_timestamp()")),
    )
