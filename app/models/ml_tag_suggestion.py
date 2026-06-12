"""
SQLModel-based MlTagSuggestions model.

Stores ML-generated tag suggestions awaiting human review. Distinct from
ImageReportTagSuggestions, which stores human suggestions filed via reports.

Lifecycle: pipeline inserts status='pending' → reviewer approves (TagLink
created) or rejects. Regeneration resets approved rows to pending when their
tag has been removed from the image.
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKey, Index, Integer, UniqueConstraint, text
from sqlalchemy import Enum as SQLEnum
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class MlTagSuggestionBase(SQLModel):
    """Shared public fields for ML tag suggestions."""

    image_id: int
    tag_id: int
    confidence: float = Field(ge=0.0, le=1.0)
    model_version: str = Field(max_length=100)  # e.g. "wd-swinv2-tagger-v3"


class MlTagSuggestions(MlTagSuggestionBase, table=True):
    """Database table for ML-generated tag suggestions."""

    __tablename__ = "ml_tag_suggestions"

    __table_args__ = (
        UniqueConstraint("image_id", "tag_id", name="unique_ml_suggestion_image_tag"),
        Index("idx_ml_suggestion_status", "status"),
    )

    suggestion_id: int | None = Field(default=None, primary_key=True)

    image_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("images.image_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )
    tag_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("tags.tag_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )
    status: str = Field(
        default="pending",
        sa_column=Column(
            SQLEnum("pending", "approved", "rejected", name="ml_suggestion_status"),
            nullable=False,
        ),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(UtcDateTime, nullable=True, server_default=text("current_timestamp()")),
    )
    reviewed_at: datetime | None = Field(default=None, sa_column=Column(UtcDateTime, nullable=True))
    reviewed_by_user_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("users.user_id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
    )
