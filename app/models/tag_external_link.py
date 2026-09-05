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

from sqlalchemy import Column, ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime, ci_string


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

    # FKs are declared here ONLY — never add foreign_key= to the Field()s below:
    # that emits a second, unnamed constraint whose implicit NO ACTION vetoes the
    # ON DELETE rule declared here (PR #370; guarded by
    # tests/integration/test_fk_constraint_names.py). When in doubt, treat Alembic
    # migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_tag_external_links_tag_id",
        ),
        Index("idx_tag_id", "tag_id"),
        Index("unique_tag_url", "tag_id", "url", unique=True),
        Index("idx_tag_external_links_site_external_id", "site", "external_id"),
    )

    # Primary key
    link_id: int | None = Field(default=None, primary_key=True)

    # Foreign key
    tag_id: int = Field(index=True)

    # Timestamp
    date_added: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(UtcDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    )

    # Dead link tracking
    dead_at: datetime | None = Field(default=None, sa_column=Column(UtcDateTime, nullable=True))
    archive_url: str | None = Field(default=None, max_length=2000)

    # Structured identity parsed from url by app/services/artist_identity.py.
    # NULL for URLs no registered parser recognizes. The unique guard on
    # (site, external_id) is added by a later migration, after backfill
    # conflicts are hand-resolved (see the design doc).
    # ci_string: identity lookups and the future uniqueness guard are
    # case-insensitive on both dialects (ADR-0008).
    site: str | None = Field(default=None, max_length=32, sa_type=ci_string(32))  # type: ignore[call-overload]
    external_id: str | None = Field(default=None, max_length=128, sa_type=ci_string(128))  # type: ignore[call-overload]

    # Custom per-tag display order. NULL = not custom-ordered; the read query then
    # falls back to a computed default (shuu-wiki links first, then by date_added).
    # A drag-to-reorder writes explicit positions.
    position: int | None = Field(default=None)

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
