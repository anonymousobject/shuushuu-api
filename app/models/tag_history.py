"""
SQLModel-based TagHistory models with inheritance for security

This module defines the TagHistory database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

TagHistoryBase (shared public fields)
    ├─> TagHistory (database table, adds internal fields)
    └─> TagHistoryResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.

Note: TagHistory tracks all tag additions/removals on images for auditing.
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class TagHistoryBase(SQLModel):
    """
    Base model with shared public fields for TagHistory.

    These fields are safe to expose via the API and are shared between:
    - The database table (TagHistory)
    - API response schemas (TagHistoryResponse)
    """

    # References
    image_id: int | None = Field(default=None)
    tag_id: int | None = Field(default=None)

    # Action: 'a' for add, 'r' for remove
    action: str | None = Field(default=None, max_length=1)

    # Public timestamp
    date: datetime | None = Field(default=None)


class TagHistory(TagHistoryBase, table=True):
    """
    Database table for tag history with internal fields.

    Extends TagHistoryBase with:
    - Primary key
    - User who performed the action (internal)
    - Foreign key relationships

    Internal fields (should NOT be exposed via public API):
    - user_id: User who performed the action (privacy-sensitive)
    """

    __tablename__ = "tag_history"

    # FKs are declared here ONLY — never add foreign_key= to the Field()s below:
    # that emits a second, unnamed constraint whose implicit NO ACTION vetoes the
    # ON DELETE rule declared here (PR #370; guarded by
    # tests/integration/test_fk_constraint_names.py). When in doubt, treat Alembic
    # migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_history_image_id",
        ),
        ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_history_tag_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_history_user_id",
        ),
        Index("fk_tag_history_tag_id", "tag_id"),
        Index("image_id", "image_id"),
        Index("user_id", "user_id"),
        Index("idx_tag_history_tag_date", "tag_id", "date"),
        Index("idx_tag_history_user_date", "user_id", "date"),
    )

    # Primary key
    tag_history_id: int | None = Field(default=None, primary_key=True)

    # Override to add foreign keys
    image_id: int | None = Field(default=None)
    tag_id: int | None = Field(default=None)

    # Override to add server default
    date: datetime | None = Field(
        default=None,
        sa_column=Column(UtcDateTime, nullable=True, server_default=text("CURRENT_TIMESTAMP")),
    )

    # Internal field
    user_id: int | None = Field(default=None)

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
