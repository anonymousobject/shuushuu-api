"""
User History API endpoints.

Provides aggregated history of all changes made by a user:
- Tag metadata changes (rename, type_change, alias, parent, source links)
- Tag usage (tag add/remove on images) — merges tag_links (upload-time adds,
  which never get a tag_history row) with tag_history (edit-flow adds and
  all removes)
- Status changes (only visible statuses: REPOST, SPOILER, ACTIVE)
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import ColumnElement, desc, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams
from app.config import ImageStatus
from app.core.database import get_db
from app.models.image_status_history import ImageStatusHistory
from app.models.tag import Tags
from app.models.tag_audit_log import TagAuditLog
from app.models.tag_history import TagHistory
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.schemas.audit import UserHistoryItem, UserHistoryListResponse
from app.schemas.tag import LinkedTag

router = APIRouter(prefix="/users", tags=["history"])

_STATUS_VISIBILITY_FILTER = or_(
    ImageStatusHistory.old_status.in_(ImageStatus.VISIBLE_USER_STATUSES),  # type: ignore[attr-defined]
    ImageStatusHistory.new_status.in_(ImageStatus.VISIBLE_USER_STATUSES),  # type: ignore[attr-defined]
)


def _user_history_tag_history_dedup_filter(user_id: int) -> ColumnElement[bool]:
    """User-scoped variant of the tag endpoint's usage-history dedup rule.

    Drops a tag_history 'a' row only if the path user's *own* tag_links row
    still exists for that (tag, image) — i.e. the EXISTS also requires
    `tag_links.user_id == user_id`. This deliberately differs from
    `_TAG_HISTORY_DEDUP_FILTER` in app.api.v1.tags, which matches any user's
    link: a history row only duplicates a link *within this user's feed* if
    the link is theirs. Without the user match, user X's add-history row
    would be dropped whenever the image's *current* link belongs to a
    different user (X added, someone removed it, Y re-added it) — X's own
    activity would silently vanish from their own history.
    """
    exists_own_link = (
        select(TagLinks.tag_id)  # type: ignore[call-overload]
        .where(
            TagLinks.tag_id == TagHistory.tag_id,
            TagLinks.image_id == TagHistory.image_id,
            TagLinks.user_id == user_id,
        )
        .exists()
    )
    return or_(
        TagHistory.action.is_distinct_from("a"),  # type: ignore[union-attr]
        ~exists_own_link,
    )


def _user_history_union(user_id: int, offset: int, per_page: int) -> Any:
    """Paginated UNION ALL of a user's four history sources.

    Each branch projects the same (kind, id_a, id_b, ts, prio, tiebreak)
    identity tuple only — not full row data — so hydration happens as a
    page-scoped follow-up per kind (see the hydration helpers below). `kind`
    identifies the source (1=audit, 2=tag_history, 3=tag_links,
    4=status_history); id_a/id_b carry enough identity to re-fetch the row
    (tag_links needs both, having a composite PK and no surrogate id);
    `prio` reproduces today's per-type ordering (status > tag usage > audit
    on a timestamp tie); `tiebreak` is a stable secondary sort within a
    branch. `kind` sits between `prio` and `tiebreak` in the outer ORDER BY
    because kinds 2 and 3 share prio 2 but draw tiebreaks from unrelated id
    spaces (tag_history_id vs image_id) — for every other kind, prio already
    implies kind, so ordering is otherwise unchanged from before.

    `id_a` is appended as the FINAL outer sort key, after `tiebreak`. For
    kinds 1/2/4, id_a *is* tiebreak (same column projected twice), so this
    is a no-op there. It is not a no-op for kind 3: tag_links' primary key
    is (tag_id, image_id), and tiebreak is image_id, so two links on the
    *same image* (an upload tagging N tags at once — see
    app.services.upload.link_tags_to_image's per-tag TagLinks loop, which
    leaves date_linked at its shared server-default timestamp for every row
    in the batch) collide on (ts, prio, kind, tiebreak) with only tag_id
    (id_a) distinguishing them. An unbroken tie there is not just cosmetically
    nondeterministic: the per-branch LIMIT below picks a different arbitrary
    subset of the tied group depending on `offset + per_page`, which differs
    per page, so paginating could silently duplicate or drop a link.

    Each branch pushes its own ORDER BY + LIMIT (offset + per_page) *before*
    the union — required, not an optimization, same rationale as
    _tag_usage_history_union in app.api.v1.tags: MariaDB materializes UNION
    ALL as a derived table, so an outer-only ORDER BY would filesort the
    full merged set (1.13M rows for the hottest user) regardless of indexes.
    This is lossless: the global top (offset + per_page) rows are
    necessarily contained in each branch's own top (offset + per_page) rows.
    The kind-3 branch's own ORDER BY carries the same tag_id tie-break as
    the outer query (its prio/kind are constant within the branch, so
    date_linked, image_id, tag_id is the branch-local restriction of the
    outer ts/tiebreak/id_a order) — required to keep that pushdown lossless
    too; the other three branches don't need it since id_a already equals
    their tiebreak.
    """
    branch_limit = offset + per_page

    audit_branch = (
        select(
            literal(1).label("kind"),
            TagAuditLog.id.label("id_a"),  # type: ignore[union-attr]
            literal(0).label("id_b"),
            TagAuditLog.created_at.label("ts"),  # type: ignore[union-attr]
            literal(1).label("prio"),
            TagAuditLog.id.label("tiebreak"),  # type: ignore[union-attr]
        )
        .where(TagAuditLog.user_id == user_id)  # type: ignore[arg-type]
        .order_by(desc(TagAuditLog.created_at), desc(TagAuditLog.id))  # type: ignore[arg-type]
        .limit(branch_limit)
    )

    history_branch = (
        select(
            literal(2).label("kind"),
            TagHistory.tag_history_id.label("id_a"),  # type: ignore[union-attr]
            literal(0).label("id_b"),
            TagHistory.date.label("ts"),  # type: ignore[union-attr]
            literal(2).label("prio"),
            TagHistory.tag_history_id.label("tiebreak"),  # type: ignore[union-attr]
        )
        .where(TagHistory.user_id == user_id)  # type: ignore[arg-type]
        .where(_user_history_tag_history_dedup_filter(user_id))
        .order_by(desc(TagHistory.date), desc(TagHistory.tag_history_id))  # type: ignore[arg-type]
        .limit(branch_limit)
    )

    link_branch = (
        select(
            literal(3).label("kind"),
            TagLinks.tag_id.label("id_a"),  # type: ignore[attr-defined]
            TagLinks.image_id.label("id_b"),  # type: ignore[attr-defined]
            TagLinks.date_linked.label("ts"),  # type: ignore[union-attr]
            literal(2).label("prio"),
            TagLinks.image_id.label("tiebreak"),  # type: ignore[attr-defined]
        )
        .where(TagLinks.user_id == user_id)  # type: ignore[arg-type]
        .order_by(
            desc(TagLinks.date_linked),  # type: ignore[arg-type]
            desc(TagLinks.image_id),  # type: ignore[arg-type]
            desc(TagLinks.tag_id),  # type: ignore[arg-type]
        )
        .limit(branch_limit)
    )

    status_branch = (
        select(
            literal(4).label("kind"),
            ImageStatusHistory.id.label("id_a"),  # type: ignore[union-attr]
            literal(0).label("id_b"),
            ImageStatusHistory.created_at.label("ts"),  # type: ignore[union-attr]
            literal(3).label("prio"),
            ImageStatusHistory.id.label("tiebreak"),  # type: ignore[union-attr]
        )
        .where(ImageStatusHistory.user_id == user_id)  # type: ignore[arg-type]
        .where(_STATUS_VISIBILITY_FILTER)
        .order_by(desc(ImageStatusHistory.created_at), desc(ImageStatusHistory.id))  # type: ignore[arg-type]
        .limit(branch_limit)
    )

    merged = union_all(audit_branch, history_branch, link_branch, status_branch).subquery()
    return (
        select(merged)
        .order_by(
            desc(merged.c.ts),
            desc(merged.c.prio),
            desc(merged.c.kind),
            desc(merged.c.tiebreak),
            desc(merged.c.id_a),
        )
        .limit(per_page)
        .offset(offset)
    )


async def _user_history_total(db: AsyncSession, user_id: int) -> int:
    """Total history events for a user: sum of four plain COUNTs.

    Deliberately not COUNT(*) over the union subquery — same tradeoff as
    _tag_usage_history_total in app.api.v1.tags (measured ~1.37s vs 98ms for
    the hottest user, since MariaDB materializes the union before it can
    count it).
    """
    audit_total = (
        await db.execute(
            select(func.count()).select_from(TagAuditLog).where(TagAuditLog.user_id == user_id)  # type: ignore[arg-type]
        )
    ).scalar() or 0
    history_total = (
        await db.execute(
            select(func.count())
            .select_from(TagHistory)
            .where(TagHistory.user_id == user_id)  # type: ignore[arg-type]
            .where(_user_history_tag_history_dedup_filter(user_id))
        )
    ).scalar() or 0
    link_total = (
        await db.execute(
            select(func.count()).select_from(TagLinks).where(TagLinks.user_id == user_id)  # type: ignore[arg-type]
        )
    ).scalar() or 0
    status_total = (
        await db.execute(
            select(func.count())
            .select_from(ImageStatusHistory)
            .where(ImageStatusHistory.user_id == user_id)  # type: ignore[arg-type]
            .where(_STATUS_VISIBILITY_FILTER)
        )
    ).scalar() or 0
    return audit_total + history_total + link_total + status_total


async def _hydrate_tag_metadata_items(
    db: AsyncSession, ids: list[int]
) -> dict[int, UserHistoryItem]:
    """Load kind-1 (tag_metadata) rows for the given TagAuditLog ids.

    Mirrors the pre-union handler's transform, but the linked_tags_map batch
    (for alias/parent/character/source tags) is now built only from this
    page's audit rows instead of the whole user's history.
    """
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(TagAuditLog, Tags)
            .outerjoin(Tags, TagAuditLog.tag_id == Tags.tag_id)  # type: ignore[arg-type]
            .where(TagAuditLog.id.in_(ids))  # type: ignore[union-attr]
        )
    ).all()

    linked_tag_ids: set[int] = set()
    for audit_log, _ in rows:
        for fk in (
            audit_log.old_alias_of,
            audit_log.new_alias_of,
            audit_log.old_parent_id,
            audit_log.new_parent_id,
            audit_log.character_tag_id,
            audit_log.source_tag_id,
        ):
            if fk:
                linked_tag_ids.add(fk)

    linked_tags_map: dict[int, LinkedTag] = {}
    if linked_tag_ids:
        linked_tags_result = await db.execute(
            select(Tags.tag_id, Tags.title, Tags.type).where(  # type: ignore[call-overload]
                Tags.tag_id.in_(linked_tag_ids)  # type: ignore[union-attr]
            )
        )
        linked_tags_map = {
            row[0]: LinkedTag(tag_id=row[0], title=row[1], type=row[2])
            for row in linked_tags_result.all()
        }

    items: dict[int, UserHistoryItem] = {}
    for audit_log, tag in rows:
        tag_info = LinkedTag(tag_id=tag.tag_id, title=tag.title, type=tag.type) if tag else None
        alias_target_id = audit_log.new_alias_of or audit_log.old_alias_of
        parent_target_id = audit_log.new_parent_id or audit_log.old_parent_id
        items[audit_log.id] = UserHistoryItem(
            type="tag_metadata",
            action_type=audit_log.action_type,
            tag=tag_info,
            old_title=audit_log.old_title,
            new_title=audit_log.new_title,
            old_type=audit_log.old_type,
            new_type=audit_log.new_type,
            old_desc=audit_log.old_desc,
            new_desc=audit_log.new_desc,
            created_at=audit_log.created_at,
            alias_tag=linked_tags_map.get(alias_target_id) if alias_target_id else None,
            parent_tag=linked_tags_map.get(parent_target_id) if parent_target_id else None,
            character_tag=(
                linked_tags_map.get(audit_log.character_tag_id)
                if audit_log.character_tag_id and audit_log.source_tag_id
                else None
            ),
            source_tag=(
                linked_tags_map.get(audit_log.source_tag_id)
                if audit_log.character_tag_id and audit_log.source_tag_id
                else None
            ),
            link_url=audit_log.link_url,
            old_archive_url=audit_log.old_archive_url,
            new_archive_url=audit_log.new_archive_url,
        )
    return items


async def _hydrate_tag_history_usage_items(
    db: AsyncSession, ids: list[int]
) -> dict[int, UserHistoryItem]:
    """Load kind-2 (tag_usage, from tag_history) rows for the given ids."""
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(TagHistory, Tags)
            .outerjoin(Tags, TagHistory.tag_id == Tags.tag_id)  # type: ignore[arg-type]
            .where(TagHistory.tag_history_id.in_(ids))  # type: ignore[union-attr]
        )
    ).all()
    items: dict[int, UserHistoryItem] = {}
    for history, tag in rows:
        tag_info = LinkedTag(tag_id=tag.tag_id, title=tag.title, type=tag.type) if tag else None
        items[history.tag_history_id] = UserHistoryItem(
            type="tag_usage",
            action="added" if history.action == "a" else "removed",
            tag=tag_info,
            image_id=history.image_id,
            date=history.date,
        )
    return items


async def _hydrate_status_change_items(
    db: AsyncSession, ids: list[int]
) -> dict[int, UserHistoryItem]:
    """Load kind-4 (status_change) rows for the given ImageStatusHistory ids."""
    if not ids:
        return {}
    rows = (
        (
            await db.execute(select(ImageStatusHistory).where(ImageStatusHistory.id.in_(ids)))  # type: ignore[union-attr]
        )
        .scalars()
        .all()
    )
    return {
        row.id or 0: UserHistoryItem(
            type="status_change",
            image_id=row.image_id,
            old_status=row.old_status,
            new_status=row.new_status,
            new_status_label=ImageStatus.get_label(row.new_status),
            created_at=row.created_at,
        )
        for row in rows
    }


async def _load_linked_tags(db: AsyncSession, tag_ids: set[int]) -> dict[int, LinkedTag]:
    """Batch-load tag_id -> LinkedTag for kind-3 (tag_links) hydration."""
    if not tag_ids:
        return {}
    result = await db.execute(
        select(Tags.tag_id, Tags.title, Tags.type).where(Tags.tag_id.in_(tag_ids))  # type: ignore[call-overload,union-attr]
    )
    return {row[0]: LinkedTag(tag_id=row[0], title=row[1], type=row[2]) for row in result.all()}


@router.get("/{user_id}/history", response_model=UserHistoryListResponse)
async def get_user_history(
    user_id: Annotated[int, Path(description="User ID")],
    pagination: Annotated[PaginationParams, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserHistoryListResponse:
    """
    Get all changes made by a user.

    Aggregates history from:
    - Tag audit log (tag metadata changes: rename, type_change, alias, parent, source links)
    - Tag history + tag links (tag add/remove on images, including upload-time adds
      which only ever exist as tag_links rows)
    - Image status history (only visible statuses: REPOST, SPOILER, ACTIVE)

    Status changes with hidden statuses (REVIEW, LOW_QUALITY, INAPPROPRIATE, OTHER)
    are excluded since this endpoint shows what the user did publicly.

    Items are sorted by date descending (most recent first).
    """
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    total = await _user_history_total(db, user_id)

    union_query = _user_history_union(user_id, pagination.offset, pagination.per_page)
    rows = (await db.execute(union_query)).all()

    metadata_items = await _hydrate_tag_metadata_items(db, [r.id_a for r in rows if r.kind == 1])
    usage_items = await _hydrate_tag_history_usage_items(db, [r.id_a for r in rows if r.kind == 2])
    linked_tags_map = await _load_linked_tags(db, {r.id_a for r in rows if r.kind == 3})
    status_items = await _hydrate_status_change_items(db, [r.id_a for r in rows if r.kind == 4])

    # (kind, id_a, id_b) is the row identity the union already sorts on, so it
    # is the natural per-event id to hand clients. Stamped here rather than in
    # the hydration helpers, which are keyed by id and don't know `kind`.
    items: list[UserHistoryItem] = []
    for row in rows:
        event_id = f"{row.kind}-{row.id_a}-{row.id_b}"
        if row.kind == 1:
            item = metadata_items[row.id_a]
        elif row.kind == 2:
            item = usage_items[row.id_a]
        elif row.kind == 3:
            item = UserHistoryItem(
                type="tag_usage",
                action="added",
                tag=linked_tags_map.get(row.id_a),
                image_id=row.id_b,
                date=row.ts,
            )
        else:
            item = status_items[row.id_a]
        item.event_id = event_id
        items.append(item)

    return UserHistoryListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        items=items,
    )
