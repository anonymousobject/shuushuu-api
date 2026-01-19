"""
SQLModel-based TagAuditLog model for tracking tag metadata changes.

This module tracks all changes to tag metadata including:
- Renames (title changes)
- Type changes
- Alias changes (setting/removing alias_of)
- Inheritance changes (setting/removing parent)
- Character-source link changes

Uses explicit columns per field type for type safety.
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class TagAuditLogBase(SQLModel):
    """
    Base model with shared fields for TagAuditLog.

    These fields are safe to expose via the API.
    """

    tag_id: int
    action_type: str = Field(max_length=32)

    # Rename fields
    old_title: str | None = Field(default=None, max_length=128)
    new_title: str | None = Field(default=None, max_length=128)

    # Type change fields
    old_type: int | None = Field(default=None)
    new_type: int | None = Field(default=None)

    # Alias change fields (FK to tags.tag_id)
    old_alias_of: int | None = Field(default=None)
    new_alias_of: int | None = Field(default=None)

    # Parent/inheritance change fields (FK to tags.tag_id)
    old_parent_id: int | None = Field(default=None)
    new_parent_id: int | None = Field(default=None)

    # Character-source link fields (FK to tags.tag_id)
    character_tag_id: int | None = Field(default=None)
    source_tag_id: int | None = Field(default=None)


class TagAuditLog(TagAuditLogBase, table=True):
    """
    Database table for tag audit log.

    Tracks all metadata changes to tags for accountability and history.
    Each row represents a single change, with only the relevant columns
    populated for that action type.
    """

    __tablename__ = "tag_audit_log"

    __table_args__ = (
        ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_tag_audit_log_tag_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_user_id",
        ),
        ForeignKeyConstraint(
            ["old_alias_of"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_old_alias_of",
        ),
        ForeignKeyConstraint(
            ["new_alias_of"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_new_alias_of",
        ),
        ForeignKeyConstraint(
            ["old_parent_id"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_old_parent_id",
        ),
        ForeignKeyConstraint(
            ["new_parent_id"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_new_parent_id",
        ),
        ForeignKeyConstraint(
            ["character_tag_id"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_character_tag_id",
        ),
        ForeignKeyConstraint(
            ["source_tag_id"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_audit_log_source_tag_id",
        ),
        Index("idx_tag_audit_log_tag_id", "tag_id"),
        Index("idx_tag_audit_log_user_id", "user_id"),
        Index("idx_tag_audit_log_action_type", "action_type"),
        Index("idx_tag_audit_log_created_at", "created_at"),
        Index("idx_tag_audit_log_character_tag_id", "character_tag_id"),
        Index("idx_tag_audit_log_source_tag_id", "source_tag_id"),
    )

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # User who made the change
    user_id: int | None = Field(default=None)

    # Timestamp
    created_at: datetime | None = Field(
        default=None,
        sa_column_kwargs={"server_default": text("current_timestamp()")},
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
