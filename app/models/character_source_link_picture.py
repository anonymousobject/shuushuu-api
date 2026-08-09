"""
SQLModel-based CharacterSourceLinkPicture model.

One representative picture per character-source link: an existing image plus a
normalized square crop rectangle (fractions of the image's natural dimensions).
The rect is the source of truth — rendering is CSS math today and could become
server-generated crop files later without data loss.
"""

from datetime import UTC, datetime

from sqlalchemy import Column, ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class CharacterSourceLinkPictureBase(SQLModel):
    """Shared public fields for a link's representative picture."""

    image_id: int
    crop_x: float
    crop_y: float
    crop_w: float
    crop_h: float


class CharacterSourceLinkPictures(CharacterSourceLinkPictureBase, table=True):
    """
    Database table for character-source link pictures (1:1 with links).

    Deleting the link or the image deletes the picture whole (CASCADE both
    ways) — no half-null states.
    """

    __tablename__ = "character_source_link_pictures"

    __table_args__ = (
        ForeignKeyConstraint(
            ["link_id"],
            ["character_source_links.id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_cslink_pictures_link_id",
        ),
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_cslink_pictures_image_id",
        ),
        ForeignKeyConstraint(
            ["set_by_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_cslink_pictures_set_by_user_id",
        ),
        Index("idx_cslink_pictures_image_id", "image_id"),
    )

    # 1:1 with the link — the link id IS the primary key.
    # FK constraints (with CASCADE) live in __table_args__; do NOT add
    # foreign_key= to the Fields (duplicates the FK without CASCADE).
    link_id: int = Field(primary_key=True)

    set_by_user_id: int | None = Field(default=None)

    set_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(UtcDateTime, nullable=False, server_default=text("current_timestamp()")),
    )

    # Relationships intentionally omitted (house style — see
    # character_source_link.py's closing comment).
