"""
SQLModel-based AdminAction model for audit logging

This module defines the AdminActions database model for tracking all admin
moderation actions. This provides an audit trail for:
- Report triage (dismiss, action, escalate)
- Review management (start, vote, close, extend)

The table is pruned after 2 years to maintain manageable size.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlalchemy.dialects.mysql import JSON
from sqlmodel import Column, Field, SQLModel


class AdminActions(SQLModel, table=True):
    """
    Audit log for admin moderation actions.

    This table logs all admin actions for accountability and debugging.
    It stores:
    - Who performed the action
    - What type of action
    - References to related entities (report, review, image)
    - JSON details with context (previous/new status, vote value, etc.)

    Note: This table should be pruned periodically (after 2 years) to
    maintain reasonable size. See background job `prune_admin_actions`.
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
    user_id: int | None = Field(default=None, foreign_key="users.user_id")

    # Action type (stored as int, mapped to AdminActionType constants)
    # 1=report_dismiss, 2=report_action, 3=review_start, 4=review_vote,
    # 5=review_close, 6=review_extend
    action_type: int = Field(default=0)

    # Related entities (nullable - not all actions have all references)
    report_id: int | None = Field(default=None, foreign_key="image_reports.report_id")
    review_id: int | None = Field(default=None, foreign_key="image_reviews.review_id")
    image_id: int | None = Field(default=None, foreign_key="images.image_id")

    # JSON details with action context
    # Examples:
    # - report_dismiss: {}
    # - report_action: {"previous_status": 1, "new_status": -2}
    # - review_vote: {"vote": 1, "comment": "Looks fine"}
    # - review_close: {"outcome": 1, "vote_count": 5, "keep_votes": 3, "remove_votes": 2}
    details: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    # Timestamp (indexed for pruning queries)
    created_at: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
