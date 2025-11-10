"""
SQLModel-based UserSession models with inheritance for security

This module defines the UserSessions database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

UserSessionBase (shared public fields)
    ├─> UserSessions (database table, adds internal fields)
    └─> UserSessionResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.

Note: UserSessions tracks active user sessions for authentication and activity monitoring.
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class UserSessionBase(SQLModel):
    """
    Base model with shared public fields for UserSessions.

    These fields are safe to expose via the API and are shared between:
    - The database table (UserSessions)
    - API response schemas (UserSessionResponse)

    Note: Most session fields are internal/sensitive, so the base is minimal.
    """

    # User reference
    user_id: int

    # Session timestamps
    last_used: datetime
    last_view_date: datetime | None = Field(default=None)


class UserSessions(UserSessionBase, table=True):
    """
    Database table for user sessions with internal fields.

    Extends UserSessionBase with:
    - Session ID (primary key)
    - IP address (internal/sensitive)
    - Activity tracking (internal)

    Internal fields (should NOT be exposed via public API):
    - session_id: Session token (highly sensitive)
    - ip: Privacy-sensitive
    - lastpage: Internal tracking
    - last_search: Internal tracking
    """

    __tablename__ = "user_sessions"

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
            name="fk_user_sessions_user_id",
        ),
        Index("fk_user_sessions_user_id", "user_id"),
        Index("ip", "ip"),
    )

    # Primary key (session token - highly sensitive)
    session_id: str = Field(default="", primary_key=True, max_length=50)

    # Override to add foreign key
    user_id: int = Field(foreign_key="users.user_id")

    # Override to add server defaults
    last_used: datetime = Field(sa_column_kwargs={"server_default": text("current_timestamp()")})
    last_view_date: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )

    # Internal tracking fields
    ip: str = Field(default="", max_length=16)
    lastpage: str | None = Field(default=None, max_length=200)
    last_search: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
