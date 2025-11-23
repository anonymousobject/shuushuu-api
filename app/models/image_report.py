"""
SQLModel-based ImageReport models with inheritance for security

This module defines the ImageReports database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

ImageReportBase (shared public fields)
    ├─> ImageReports (database table, adds internal fields)
    └─> ImageReportCreate/ImageReportResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.config import ReportStatus


class ImageReportBase(SQLModel):
    """
    Base model with shared public fields for ImageReports.

    These fields are safe to expose via the API and are shared between:
    - The database table (ImageReports)
    - API response schemas (ImageReportResponse)
    - API request schemas (ImageReportCreate)
    """

    # References
    image_id: int
    user_id: int

    # Report details
    category: int | None = Field(default=None)
    reason_text: str | None = Field(default=None)

    # Status: 0=pending, 1=reviewed, 2=dismissed
    status: int = Field(default=ReportStatus.PENDING)


class ImageReports(ImageReportBase, table=True):
    """
    Database table for image reports.

    Extends ImageReportBase with:
    - Primary key
    - Foreign key relationships
    - Timestamps
    - Review tracking fields

    Note: 'reason_text' was renamed from 'text' to avoid reserved keyword issues.
    """

    __tablename__ = "image_reports"

    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_reports_image_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_reports_user_id",
        ),
        ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_image_reports_reviewed_by",
        ),
        Index("fk_image_reports_image_id", "image_id"),
        Index("fk_image_reports_user_id", "user_id"),
        Index("fk_image_reports_reviewed_by", "reviewed_by"),
        Index("idx_image_reports_status", "status"),
    )

    # Primary key
    report_id: int | None = Field(default=None, primary_key=True)

    # Override to add foreign keys
    image_id: int = Field(foreign_key="images.image_id")
    user_id: int = Field(foreign_key="users.user_id")

    # Public timestamp
    created_at: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )

    # Review tracking
    reviewed_by: int | None = Field(default=None, foreign_key="users.user_id")
    reviewed_at: datetime | None = Field(default=None)

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
