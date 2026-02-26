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
from enum import Enum

from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

# ===== Banners =====


class BannerSize(str, Enum):
    """Banner size variants."""

    small = "small"
    large = "large"


class BannerBase(SQLModel):
    """
    Base model with shared public fields for Banners.

    These fields are safe to expose via the API.
    """

    name: str = Field(max_length=255)
    author: str | None = Field(default=None, max_length=255)

    size: BannerSize = Field(default=BannerSize.small)

    # Image paths (relative to banner directory)
    full_image: str | None = Field(default=None, max_length=255, unique=True)
    left_image: str | None = Field(default=None, max_length=255)
    middle_image: str | None = Field(default=None, max_length=255)
    right_image: str | None = Field(default=None, max_length=255)

    # Theme compatibility
    supports_dark: bool = Field(default=True)
    supports_light: bool = Field(default=True)

    # State
    active: bool = Field(default=True)


class Banners(BannerBase, table=True):
    """
    Database table for site banners.

    Banners are displayed at the top of the site and can be themed or event-specific.
    """

    __tablename__ = "banners"

    # Primary key
    banner_id: int | None = Field(default=None, primary_key=True)

    # Timestamp
    created_at: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )


class BannerTheme(str, Enum):
    """Banner theme variants."""

    dark = "dark"
    light = "light"


class UserBannerPreferencesBase(SQLModel):
    """Base model for user banner size preference."""

    preferred_size: BannerSize = Field(default=BannerSize.small)


class UserBannerPreferences(UserBannerPreferencesBase, table=True):
    """One row per user — stores preferred banner size."""

    __tablename__ = "user_banner_preferences"

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_user_banner_prefs_user_id",
        ),
    )

    user_id: int = Field(primary_key=True, foreign_key="users.user_id")


class UserBannerPinsBase(SQLModel):
    """Base model for user banner pins."""

    size: BannerSize
    theme: BannerTheme


class UserBannerPins(UserBannerPinsBase, table=True):
    """One row per pin — up to 4 per user (2 sizes x 2 themes)."""

    __tablename__ = "user_banner_pins"

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_user_banner_pins_user_id",
        ),
        ForeignKeyConstraint(
            ["banner_id"],
            ["banners.banner_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_user_banner_pins_banner_id",
        ),
        Index("uq_user_size_theme", "user_id", "size", "theme", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.user_id")
    banner_id: int = Field(foreign_key="banners.banner_id")


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


# ===== Donations =====


class DonationBase(SQLModel):
    """
    Base model for Donations table.

    This is a simple tracking table with no foreign key relationships.
    Note: Original table has no primary key, but we need one for SQLModel.
    """

    date: datetime = Field(sa_column_kwargs={"server_default": text("current_timestamp()")})
    user_id: int | None = Field(default=None)
    nick: str | None = Field(default=None, max_length=30)
    amount: int | None = Field(default=None)


class Donations(DonationBase, table=True):
    """
    Database table for tracking donations.

    Note: This table uses a composite of all columns as the natural key since
    the original schema has no primary key. For SQLModel, we add an ID column.
    """

    __tablename__ = "donations"

    __table_args__ = (Index("idx_date", "date"),)

    # SQLModel requires a primary key, but original table has none
    # We'll make this auto-increment for new inserts
    id: int | None = Field(default=None, primary_key=True, exclude=True)


# ===== Image Ratings Average =====


class ImageRatingsAvgBase(SQLModel):
    """
    Base model for Image Ratings Average table.

    Stores aggregated rating statistics by type.
    """

    type: str | None = Field(default=None, max_length=3)
    avg: float | None = Field(default=None)


class ImageRatingsAvg(ImageRatingsAvgBase, table=True):
    """
    Database table for aggregated image rating statistics.

    This table stores average ratings grouped by type.
    """

    __tablename__ = "image_ratings_avg"

    # Note: Original table has no primary key
    # We'll use type as a unique identifier for SQLModel
    type: str = Field(primary_key=True, max_length=3)


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
