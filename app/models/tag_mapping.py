"""
SQLModel-based TagMappings model.

Maps external (Danbooru-vocabulary) tag names emitted by ML taggers to
internal tag IDs. A row with internal_tag_id=NULL means "known but ignored"
(e.g. '1girl'); an absent row means unmapped (logged, then dropped).
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class TagMappingBase(SQLModel):
    """Shared public fields for tag mappings."""

    external_tag: str = Field(max_length=255)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TagMappings(TagMappingBase, table=True):
    """Database table mapping external tagger vocabulary to internal tags."""

    __tablename__ = "tag_mappings"

    __table_args__ = (UniqueConstraint("external_tag", name="unique_external_tag"),)

    mapping_id: int | None = Field(default=None, primary_key=True)

    internal_tag_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("tags.tag_id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(UtcDateTime, nullable=True, server_default=text("current_timestamp()")),
    )
