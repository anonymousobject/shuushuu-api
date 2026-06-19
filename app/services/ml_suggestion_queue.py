"""Queue service for ML tag suggestion review worklist.

Provides two async functions for the suggestion review queue:

- count_pending_by_tag: aggregate worklist counts per tag (for the tag list view)
- list_pending_for_tag: paginated pending suggestions for a single tag (for the
  per-tag review view)

Neither function fetches Image rows — the router assembles ImageResponse objects
after receiving the (suggestion_id, image_id, confidence) tuples from this layer.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags


async def count_pending_by_tag(
    db: AsyncSession,
    type_filter: int | None = None,
    min_confidence: float = 0.0,
) -> list[tuple[int, str | None, int, int]]:
    """Return pending suggestion counts grouped by tag.

    Joins MlTagSuggestions → Tags and aggregates only suggestions with
    status='pending' and confidence >= min_confidence.  When type_filter is
    not None, only tags with that type are included.

    Returns a list of (tag_id, title, type, pending_count) ordered by
    pending_count DESC.
    """
    stmt = (
        select(
            Tags.tag_id,
            Tags.title,
            Tags.type,
            func.count(MlTagSuggestions.suggestion_id).label("pending_count"),
        )
        .join(Tags, MlTagSuggestions.tag_id == Tags.tag_id)  # type: ignore[arg-type]
        .where(
            MlTagSuggestions.status == "pending",  # type: ignore[arg-type]
            MlTagSuggestions.confidence >= min_confidence,  # type: ignore[operator]
        )
        .group_by(Tags.tag_id, Tags.title, Tags.type)
        .order_by(func.count(MlTagSuggestions.suggestion_id).desc())
    )

    if type_filter is not None:
        stmt = stmt.where(Tags.type == type_filter)  # type: ignore[arg-type]

    result = await db.execute(stmt)
    rows = result.all()
    return [(row[0], row[1], row[2], row[3]) for row in rows]


async def list_pending_for_tag(
    db: AsyncSession,
    tag_id: int,
    min_confidence: float,
    page: int,
    per_page: int,
) -> tuple[list[tuple[int, int, float]], int]:
    """Return paginated pending suggestions for a single tag.

    Fetches MlTagSuggestions rows with status='pending', tag_id=tag_id, and
    confidence >= min_confidence, ordered by confidence DESC.  Pagination is
    1-based (page=1 is the first page).

    Returns (items, total) where:
    - items is a list of (suggestion_id, image_id, confidence)
    - total is the full count matching tag_id + min_confidence (before pagination)
    """
    base_filter = (
        MlTagSuggestions.status == "pending",  # type: ignore[arg-type]
        MlTagSuggestions.tag_id == tag_id,  # type: ignore[arg-type]
        MlTagSuggestions.confidence >= min_confidence,  # type: ignore[operator]
    )

    count_stmt = select(func.count(MlTagSuggestions.suggestion_id)).where(*base_filter)
    total: int = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * per_page
    items_stmt = (
        select(
            MlTagSuggestions.suggestion_id,
            MlTagSuggestions.image_id,
            MlTagSuggestions.confidence,
        )
        .where(*base_filter)
        .order_by(MlTagSuggestions.confidence.desc())  # type: ignore[union-attr]
        .offset(offset)
        .limit(per_page)
    )

    result = await db.execute(items_stmt)
    rows = result.all()
    items = [(row[0], row[1], row[2]) for row in rows]
    return items, total
