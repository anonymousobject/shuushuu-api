"""SQLModel for the precomputed per-user tag-affinity table (taste profiles)."""

from datetime import datetime

from sqlalchemy import Column, Float, Index, Integer, text
from sqlmodel import Field, SQLModel

from app.models.types import UnsignedInt, UtcDateTime


class UserTagAffinity(SQLModel, table=True):
    """Per-(user, tag) taste evidence + blended affinity score.

    Rebuilt nightly by refresh_user_tag_affinity via atomic staging-table swap;
    treat as read-only outside the refresh job. No FKs by design (same rationale
    as tag_cooccurrence: full rebuild maintains consistency, and
    CREATE TABLE ... LIKE would silently drop FKs after the first swap anyway).
    Only rows meeting min support are stored: pool_cnt >= TASTE_MIN_SUPPORT or
    rated_count >= TASTE_MIN_SUPPORT.
    """

    __tablename__ = "user_tag_affinity"

    __table_args__ = (Index("idx_user_tag_affinity_lookup", "user_id", "affinity"),)

    user_id: int = Field(sa_column=Column(UnsignedInt, primary_key=True, nullable=False))
    tag_id: int = Field(sa_column=Column(UnsignedInt, primary_key=True, nullable=False))
    # positive pool = favorites ∪ uploads, deduped; pool_cnt is the lift support
    pool_cnt: int = Field(sa_column=Column(Integer, nullable=False))
    fav_count: int = Field(sa_column=Column(Integer, nullable=False))
    upload_count: int = Field(sa_column=Column(Integer, nullable=False))
    rated_count: int = Field(sa_column=Column(Integer, nullable=False))
    rating_avg: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    # smoothed pool-share vs global-share; NULL when the user has no positive pool
    lift: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    # rating_avg minus the user's overall mean rating; NULL when unrated
    rating_delta: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    affinity: float = Field(sa_column=Column(Float, nullable=False))
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(UtcDateTime, nullable=True, server_default=text("current_timestamp()")),
    )
