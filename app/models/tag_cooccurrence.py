"""SQLModel for precomputed tag co-occurrence (top-N related tags per tag)."""

from sqlalchemy import Column, Float, Index, Integer, SmallInteger
from sqlmodel import Field, SQLModel

from app.models.types import UnsignedInt


class TagCooccurrence(SQLModel, table=True):
    """Top-N co-occurring tags per base tag, refreshed by the weekly batch.

    Rows are directional (base `tag_id` -> `related_tag_id`): confidence depends
    on the base; lift is symmetric. Written via atomic staging-table swap, so
    treat this as read-only outside the refresh job.

    No FKs by design: referential consistency is maintained by the weekly full
    rebuild (atomic swap), not `ON DELETE CASCADE`. (`CREATE TABLE ... LIKE`,
    used to build the staging table, does not copy FKs anyway, so declaring them
    here would silently diverge from the runtime table after the first swap.)
    Transient orphaned rows from tag deletes between rebuilds are harmless: the
    `/tags/{id}/related` endpoint inner-joins `tags`, so orphaned related rows
    are filtered out and a deleted base tag is never queried.
    """

    __tablename__ = "tag_cooccurrence"

    __table_args__ = (Index("idx_tag_cooccurrence_lookup", "tag_id", "lift"),)

    tag_id: int = Field(sa_column=Column(UnsignedInt, primary_key=True, nullable=False))
    related_tag_id: int = Field(sa_column=Column(UnsignedInt, primary_key=True, nullable=False))
    related_type: int = Field(sa_column=Column(SmallInteger, nullable=False))
    cooccur_count: int = Field(sa_column=Column(Integer, nullable=False))
    lift: float = Field(sa_column=Column(Float, nullable=False))
    confidence: float = Field(sa_column=Column(Float, nullable=False))
