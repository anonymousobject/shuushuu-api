"""
SQLModel-based CharacterSourceLink models with inheritance for security

This module defines the CharacterSourceLinks database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

CharacterSourceLinkBase (shared public fields)
    ├─> CharacterSourceLinks (database table, adds internal fields)
    └─> API schemas (defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.
"""

from datetime import UTC, datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class CharacterSourceLinkBase(SQLModel):
    """
    Base model with shared public fields for character-source links.

    These fields are safe to expose via the API and are shared between:
    - The database table (CharacterSourceLinks)
    - API response schemas
    - API request schemas
    """

    character_tag_id: int
    source_tag_id: int


class CharacterSourceLinks(CharacterSourceLinkBase, table=True):
    """
    Database table for character-source tag links.

    Links character tags to their source/series tags (e.g., Hakurei Reimu → Touhou).

    Extends CharacterSourceLinkBase with:
    - Primary key
    - Foreign keys to tags (character and source)
    - Foreign key to user who created the link
    - Timestamp tracking
    """

    __tablename__ = "character_source_links"

    # NOTE: __table_args__ is partially redundant with Field(foreign_key=...) declarations below.
    # However, it's kept for explicit CASCADE behavior and named constraints that SQLModel's
    # Field() cannot express. Be aware: if using Alembic migrations to manage schema changes,
    # these definitions may drift from the actual database structure over time. When in doubt,
    # treat Alembic migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(
            ["character_tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_character_source_links_character_tag_id",
        ),
        ForeignKeyConstraint(
            ["source_tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_character_source_links_source_tag_id",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_character_source_links_created_by_user_id",
        ),
        Index("idx_character_tag_id", "character_tag_id"),
        Index("idx_source_tag_id", "source_tag_id"),
        Index("unique_character_source", "character_tag_id", "source_tag_id", unique=True),
    )

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # Foreign keys - note: FK constraints with CASCADE are defined in __table_args__
    # Do NOT add foreign_key= here as it creates duplicate FKs without CASCADE
    character_tag_id: int = Field(index=True)
    source_tag_id: int = Field(index=True)

    # User who created this link (nullable for SET NULL on delete)
    # FK constraint with SET NULL is defined in __table_args__
    created_by_user_id: int | None = Field(default=None, index=True)

    # Timestamp
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column_kwargs={"server_default": text("current_timestamp()")},
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
