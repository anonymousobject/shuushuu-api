"""
SQLModel-based CommentReport models with inheritance for security

This module defines the CommentReports database model using SQLModel.
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKey, Index, Integer, text
from sqlmodel import Field, SQLModel

from app.config import ReportStatus


class CommentReportBase(SQLModel):
    """
    Base model with shared public fields for CommentReports.

    These fields are safe to expose via the API.
    """

    comment_id: int
    user_id: int

    category: int | None = Field(default=None)
    reason_text: str | None = Field(default=None, max_length=1000)

    status: int = Field(default=ReportStatus.PENDING)

    admin_notes: str | None = Field(default=None)


class CommentReports(CommentReportBase, table=True):
    """
    Database table for comment reports.

    Extends CommentReportBase with:
    - Primary key
    - Foreign key relationships
    - Timestamps
    - Review tracking fields
    """

    __tablename__ = "comment_reports"

    __table_args__ = (
        Index("idx_comment_reports_comment_id", "comment_id"),
        Index("idx_comment_reports_user_id", "user_id"),
        Index("idx_comment_reports_reviewed_by", "reviewed_by"),
        Index("idx_comment_reports_status_category", "status", "category"),
        Index(
            "idx_comment_reports_pending_per_user",
            "comment_id",
            "user_id",
            "status",
        ),
    )

    report_id: int | None = Field(default=None, primary_key=True)

    comment_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("posts.post_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("users.user_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )

    created_at: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )

    reviewed_by: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("users.user_id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
    )
    reviewed_at: datetime | None = Field(default=None)
