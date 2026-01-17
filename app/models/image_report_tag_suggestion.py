"""
SQLModel-based ImageReportTagSuggestion model.

This table stores tag suggestions made by users when filing MISSING_TAGS reports.
Each suggestion tracks whether it was accepted/rejected by moderators for
contribution metrics and potential promotion to tagging roles.
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKey, Index, Integer, UniqueConstraint, text
from sqlmodel import Field, SQLModel


class ImageReportTagSuggestionBase(SQLModel):
    """
    Base model with shared fields for ImageReportTagSuggestions.
    """

    report_id: int
    tag_id: int
    suggestion_type: int = Field(default=1)  # 1=add, 2=remove
    accepted: bool | None = Field(default=None)  # NULL=pending, True=approved, False=rejected


class ImageReportTagSuggestions(ImageReportTagSuggestionBase, table=True):
    """
    Database table for tag suggestions in image reports.

    Stores tags suggested by users when filing MISSING_TAGS reports.
    Tracks acceptance/rejection for contribution metrics.
    """

    __tablename__ = "image_report_tag_suggestions"

    __table_args__ = (
        Index("idx_report_id", "report_id"),
        Index("idx_tag_id", "tag_id"),
        Index("idx_accepted", "accepted"),
        UniqueConstraint("report_id", "tag_id", name="unique_report_tag"),
    )

    suggestion_id: int | None = Field(default=None, primary_key=True)

    report_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("image_reports.report_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )

    tag_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("tags.tag_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )

    created_at: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )
