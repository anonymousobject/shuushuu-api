"""
User History API endpoints.

Provides aggregated history of all changes made by a user:
- Tag metadata changes (rename, type_change, alias, parent, source links)
- Tag usage (tag add/remove on images)
- Status changes (only visible statuses: REPOST, SPOILER, ACTIVE)
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams
from app.config import ImageStatus
from app.core.database import get_db
from app.models.image_status_history import ImageStatusHistory
from app.models.tag import Tags
from app.models.tag_audit_log import TagAuditLog
from app.models.tag_history import TagHistory
from app.models.user import Users
from app.schemas.audit import UserHistoryItem, UserHistoryListResponse
from app.schemas.tag import LinkedTag

router = APIRouter(prefix="/users", tags=["history"])


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
    - Tag history (tag add/remove on images)
    - Image status history (only visible statuses: REPOST, SPOILER, ACTIVE)

    Status changes with hidden statuses (REVIEW, LOW_QUALITY, INAPPROPRIATE, OTHER)
    are excluded since this endpoint shows what the user did publicly.

    Items are sorted by date descending (most recent first).
    """
    # Check if user exists
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Query all three tables and count total
    # We need to get all records, merge them, sort by timestamp, and paginate

    # 1. Tag audit log (tag metadata changes)
    tag_audit_query = (
        select(TagAuditLog, Tags)
        .outerjoin(Tags, TagAuditLog.tag_id == Tags.tag_id)  # type: ignore[arg-type]
        .where(TagAuditLog.user_id == user_id)  # type: ignore[arg-type]
    )
    tag_audit_result = await db.execute(tag_audit_query)
    tag_audit_rows = tag_audit_result.all()

    # 2. Tag history (tag add/remove on images)
    tag_history_query = (
        select(TagHistory, Tags)
        .outerjoin(Tags, TagHistory.tag_id == Tags.tag_id)  # type: ignore[arg-type]
        .where(TagHistory.user_id == user_id)  # type: ignore[arg-type]
    )
    tag_history_result = await db.execute(tag_history_query)
    tag_history_rows = tag_history_result.all()

    # 3. Image status history (only visible statuses)
    # Include where old_status OR new_status is in VISIBLE_USER_STATUSES
    status_history_query = (
        select(ImageStatusHistory)
        .where(ImageStatusHistory.user_id == user_id)  # type: ignore[arg-type]
        .where(
            or_(
                ImageStatusHistory.old_status.in_(ImageStatus.VISIBLE_USER_STATUSES),  # type: ignore[attr-defined]
                ImageStatusHistory.new_status.in_(ImageStatus.VISIBLE_USER_STATUSES),  # type: ignore[attr-defined]
            )
        )
    )
    status_history_result = await db.execute(status_history_query)
    status_history_rows = status_history_result.scalars().all()

    # Transform to UserHistoryItem objects with timestamp for sorting
    # NOTE: This loads all records into memory before sorting/pagination.
    # For users with very large history, this could be slow. A more scalable
    # approach would use SQL UNION ALL with database-side sorting/pagination.
    # Using (timestamp, type_priority, source_id) for stable sorting.
    items_with_sort_keys: list[tuple[datetime, int, int, UserHistoryItem]] = []

    # Batch-load the "other" tags referenced by tag_metadata rows so the
    # frontend can render things like "Aliased to <X>" without a follow-up
    # request. Mirrors the per-tag /tags/{id}/history endpoint.
    linked_tag_ids: set[int] = set()
    for audit_log, _ in tag_audit_rows:
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

    # Transform tag audit log entries (type_priority=1)
    for audit_log, tag in tag_audit_rows:
        timestamp = audit_log.created_at or datetime.min
        tag_info = LinkedTag(tag_id=tag.tag_id, title=tag.title, type=tag.type) if tag else None

        # Resolve the "other" tag(s) per action_type. Matches the per-tag
        # endpoint: alias uses new_alias_of OR old_alias_of (whichever is set
        # for this row's set/removed variant), parent likewise, and source-
        # linked rows carry both character and source side-by-side.
        alias_target_id = audit_log.new_alias_of or audit_log.old_alias_of
        parent_target_id = audit_log.new_parent_id or audit_log.old_parent_id
        item = UserHistoryItem(
            type="tag_metadata",
            action_type=audit_log.action_type,
            tag=tag_info,
            old_title=audit_log.old_title,
            new_title=audit_log.new_title,
            created_at=timestamp,
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
        )
        items_with_sort_keys.append((timestamp, 1, audit_log.id or 0, item))

    # Transform tag history entries (type_priority=2)
    for history, tag in tag_history_rows:
        timestamp = history.date or datetime.min
        tag_info = LinkedTag(tag_id=tag.tag_id, title=tag.title, type=tag.type) if tag else None
        action = "added" if history.action == "a" else "removed"
        item = UserHistoryItem(
            type="tag_usage",
            action=action,
            tag=tag_info,
            image_id=history.image_id,
            date=timestamp,
        )
        items_with_sort_keys.append((timestamp, 2, history.tag_history_id or 0, item))

    # Transform status history entries (type_priority=3)
    for status_history in status_history_rows:
        timestamp = status_history.created_at or datetime.min
        item = UserHistoryItem(
            type="status_change",
            image_id=status_history.image_id,
            old_status=status_history.old_status,
            new_status=status_history.new_status,
            new_status_label=ImageStatus.get_label(status_history.new_status),
            created_at=timestamp,
        )
        items_with_sort_keys.append((timestamp, 3, status_history.id or 0, item))

    # Sort by timestamp descending, then type priority, then source ID for stable ordering
    items_with_sort_keys.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)

    # Get total count
    total = len(items_with_sort_keys)

    # Apply pagination
    start = pagination.offset
    end = start + pagination.per_page
    paginated_items = [item for _, _, _, item in items_with_sort_keys[start:end]]

    return UserHistoryListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        items=paginated_items,
    )
