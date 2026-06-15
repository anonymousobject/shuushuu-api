"""SQLModel for precomputed tag co-occurrence (top-N related tags per tag)."""

from sqlalchemy import Column, Float, ForeignKeyConstraint, Index, Integer, SmallInteger
from sqlmodel import Field, SQLModel

from app.models.types import UnsignedInt


class TagCooccurrence(SQLModel, table=True):
    """Top-N co-occurring tags per base tag, refreshed by the weekly batch.

    Rows are directional (base `tag_id` -> `related_tag_id`): confidence depends
    on the base; lift is symmetric. Written via atomic staging-table swap, so
    treat this as read-only outside the refresh job.
    """

    __tablename__ = "tag_cooccurrence"

    __table_args__ = (
        ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_tag_cooccurrence_tag_id",
        ),
        ForeignKeyConstraint(
            ["related_tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_tag_cooccurrence_related_tag_id",
        ),
        Index("idx_tag_cooccurrence_lookup", "tag_id", "lift"),
    )

    tag_id: int = Field(sa_column=Column(UnsignedInt, primary_key=True, nullable=False))
    related_tag_id: int = Field(sa_column=Column(UnsignedInt, primary_key=True, nullable=False))
    related_type: int = Field(sa_column=Column(SmallInteger, nullable=False))
    cooccur_count: int = Field(sa_column=Column(Integer, nullable=False))
    lift: float = Field(sa_column=Column(Float, nullable=False))
    confidence: float = Field(sa_column=Column(Float, nullable=False))
