"""
SQLModel-based TagExternalLink models with inheritance for security

This module defines the TagExternalLinks database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

TagExternalLinkBase (shared public fields)
    ├─> TagExternalLinks (database table, adds internal fields)
    └─> API schemas (defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.
"""

from datetime import UTC, datetime

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


class TagExternalLinkBase(SQLModel):
    """
    Base model with shared public fields for tag external links.

    These fields are safe to expose via the API and are shared between:
    - The database table (TagExternalLinks)
    - API response schemas
    - API request schemas
    """

    url: str = Field(max_length=2000)


class TagExternalLinks(TagExternalLinkBase, table=True):
    """
    Database table for tag external links.

    Stores URLs associated with tags (artist sites, social media, etc.)

    Extends TagExternalLinkBase with:
    - Primary key
    - Foreign key to tags
    - Date tracking
    """

    __tablename__ = "tag_external_links"

    __table_args__ = (
        Index("idx_tag_id", "tag_id"),
        Index("unique_tag_url", "tag_id", "url", unique=True),
    )

    # Primary key
    link_id: int | None = Field(default=None, primary_key=True)

    # Foreign key
    tag_id: int = Field(foreign_key="tags.tag_id", index=True)

    # Timestamp
    date_added: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column_kwargs={"server_default": text("current_timestamp()")},
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
