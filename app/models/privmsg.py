"""
SQLModel-based Privmsg models with inheritance for security

This module defines the Privmsgs database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

PrivmsgBase (shared public fields)
    ├─> Privmsgs (database table)
    └─> PrivmsgCreate/PrivmsgUpdate/PrivmsgResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.

Note: Privmsgs are private messages between users.
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class PrivmsgBase(SQLModel):
    """
    Base model with shared public fields for Privmsgs.

    These fields are safe to expose via the API and are shared between:
    - The database table (Privmsgs)
    - API response schemas (PrivmsgResponse)
    - API request schemas (PrivmsgCreate, PrivmsgUpdate)
    """

    # Message details
    subject: str = Field(default="", max_length=255)
    text: str | None = Field(default=None, sa_column_kwargs={"name": "text"})

    # User references
    from_user_id: int
    to_user_id: int

    # Thread grouping
    thread_id: str | None = Field(default=None, max_length=36)

    # Public timestamp
    date: datetime


class Privmsgs(PrivmsgBase, table=True):
    """
    Database table for private messages.

    Extends PrivmsgBase with:
    - Primary key
    - Foreign key relationships
    - Timestamp

    Note: 'text' is a reserved keyword, so we use sa_column_kwargs to map it.
    """

    __tablename__ = "privmsgs"

    # NOTE: __table_args__ is partially redundant with Field(foreign_key=...) declarations below.
    # However, it's kept for explicit CASCADE behavior and named constraints that SQLModel's
    # Field() cannot express. Be aware: if using Alembic migrations to manage schema changes,
    # these definitions may drift from the actual database structure over time. When in doubt,
    # treat Alembic migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(
            ["from_user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_privmsgs_from_user_id",
        ),
        ForeignKeyConstraint(
            ["to_user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_privmsgs_to_user_id",
        ),
        Index("fk_privmsgs_from_user_id", "from_user_id"),
        Index("fk_privmsgs_to_user_id", "to_user_id"),
        Index("ix_privmsgs_thread_id", "thread_id"),
    )

    # Primary key
    privmsg_id: int | None = Field(default=None, primary_key=True)

    # Override to add foreign keys
    from_user_id: int = Field(foreign_key="users.user_id")
    to_user_id: int = Field(foreign_key="users.user_id")

    # Message status
    viewed: int = Field(default=0)
    from_del: int = Field(default=0)
    to_del: int = Field(default=0)

    thread_id: str | None = Field(default=None, max_length=36)

    # Public timestamp
    date: datetime = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
