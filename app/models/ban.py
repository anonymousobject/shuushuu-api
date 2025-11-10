"""
SQLModel-based Ban models with inheritance for security

This module defines the Bans database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

BanBase (shared public fields)
    ├─> Bans (database table, adds internal fields)
    └─> BanCreate/BanUpdate/BanResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class BanAction(str, Enum):
    """Enumeration of ban action types"""

    NONE = "None"
    ONE_WEEK = "One Week Ban"
    TWO_WEEK = "Two Week Ban"
    ONE_MONTH = "One Month Ban"
    PERMANENT = "Permanent Ban"


class BanBase(SQLModel):
    """
    Base model with shared public fields for Bans.

    These fields are safe to expose via the API and are shared between:
    - The database table (Bans)
    - API response schemas (BanResponse)
    - API request schemas (BanCreate, BanUpdate)
    """

    # Ban details
    action: str | None = Field(default=None, max_length=50)
    reason: str | None = Field(default=None)
    message: str | None = Field(default=None, max_length=255)

    # Status
    viewed: int = Field(default=0)

    # Dates
    date: datetime | None = Field(default=None)
    expires: datetime | None = Field(default=None)


class Bans(BanBase, table=True):
    """
    Database table for user bans with internal fields.

    Extends BanBase with:
    - Primary key and foreign keys
    - User being banned
    - Moderator who issued the ban (internal)
    - IP address (internal)

    Internal fields (should NOT be exposed via public API):
    - ip: Privacy-sensitive
    - banned_by: Moderator user ID (may be sensitive)
    """

    __tablename__ = "bans"

    # NOTE: __table_args__ is partially redundant with Field(foreign_key=...) declarations below.
    # However, it's kept for explicit CASCADE behavior and named constraints that SQLModel's
    # Field() cannot express. Be aware: if using Alembic migrations to manage schema changes,
    # these definitions may drift from the actual database structure over time. When in doubt,
    # treat Alembic migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(
            ["banned_by"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_bans_banned_by",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_bans_user_id",
        ),
        Index("fk_bans_banned_by", "banned_by"),
        Index("fk_bans_user_id", "user_id"),
    )

    # Primary key
    ban_id: int | None = Field(default=None, primary_key=True)

    # Public reference - user being banned
    user_id: int = Field(foreign_key="users.user_id")

    # Override to add server default
    date: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )

    # Internal fields
    banned_by: int | None = Field(default=None, foreign_key="users.user_id")
    ip: str | None = Field(default=None, max_length=15)

    # Note: Relationships (e.g., user, banned_by_user) are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
