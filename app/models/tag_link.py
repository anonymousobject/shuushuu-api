"""
SQLModel-based TagLinks models with inheritance for security

This module defines the TagLinks database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

TagLinkBase (shared public fields)
    ├─> TagLinks (database table, adds internal fields)
    └─> TagLinkCreate/TagLinkResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.

Note: TagLinks is a junction table connecting tags to images with metadata.
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class TagLinkBase(SQLModel):
    """
    Base model with shared public fields for TagLinks.

    These fields are safe to expose via the API and are shared between:
    - The database table (TagLinks)
    - API response schemas (TagLinkResponse)
    - API request schemas (TagLinkCreate)
    """

    # Junction table primary keys
    tag_id: int = Field(foreign_key="tags.tag_id", primary_key=True)
    image_id: int = Field(foreign_key="images.image_id", primary_key=True)


class TagLinks(TagLinkBase, table=True):
    """
    Database table for tag-image links with internal fields.

    Extends TagLinkBase with:
    - Composite primary key (tag_id, image_id)
    - User who created the link (internal)
    - Date linked timestamp

    Internal fields (should NOT be exposed via public API):
    - user_id: Creator user (privacy-sensitive)
    """

    __tablename__ = "tag_links"

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
            name="fk_tag_links_image_id",
        ),
        ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_tag_links_tag_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_links_user_id",
        ),
        Index("fk_tag_links_image_id", "image_id"),
        Index("fk_tag_links_user_id", "user_id"),
    )

    # Public timestamp
    date_linked: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )

    # Internal field
    user_id: int | None = Field(default=None, foreign_key="users.user_id")

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
