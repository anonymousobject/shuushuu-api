"""
SQLModel-based AdminAction model for audit logging

This module defines the AdminActions database model for tracking all admin
moderation actions. This provides an audit trail for:
- Report triage (dismiss, action, escalate)
- Review management (start, vote, close, extend)

Rows are retained indefinitely: growth is a few thousand rows a year on this
site, all read paths are indexed point lookups, and an audit trail loses its
value if history expires. (A 2-year prune job existed but was never scheduled;
see git history for `prune_admin_actions`.)
"""

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlalchemy.dialects.mysql import JSON
from sqlmodel import Column, Field, SQLModel

from app.models.types import UnsignedInt, UtcDateTime


class AdminActions(SQLModel, table=True):
    """
    Audit log for admin moderation actions.

    This table logs all admin actions for accountability and debugging.
    It stores:
    - Who performed the action
    - What type of action
    - References to related entities (report, review, image)
    - JSON details with context (previous/new status, vote value, etc.)

    Rows are retained indefinitely (see module docstring). Report-linked rows
    in particular must never be deleted: report resolutions are derived from
    them (_populate_report_resolutions).
    """

    __tablename__ = "admin_actions"

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_admin_actions_user_id",
        ),
        ForeignKeyConstraint(
            ["report_id"],
            ["image_reports.report_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_admin_actions_report_id",
        ),
        ForeignKeyConstraint(
            ["review_id"],
            ["image_reviews.review_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_admin_actions_review_id",
        ),
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_admin_actions_image_id",
        ),
        Index("fk_admin_actions_user_id", "user_id"),
        Index("fk_admin_actions_report_id", "report_id"),
        Index("fk_admin_actions_review_id", "review_id"),
        Index("fk_admin_actions_image_id", "image_id"),
        Index("idx_admin_actions_created_at", "created_at"),
        Index("idx_admin_actions_action_type", "action_type"),
    )

    # Primary key
    action_id: int | None = Field(default=None, primary_key=True)

    # Admin who performed the action
    # FKs with ON DELETE SET NULL are defined in __table_args__; don't duplicate via foreign_key= param
    user_id: int | None = Field(default=None)

    action_type: int = Field(default=0)

    # INT UNSIGNED to match the legacy-unsigned image_reports/image_reviews PKs
    report_id: int | None = Field(default=None, sa_column=Column(UnsignedInt, nullable=True))
    review_id: int | None = Field(default=None, sa_column=Column(UnsignedInt, nullable=True))
    image_id: int | None = Field(default=None)

    # JSON details with action context
    # Examples:
    # - report_dismiss: {}
    # - report_action: {"previous_status": 1, "new_status": -2}
    # - review_vote: {"vote": 1, "comment": "Looks fine"}
    # - review_close: {"outcome": 1, "vote_count": 5, "keep_votes": 3, "remove_votes": 2}
    details: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    # Timestamp (indexed for time-ordered lookups)
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(UtcDateTime, nullable=True, server_default=text("current_timestamp()")),
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
