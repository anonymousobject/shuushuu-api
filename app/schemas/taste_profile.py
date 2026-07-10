"""Pydantic schemas for the private user taste-profile endpoint"""

from pydantic import BaseModel

from app.schemas.base import UTCDatetime


class TasteProfileTag(BaseModel):
    """One tag's evidence in a user's taste profile."""

    tag_id: int
    title: str | None
    type: int
    type_name: str
    pool_cnt: int
    fav_count: int
    upload_count: int
    rated_count: int
    rating_avg: float | None
    lift: float | None
    rating_delta: float | None
    affinity: float


class TasteProfileSummary(BaseModel):
    """Aggregate stats shown above the tag lists."""

    pool_size: int  # favorites ∪ uploads (deduped)
    rated_total: int
    mean_rating: float | None
    updated_at: UTCDatetime | None


class TasteProfileResponse(BaseModel):
    """Private analytics payload; owner-only."""

    profile_ready: bool
    summary: TasteProfileSummary | None = None
    top_tags: list[TasteProfileTag] = []
    rated_high: list[TasteProfileTag] = []
    rated_low: list[TasteProfileTag] = []
