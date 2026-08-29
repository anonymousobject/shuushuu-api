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
from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKeyConstraint, Index, text
from sqlmodel import Field, Relationship, SQLModel

from app.models.types import UtcDateTime

if TYPE_CHECKING:
    from app.models.tag import Tags


class TagLinkBase(SQLModel):
    """
    Base model with shared public fields for TagLinks.

    These fields are safe to expose via the API and are shared between:
    - The database table (TagLinks)
    - API response schemas (TagLinkResponse)
    - API request schemas (TagLinkCreate)
    """

    # Junction table primary keys
    tag_id: int = Field(primary_key=True)
    image_id: int = Field(primary_key=True)


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

    # FKs are declared here ONLY — never add foreign_key= to the Field()s below:
    # that emits a second, unnamed constraint whose implicit NO ACTION vetoes the
    # ON DELETE rule declared here (PR #370; guarded by
    # tests/integration/test_fk_constraint_names.py). When in doubt, treat Alembic
    # migrations as the source of truth for production schema.
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
        Index("idx_tag_links_tag_date", "tag_id", "date_linked"),
        Index("idx_tag_links_user_date_image", "user_id", "date_linked", "image_id"),
    )

    # Public timestamp
    date_linked: datetime | None = Field(
        default=None,
        sa_column=Column(UtcDateTime, nullable=True, server_default=text("CURRENT_TIMESTAMP")),
    )

    # Internal field
    user_id: int | None = Field(default=None)

    # Relationship to tag
    tag: Tags = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[TagLinks.tag_id]",
        }
    )
