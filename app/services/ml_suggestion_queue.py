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
from sqlalchemy.orm import aliased

from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_link import TagLinks

# Anti-join: excludes suggestions whose tag is already applied to the image.
# Tags can be applied without going through the review flow (manual tag add,
# batch tagging, report resolution) — those paths leave the suggestion row
# status='pending', so status alone cannot tell us the work is done.
_TAG_NOT_ALREADY_APPLIED = ~(
    select(TagLinks.tag_id)  # type: ignore[call-overload]
    .where(
        TagLinks.image_id == MlTagSuggestions.image_id,
        TagLinks.tag_id == MlTagSuggestions.tag_id,
    )
    .exists()
)


async def _descendant_tag_ids(db: AsyncSession, tag_id: int) -> set[int]:
    """All tags whose inheritedfrom chain leads to ``tag_id`` (children,
    grandchildren, ...), excluding ``tag_id`` itself. Breadth-first with the
    same depth cap the pipeline's ancestor walks use."""
    descendants: set[int] = set()
    frontier = {tag_id}
    for _ in range(10):
        rows = (
            await db.execute(
                select(Tags.tag_id).where(  # type: ignore[call-overload]
                    Tags.inheritedfrom_id.in_(frontier)  # type: ignore[union-attr]
                )
            )
        ).all()
        new_ids = {row[0] for row in rows} - descendants
        if not new_ids:
            break
        descendants |= new_ids
        frontier = new_ids
    return descendants


async def count_pending_by_tag(
    db: AsyncSession,
    type_filter: int | None = None,
    min_confidence: float = 0.0,
    page: int = 1,
    per_page: int = 50,
    search: str | None = None,
) -> tuple[list[tuple[int, str | None, int, int]], int]:
    """Return paginated pending suggestion counts grouped by tag.

    Joins MlTagSuggestions → Tags and aggregates only suggestions with
    status='pending' and confidence >= min_confidence.  When type_filter is
    not None, only tags with that type are included.

    Unlike list_pending_for_tag, this does NOT exclude suggestions whose tag
    is already applied to the image: the anti-join over every pending row was
    measured at 7-11s on production-sized data (vs ~0.9s without), so counts
    may run slightly ahead of what the per-tag grid actually shows. For the
    same accepted-overcount/perf reason, it likewise does not exclude rows
    hidden by list_pending_for_tag's descendant-pending check.

    When search is provided, only tags whose title contains the search string
    (case-insensitive LIKE %search%) are included.

    Returns (items, total) where:
    - items is a list of (tag_id, title, type, pending_count) for the
      requested page, ordered by pending_count DESC
    - total is the count of DISTINCT tags matching the filters (before
      pagination), used for building pagination controls
    """
    base_filters = [
        MlTagSuggestions.status == "pending",
        MlTagSuggestions.confidence >= min_confidence,
    ]
    if type_filter is not None:
        base_filters.append(Tags.type == type_filter)
    if search is not None:
        base_filters.append(Tags.title.ilike(f"%{search}%"))  # type: ignore[union-attr]

    # Total count of DISTINCT tags matching filters (used for pagination metadata).
    total_stmt = (
        select(func.count(func.distinct(MlTagSuggestions.tag_id)))
        .join(Tags, MlTagSuggestions.tag_id == Tags.tag_id)  # type: ignore[arg-type]
        .where(*base_filters)  # type: ignore[arg-type]
    )
    total: int = (await db.execute(total_stmt)).scalar_one()

    offset = (page - 1) * per_page
    items_stmt = (
        select(  # type: ignore[call-overload]
            Tags.tag_id,
            Tags.title,
            Tags.type,
            func.count(MlTagSuggestions.suggestion_id).label("pending_count"),  # type: ignore[arg-type]
        )
        .join(Tags, MlTagSuggestions.tag_id == Tags.tag_id)
        .where(*base_filters)
        .group_by(Tags.tag_id, Tags.title, Tags.type)
        .order_by(func.count(MlTagSuggestions.suggestion_id).desc())  # type: ignore[arg-type]
        .offset(offset)
        .limit(per_page)
    )

    result = await db.execute(items_stmt)
    rows = result.all()
    items = [(row[0], row[1], row[2], row[3]) for row in rows]
    return items, total


async def list_pending_for_tag(
    db: AsyncSession,
    tag_id: int,
    min_confidence: float,
    page: int,
    per_page: int,
) -> tuple[list[tuple[int, int, float]], int]:
    """Return paginated pending suggestions for a single tag.

    Fetches MlTagSuggestions rows with status='pending', tag_id=tag_id, and
    confidence >= min_confidence, ordered by confidence DESC, excluding
    suggestions whose tag is already applied to the image (see
    _TAG_NOT_ALREADY_APPLIED).  Also excludes suggestions on images that
    still have a pending suggestion for a DESCENDANT of ``tag_id`` — per-tag
    review is most-specific-first; rejecting the descendant resurfaces the
    ancestor here.  Pagination is 1-based (page=1 is the first page).

    Returns (items, total) where:
    - items is a list of (suggestion_id, image_id, confidence)
    - total is the full count matching tag_id + min_confidence (before pagination)
    """
    descendant_ids = await _descendant_tag_ids(db, tag_id)

    base_filter = [
        MlTagSuggestions.status == "pending",
        MlTagSuggestions.tag_id == tag_id,
        MlTagSuggestions.confidence >= min_confidence,
        _TAG_NOT_ALREADY_APPLIED,
    ]
    if descendant_ids:
        descendant_pending = aliased(MlTagSuggestions)
        base_filter.append(
            ~(
                select(descendant_pending.suggestion_id)  # type: ignore[call-overload]
                .where(
                    descendant_pending.image_id == MlTagSuggestions.image_id,
                    descendant_pending.status == "pending",
                    descendant_pending.tag_id.in_(descendant_ids),  # type: ignore[attr-defined]
                )
                .exists()
            )
        )

    count_stmt = select(func.count(MlTagSuggestions.suggestion_id)).where(*base_filter)  # type: ignore[arg-type]
    total: int = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * per_page
    items_stmt = (
        select(  # type: ignore[call-overload]
            MlTagSuggestions.suggestion_id,
            MlTagSuggestions.image_id,
            MlTagSuggestions.confidence,
        )
        .where(*base_filter)
        .order_by(MlTagSuggestions.confidence.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(per_page)
    )

    result = await db.execute(items_stmt)
    rows = result.all()
    items = [(row[0], row[1], row[2]) for row in rows]
    return items, total
