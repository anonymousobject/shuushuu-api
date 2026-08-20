"""
SQLModel-based user favorite tag/link models.

MAL-style public profile favorites. Characters are favorited per
(character, source) LINK — a row references character_source_links, so
the same character from two sources is two distinct favoritable combos.
Sources and artists are favorited as plain tags; the category derives
from tags.type (validated SOURCE/ARTIST on write, no stored kind).
CASCADE from users/tags/links means favorites clean themselves up.
"""

from datetime import UTC, datetime

from sqlalchemy import Column, ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class UserFavoriteLinks(SQLModel, table=True):
    """A user's favorite character combos, ordered by position."""

    __tablename__ = "user_favorite_links"

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_user_favorite_links_user_id",
        ),
        ForeignKeyConstraint(
            ["link_id"],
            ["character_source_links.id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_user_favorite_links_link_id",
        ),
        Index("idx_user_favorite_links_link_id", "link_id"),
    )

    # Composite PK; FK constraints (with CASCADE) live in __table_args__ —
    # do NOT add foreign_key= to the Fields (duplicates the FK sans CASCADE).
    user_id: int = Field(primary_key=True)
    link_id: int = Field(primary_key=True)

    # 0-based within the user's characters list. Gaps after deletes are
    # harmless (order-by stays correct); reorder rewrites 0..n-1.
    position: int

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(UtcDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    )

    # Relationships intentionally omitted (house style).


class UserFavoriteTags(SQLModel, table=True):
    """A user's favorite source/artist tags, ordered by position per type."""

    __tablename__ = "user_favorite_tags"

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_user_favorite_tags_user_id",
        ),
        ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_user_favorite_tags_tag_id",
        ),
        Index("idx_user_favorite_tags_tag_id", "tag_id"),
    )

    user_id: int = Field(primary_key=True)
    tag_id: int = Field(primary_key=True)

    # 0-based within that tag-type's list (sources and artists order
    # independently even though they share this table).
    position: int

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(UtcDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    )
