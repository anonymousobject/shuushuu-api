"""
SQLModel-based miscellaneous utility models

This module defines various utility database models using SQLModel:
- Banners: Site banner images
- EvaTheme: Eva-themed content/themes
- Tips: Helpful tips displayed to users
- Donations: Table tracking donations (no relations)

These are generally simple utility tables with minimal relationships.
"""

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

# ===== Banners =====


class BannerBase(SQLModel):
    """
    Base model with shared public fields for Banners.

    These fields are safe to expose via the API.
    """

    path: str = Field(default="", max_length=255)
    author: str = Field(default="", max_length=255)
    leftext: str = Field(default="png", max_length=3)
    midext: str = Field(default="png", max_length=3)
    rightext: str = Field(default="png", max_length=3)
    full: int = Field(default=0)
    event_id: int = Field(default=0)
    active: int = Field(default=1)
    date: datetime | None = Field(default=None)


class Banners(BannerBase, table=True):
    """
    Database table for site banners.

    Banners are displayed at the top of the site and can be themed or event-specific.
    """

    __tablename__ = "banners"

    # Primary key
    banner_id: int | None = Field(default=None, primary_key=True)

    # Override to add server default
    date: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )


# ===== EvaTheme =====


class EvaThemeBase(SQLModel):
    """
    Base model with shared public fields for EvaTheme.

    These fields are safe to expose via the API.
    """

    theme_name: str = Field(default="", max_length=255)
    banner: str = Field(default="", max_length=255)
    theme_content: str | None = Field(default=None)

    # Active date range (month and day)
    active_month_from: int = Field(default=0)
    active_month_to: int = Field(default=0)
    active_day_from: int = Field(default=0)
    active_day_to: int = Field(default=0)
    active: int = Field(default=0)


class EvaTheme(EvaThemeBase, table=True):
    """
    Database table for Eva-themed content.

    Stores seasonal/event themes that activate during specific date ranges.
    """

    __tablename__ = "eva_theme"

    # Primary key
    theme_id: int | None = Field(default=None, primary_key=True)


# ===== Tips =====


class TipBase(SQLModel):
    """
    Base model with shared public fields for Tips.

    These fields are safe to expose via the API.
    """

    tip: str | None = Field(default=None, max_length=255)
    type: int = Field(default=0)


class Tips(TipBase, table=True):
    """
    Database table for user tips.

    Tips are helpful messages displayed to users throughout the site.
    """

    __tablename__ = "tips"

    # Primary key
    id: int | None = Field(default=None, primary_key=True)


# ===== Donations (Table without model - view only) =====


class DonationBase(SQLModel):
    """
    Base model for Donations table.

    This is a simple tracking table with no foreign key relationships.
    """

    date: datetime
    user_id: int | None = Field(default=None)
    nick: str | None = Field(default=None, max_length=30)
    amount: int | None = Field(default=None)


# Note: Donations table has no primary key in the schema, so we'll treat it as a view
# If you need to insert/update, you may want to add an auto-increment ID column via migration


# ===== Quicklinks =====


class QuicklinkBase(SQLModel):
    """
    Base model with shared public fields for Quicklinks.

    Quicklinks are user-specific saved links or shortcuts.
    """

    user_id: int | None = Field(default=None)
    link: str | None = Field(default=None, max_length=32)


class Quicklinks(QuicklinkBase, table=True):
    """
    Database table for user quicklinks.
    """

    __tablename__ = "quicklinks"

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_quicklinks_user_id",
        ),
        Index("fk_quicklinks_user_id", "user_id"),
    )

    # Primary key
    quicklink_id: int | None = Field(default=None, primary_key=True)

    # Override to add foreign key
    user_id: int | None = Field(default=None, foreign_key="users.user_id")

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
