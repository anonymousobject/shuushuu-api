"""
SQLModel-based News models with inheritance for security

This module defines the News database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

NewsBase (shared public fields)
    ├─> News (database table, adds internal fields)
    └─> NewsCreate/NewsUpdate/NewsResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class NewsBase(SQLModel):
    """
    Base model with shared public fields for News.

    These fields are safe to expose via the API and are shared between:
    - The database table (News)
    - API response schemas (NewsResponse)
    - API request schemas (NewsCreate, NewsUpdate)
    """

    # News content
    title: str | None = Field(default=None, max_length=128)
    news_text: str | None = Field(default=None)

    # Public timestamps
    date: datetime | None = Field(default=None)
    edited: datetime | None = Field(default=None)


class News(NewsBase, table=True):
    """
    Database table for news posts.

    Extends NewsBase with:
    - Primary key
    - User who created the news (could be public or internal)
    - Foreign key relationships

    Internal fields (may be sensitive):
    - user_id: Author user ID (could be considered internal)
    """

    __tablename__ = "news"

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
            name="fk_news_user_id",
        ),
        Index("fk_news_user_id", "user_id"),
    )

    # Primary key
    news_id: int | None = Field(default=None, primary_key=True)

    # User reference (public or internal depending on use case)
    user_id: int = Field(foreign_key="users.user_id")

    # Override to add server default
    date: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
