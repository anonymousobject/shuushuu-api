"""
SQLModel-based ImageMetadataHistory model for tracking edits to an image's
free-text metadata (miscmeta, source_url).

A public audit table, like image_status_history and deliberately separate
from AdminActions (the private moderation log): every row is shown, values
and editor included, on the image's history page and on the editor's profile
history. One row per changed field.
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKeyConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class ImageMetadataHistoryBase(SQLModel):
    """
    Base model with shared fields for ImageMetadataHistory.
    """

    image_id: int
    # An ImageMetadataField value
    field: str = Field(max_length=32)
    # None means unset (old_value) or cleared (new_value). 2000 fits the wider
    # of the two tracked columns, source_url.
    old_value: str | None = Field(default=None, max_length=2000)
    new_value: str | None = Field(default=None, max_length=2000)


class ImageMetadataHistory(ImageMetadataHistoryBase, table=True):
    """
    Database table for image metadata history.
    """

    __tablename__ = "image_metadata_history"

    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_metadata_history_image_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_image_metadata_history_user_id",
        ),
        Index("idx_image_metadata_history_image_id", "image_id"),
        Index("idx_image_metadata_history_user_id", "user_id"),
    )

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # Editor (nullable: the account may be deleted later)
    user_id: int | None = Field(default=None)

    # Timestamp
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(UtcDateTime, nullable=True, server_default=text("CURRENT_TIMESTAMP")),
    )
