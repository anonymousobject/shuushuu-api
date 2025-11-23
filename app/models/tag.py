"""
SQLModel-based Tag models with inheritance for security

This module defines the Tags database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

TagBase (shared public fields)
    ├─> Tags (database table, adds internal fields)
    └─> TagCreate/TagUpdate/TagResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.
"""

from datetime import datetime

from pydantic import field_validator
from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.config import TagType


class TagBase(SQLModel):
    """
    Base model with shared public fields for Tags.

    These fields are safe to expose via the API and are shared between:
    - The database table (Tags)
    - API response schemas (TagResponse)
    - API request schemas (TagCreate, TagUpdate)
    """

    # Basic information
    title: str | None = Field(default=None, max_length=150)
    desc: str | None = Field(default=None, max_length=200)
    type: int = Field(
        default=TagType.THEME,
        description="Tag type: 0=All, 1=Theme, 2=Source, 3=Artist, 4=Character",
    )

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: int) -> int:
        """Validate that type is one of the allowed TagType constants."""
        valid_types = {
            TagType.ALL,
            TagType.THEME,
            TagType.SOURCE,
            TagType.ARTIST,
            TagType.CHARACTER,
        }
        if v not in valid_types:
            raise ValueError(
                f"Invalid tag type: {v}. Must be one of {valid_types} "
                f"(0=All, 1=Theme, 2=Source, 3=Artist, 4=Character)"
            )
        return v


class Tags(TagBase, table=True):
    """
    Database table for tags with internal fields.

    Extends TagBase with:
    - Primary key and foreign keys
    - Date tracking
    - Alias and inheritance relationships
    - Internal metadata

    Internal fields (should NOT be exposed via public API):
    - user_id: Creator user (privacy-sensitive)
    """

    __tablename__ = "tags"

    # NOTE: __table_args__ is partially redundant with Field(foreign_key=...) declarations below.
    # However, it's kept for explicit CASCADE behavior and named constraints that SQLModel's
    # Field() cannot express. Be aware: if using Alembic migrations to manage schema changes,
    # these definitions may drift from the actual database structure over time. When in doubt,
    # treat Alembic migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(
            ["alias"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tags_alias",
        ),
        ForeignKeyConstraint(
            ["inheritedfrom_id"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tags_inheritedfrom_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tags_user_id",
        ),
        Index("fk_tags_alias", "alias"),
        Index("fk_tags_inheritedfrom_id", "inheritedfrom_id"),
        Index("fk_tags_user_id", "user_id"),
        Index("type_alias", "type", "alias"),
    )

    # Primary key
    tag_id: int | None = Field(default=None, primary_key=True)

    # Public timestamp
    date_added: datetime = Field(sa_column_kwargs={"server_default": text("current_timestamp()")})

    # Public relationship fields
    alias: int | None = Field(default=None, foreign_key="tags.tag_id")
    inheritedfrom_id: int | None = Field(default=None, foreign_key="tags.tag_id")

    # Internal fields
    user_id: int | None = Field(default=None, foreign_key="users.user_id")

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
