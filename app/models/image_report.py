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
    text: str | None = Field(default=None, sa_column_kwargs={"name": "text"})

    # Status
    open: int = Field(default=1)


class ImageReports(ImageReportBase, table=True):
    """
    Database table for image reports.

    Extends ImageReportBase with:
    - Primary key
    - Foreign key relationships
    - Timestamp

    Note: 'text' is a reserved keyword, so we use sa_column_kwargs to map it.
    """

    __tablename__ = "image_reports"

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
            name="fk_image_reports_image_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_reports_user_id",
        ),
        Index("fk_image_reports_image_id", "image_id"),
        Index("fk_image_reports_user_id", "user_id"),
        Index("open", "open"),
    )

    # Primary key
    image_report_id: int | None = Field(default=None, primary_key=True)

    # Override to add foreign keys
    image_id: int = Field(foreign_key="images.image_id")
    user_id: int = Field(foreign_key="users.user_id")

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
