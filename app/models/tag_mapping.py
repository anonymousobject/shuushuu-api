"""
Tag Mapping Model

Maps external tag vocabularies (e.g., Danbooru) to internal tags.
"""

from datetime import datetime, UTC
from typing import Optional

from sqlmodel import SQLModel, Field, Column
from sqlalchemy import Enum as SQLEnum, UniqueConstraint


class TagMapping(SQLModel, table=True):
    """
    Maps external tags (from Danbooru, etc.) to internal tags.

    If internal_tag_id is NULL, it means "ignore this external tag".
    """

    __tablename__ = "tag_mappings"
    __table_args__ = (
        UniqueConstraint('external_source', 'external_tag', name='uq_external_source_tag'),
    )

    mapping_id: Optional[int] = Field(default=None, primary_key=True)
    external_tag: str = Field(max_length=255)
    external_source: str = Field(
        sa_column=Column(
            SQLEnum('danbooru', 'other', name='external_source_enum'),
            nullable=False
        )
    )
    internal_tag_id: Optional[int] = Field(
        default=None,
        foreign_key="tags.tag_id",
        index=True
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="users.user_id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
