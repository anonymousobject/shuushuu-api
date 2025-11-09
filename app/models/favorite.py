"""
SQLModel-based Favorite models with inheritance for security

This module defines the Favorites database model using SQLModel, which combines
SQLAlchemy and Pydantic functionality. The inheritance structure is:

FavoriteBase (shared public fields)
    ├─> Favorites (database table, adds internal fields)
    └─> FavoriteCreate/FavoriteUpdate/FavoriteResponse (API schemas, defined in app/schemas)

This approach eliminates field duplication while maintaining security boundaries.
"""
from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel


class FavoriteBase(SQLModel):
    """
    Base model with shared public fields for Favorites.

    These fields are safe to expose via the API and are shared between:
    - The database table (Favorites)
    - API response schemas (FavoriteResponse)
    - API request schemas (FavoriteCreate, FavoriteUpdate, etc)
    """

    # Composite primary key (order matches schema: user_id, image_id)
    user_id: int = Field(foreign_key="users.user_id", primary_key=True)
    image_id: int = Field(foreign_key="images.image_id", primary_key=True)

    # Public timestamp
    fav_date: datetime = Field(sa_column_kwargs={"server_default": text('current_timestamp()')})

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
    __tablename__ = 'favorites'

    # NOTE: __table_args__ is partially redundant with Field(foreign_key=...) declarations below.
    # However, it's kept for explicit CASCADE behavior and named constraints that SQLModel's
    # Field() cannot express. Be aware: if using Alembic migrations to manage schema changes,
    # these definitions may drift from the actual database structure over time. When in doubt,
    # treat Alembic migrations as the source of truth for production schema.
    __table_args__ = (
        ForeignKeyConstraint(['image_id'], ['images.image_id'], ondelete='CASCADE', onupdate='CASCADE', name='fk_favorites_image_id'),
        ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE', onupdate='CASCADE', name='fk_favorites_user_id'),
        Index('fk_favorites_image_id', 'image_id')
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.