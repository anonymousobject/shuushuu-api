"""
SQLModel-based Comment models with inheritance for security

This module defines the Comments database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

CommentBase (shared public fields)
    ├─> Comments (database table, adds internal fields)
    └─> CommentCreate/CommentUpdate/CommentResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.

Note: The database table name remains 'posts' for backwards compatibility,
but the model is named Comments and all references use 'comment' terminology.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.user import Users


class CommentBase(SQLModel):
    """
    Base model with shared public fields for Comments.

    These fields are safe to expose via the API and are shared between:
    - The database table (Comments)
    - API response schemas (CommentResponse)
    - API request schemas (CommentCreate, CommentUpdate)
    """

    # Comment content
    post_text: str = Field(default="")

    # Image reference
    image_id: int | None = Field(default=None)

    # Threading: null for top-level, post_id of parent for replies
    parent_comment_id: int | None = Field(default=None)

    # Soft-delete flag
    deleted: bool = Field(default=False, index=True)


class Comments(CommentBase, table=True):
    """
    Database table for comments with internal fields.

    Extends CommentBase with:
    - Primary key and foreign keys
    - Internal tracking fields (IP, user agent, etc.)
    - Update tracking fields
    - User relationships

    Internal fields (should NOT be exposed via public API):
    - ip: Privacy-sensitive tracking
    - last_updated, last_updated_user_id: Internal moderation
    - update_count: Internal metadata

    Note: The database table name remains 'posts' for backwards compatibility.
    """

    __tablename__ = "posts"

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
            name="fk_posts_image_id",
        ),
        ForeignKeyConstraint(
            ["parent_comment_id"],
            ["posts.post_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_posts_parent_comment_id",
        ),
        ForeignKeyConstraint(
            ["last_updated_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_posts_last_updated_user_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_posts_user_id",
        ),
        Index("fk_posts_image_id", "image_id"),
        Index("fk_posts_parent_comment_id", "parent_comment_id"),
        Index("fk_posts_last_updated_user_id", "last_updated_user_id"),
        Index("fk_posts_user_id", "user_id"),
        Index("idx_date", "date"),
    )

    # Primary key
    post_id: int | None = Field(default=None, primary_key=True)

    # User reference (public)
    user_id: int = Field(foreign_key="users.user_id")

    # Public timestamp
    date: datetime = Field(sa_column_kwargs={"server_default": text("current_timestamp()")})

    # Public update tracking
    update_count: int = Field(default=0)

    # Internal tracking fields (privacy-sensitive)
    ip: str = Field(default="", max_length=15)

    # Internal moderation fields
    last_updated: datetime | None = Field(default=None)
    last_updated_user_id: int | None = Field(default=None, foreign_key="users.user_id")

    # Relationships
    # Load user info for displaying comment author
    # Specify foreign_keys since there are multiple FKs to Users table
    user: Users = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[Comments.user_id]",
        }
    )

    # Note: Other relationships are intentionally omitted.
    # Foreign keys are sufficient for most queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
