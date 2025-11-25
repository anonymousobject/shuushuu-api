"""
SQLModel-based UserSuspension model for audit trail

This module defines the user_suspensions table for tracking suspension history.
Unlike other models, this doesn't use inheritance since it's purely for audit
logging and not exposed via API endpoints.
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class UserSuspensions(SQLModel, table=True):
    """
    Audit trail table for user account suspensions and reactivations.

    Logs every suspension and reactivation action for accountability and history.
    This table is append-only - records should never be deleted or modified.

    Fields:
    - suspension_id: Primary key
    - user_id: User being suspended/reactivated
    - action: Type of action ("suspended" or "reactivated")
    - actioned_by: Admin/moderator who performed the action
    - actioned_at: When the action occurred
    - suspended_until: Expiration time (for suspensions only)
    - reason: Reason shown to user (for suspensions only)
    """

    __tablename__ = "user_suspensions"

    # NOTE: __table_args__ is partially redundant with Field(foreign_key=...) declarations below.
    # However, it's kept for explicit CASCADE behavior and named constraints that SQLModel's
    # Field() cannot express. Be aware: if using Alembic migrations to manage schema changes,
    # these definitions may drift from the actual database structure over time. When in doubt,
    # treat Alembic migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_user_suspensions_user_id",
        ),
        ForeignKeyConstraint(
            ["actioned_by"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_user_suspensions_actioned_by",
        ),
        Index("idx_user_suspensions_user_id", "user_id"),
        Index("idx_user_suspensions_actioned_by", "actioned_by"),
        Index("idx_user_suspensions_actioned_at", "actioned_at"),
    )

    # Primary key
    suspension_id: int | None = Field(default=None, primary_key=True)

    # User being suspended/reactivated
    user_id: int = Field(foreign_key="users.user_id")

    # Action type
    action: str = Field(max_length=20)  # "suspended" or "reactivated"

    # Who performed the action
    actioned_by: int | None = Field(default=None, foreign_key="users.user_id")

    # When the action occurred
    actioned_at: datetime = Field(sa_column_kwargs={"server_default": text("current_timestamp()")})

    # Suspension details (only for "suspended" action)
    suspended_until: datetime | None = Field(default=None)
    reason: str | None = Field(default=None, max_length=500)

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
