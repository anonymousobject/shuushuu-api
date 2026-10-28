"""
SQLModel-based Favorite models with inheritance for security

This module defines the Favorites database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

FavoriteBase (shared public fields)
    ├─> Favorites (database table, adds internal fields)
    └─> FavoriteCreate/FavoriteUpdate/FavoriteResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.
"""

from datetime import UTC, datetime

from sqlalchemy import Column, ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class FavoriteBase(SQLModel):
    """
    Base model with shared public fields for Favorites.

    These fields are safe to expose via the API and are shared between:
    - The database table (Favorites)
    - API response schemas (FavoriteResponse)
    - API request schemas (FavoriteCreate, FavoriteUpdate, etc)
    """

    # Composite primary key (order matches schema: user_id, image_id)
    user_id: int = Field(primary_key=True)
    image_id: int = Field(primary_key=True)

    # Public timestamp
    fav_date: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(UtcDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.


class Favorites(FavoriteBase, table=True):
    """
    Database table for favorites with internal fields.

    Extends FavoriteBase with:
    - Primary key and foreign keys
    """

    __tablename__ = "favorites"

    # FKs are declared here ONLY — never add foreign_key= to the Field()s below:
    # that emits a second, unnamed constraint whose implicit NO ACTION vetoes the
    # ON DELETE rule declared here (PR #370; guarded by
    # tests/integration/test_fk_constraint_names.py). When in doubt, treat Alembic
    # migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_favorites_image_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_favorites_user_id",
        ),
        Index("fk_favorites_image_id", "image_id"),
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
