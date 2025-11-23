"""
SQLModel-based User models with inheritance for security

This module defines the Users database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

UserBase (shared public fields)
    ├─> Users (database table, adds internal/sensitive fields)
    └─> UserCreate/UserUpdate/UserResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class UserBase(SQLModel):
    """
    Base model with shared public fields for Users.

    These fields are safe to expose via the API and are shared between:
    - The database table (Users)
    - API response schemas (UserResponse)
    - API request schemas (UserCreate, UserUpdate)
    """

    # Basic information
    username: str = Field(max_length=30)

    # Public profile
    location: str | None = Field(default=None, max_length=100)
    website: str | None = Field(default=None, max_length=100)
    interests: str | None = Field(default=None, max_length=255)
    user_title: str | None = Field(default=None, max_length=50)

    # Avatar
    avatar: str = Field(default="", max_length=255)
    gender: str = Field(default="", max_length=1)

    # Public stats
    posts: int = Field(default=0)
    image_posts: int = Field(default=0)
    favorites: int = Field(default=0)


class Users(UserBase, table=True):
    """
    Database table for users with internal and sensitive fields.

    Extends UserBase with:
    - Primary key and foreign keys
    - Authentication fields (password, salt)
    - Email and privacy settings
    - User preferences
    - Internal moderation/admin fields

    Internal/sensitive fields (should NOT be exposed via public API):
    - password, salt, newpassword, newsalt: Authentication (highly sensitive)
    - email: Privacy-sensitive
    - actkey: Activation key (security-sensitive)
    - admin, active: Moderation/access control
    - All preference fields: User-private settings
    - infected_by, date_infected: Internal tracking
    - bookmark: User-private reference
    """

    __tablename__ = "users"

    # NOTE: __table_args__ is partially redundant with Field(foreign_key=...) declarations below.
    # However, it's kept for explicit CASCADE behavior and named constraints that SQLModel's
    # Field() cannot express. Be aware: if using Alembic migrations to manage schema changes,
    # these definitions may drift from the actual database structure over time. When in doubt,
    # treat Alembic migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(
            ["bookmark"],
            ["images.image_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_bookmark",
        ),
        Index("fk_bookmark", "bookmark"),
        Index("username", "username", unique=True),
    )

    # Primary key
    user_id: int = Field(primary_key=True)

    # Public timestamps
    date_joined: datetime = Field(sa_column_kwargs={"server_default": text("current_timestamp()")})
    last_login: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )
    last_login_new: datetime | None = Field(default=None)

    # Internal status fields
    active: int = Field(default=0)
    admin: int = Field(default=0)

    # Authentication (highly sensitive - never expose)
    password: str = Field(max_length=255)  # Extended for bcrypt (60 chars needed)
    password_type: str = Field(default="md5", max_length=10)  # 'md5' or 'bcrypt'
    salt: str = Field(max_length=16)
    newpassword: str | None = Field(default=None, max_length=40)
    newsalt: str | None = Field(default=None, max_length=16)
    actkey: str = Field(default="", max_length=32)

    # Account lockout (security)
    failed_login_attempts: int = Field(default=0)
    lockout_until: datetime | None = Field(default=None)

    # Contact info (privacy-sensitive)
    email: str = Field(max_length=120)

    # User preferences (private)
    timezone: Decimal = Field(default=Decimal("0.00"))
    email_pm_pref: int = Field(default=1)
    spoiler_warning_pref: int = Field(default=1)
    thumb_layout: int = Field(default=0)
    sorting_pref: str = Field(default="image_id", max_length=100)
    sorting_pref_order: str = Field(default="DESC", max_length=10)
    images_per_page: int = Field(default=10)
    show_all_images: int = Field(default=0)

    # Rate limiting
    maximgperday: int = Field(default=15)
    rating_ratio: float = Field(default=0.0)

    # Internal tracking
    infected_by: int = Field(default=0)
    date_infected: int = Field(default=0)
    infected: int | None = Field(default=0)

    # References
    forum_id: int | None = Field(default=None)
    bookmark: int | None = Field(default=None, foreign_key="images.image_id")

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
