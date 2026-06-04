"""Side effects for image status transitions.

Currently just the R2 bucket-move enqueue. Any status-change code path must
route through this helper (or enqueue `sync_image_status_job` directly) so
the canonical R2 object follows the public/protected boundary — otherwise
a public→protected transition leaves the image reachable via CDN until the
edge TTL expires.
"""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AdminActionType, ImageStatus, settings
from app.core.r2_constants import PUBLIC_IMAGE_STATUSES_FOR_R2
from app.models.admin_action import AdminActions
from app.models.image import Images
from app.models.image_status_history import ImageStatusHistory
from app.models.user import Users
from app.services.repost import migrate_repost_data
from app.tasks.queue import enqueue_job


async def enqueue_r2_sync_on_status_change(
    image_id: int,
    old_status: int,
    new_status: int,
) -> None:
    """Enqueue R2 bucket move for an image whose status changed.

    No-op when R2 is disabled, the status is unchanged, or the transition
    stays on the same side of the public/protected boundary (e.g.
    ACTIVE→SPOILER, both public) — the worker would early-return in those
    cases, so skipping here saves an enqueue + DB round-trip per hop. MUST
    be called AFTER the DB commit that persists `new_status`; the worker
    loads the row in a fresh session and derives the destination bucket
    from its current status.
    """
    if not settings.R2_ENABLED or old_status == new_status:
        return
    if (old_status in PUBLIC_IMAGE_STATUSES_FOR_R2) == (new_status in PUBLIC_IMAGE_STATUSES_FOR_R2):
        return
    await enqueue_job(
        "sync_image_status_job",
        image_id=image_id,
        old_status=old_status,
        new_status=new_status,
    )


async def change_image_status(
    db: AsyncSession,
    image: Images,
    actor: Users | None,
    *,
    new_status: int | None = None,
    reason_category: int | None = None,
    reason: str | None = None,
    replacement_id: int | None = None,
    locked: bool | None = None,
    action_type: int = AdminActionType.IMAGE_STATUS_CHANGE,
    report_id: int | None = None,
    review_id: int | None = None,
    extra_details: dict[str, object] | None = None,
) -> dict[str, int]:
    """Apply a moderation status and/or lock change to an image.

    Writes the public status-history row (when the status actually changes) and
    the internal admin-action audit row. Does NOT commit — the caller owns the
    transaction and any post-commit side effects (R2 sync enqueue, rating
    recalculation). Raises HTTPException on invalid transitions.

    Returns the repost migration_result dict (empty unless a repost was processed).
    """
    assert image.image_id is not None  # caller passes a persisted image
    actor_id = actor.user_id if actor is not None else None  # None for system actions
    previous_status = image.status
    previous_locked = image.locked
    migration_result: dict[str, int] = {}

    if new_status is not None:
        # Un-hiding an image (hidden -> visible) is a deliberate moderation action
        # and must carry a reason. Enforced only for manual mod paths; review/system
        # closes reactivate on a KEEP outcome with no free-text reason and are exempt.
        _manual_actions = {AdminActionType.IMAGE_STATUS_CHANGE, AdminActionType.REPORT_ACTION}
        if (
            action_type in _manual_actions
            and previous_status not in ImageStatus.VISIBLE_USER_STATUSES
            and new_status in ImageStatus.VISIBLE_USER_STATUSES
            and not reason
        ):
            raise HTTPException(
                status_code=400,
                detail="A reason is required when restoring a hidden image to a visible status.",
            )

        if new_status == ImageStatus.REPOST:
            if replacement_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="replacement_id is required when marking as repost",
                )
            if replacement_id == image.image_id:
                raise HTTPException(
                    status_code=400,
                    detail="An image cannot be a repost of itself",
                )
            original = (
                await db.execute(
                    select(Images).where(Images.image_id == replacement_id)  # type: ignore[arg-type]
                )
            ).scalar_one_or_none()
            if not original:
                raise HTTPException(status_code=404, detail="Original image not found")
            image.replacement_id = replacement_id
            migration_result = await migrate_repost_data(image.image_id, replacement_id, db)
        else:
            # Clear replacement_id when not a repost
            image.replacement_id = None

        # reason_category only applies to DEACTIVATED; reason may accompany any status
        if new_status == ImageStatus.DEACTIVATED:
            image.reason_category = reason_category
        else:
            image.reason_category = None
        image.status_reason = reason

        image.status = new_status
        image.status_user_id = actor_id
        image.status_updated = datetime.now(UTC)

    if locked is not None:
        image.locked = 1 if locked else 0

    # Public status-history row only when the status actually changed
    if new_status is not None and new_status != previous_status:
        db.add(
            ImageStatusHistory(
                image_id=image.image_id,
                old_status=previous_status,
                new_status=new_status,
                user_id=actor_id,
                reason_category=image.reason_category,
                reason=image.status_reason,
            )
        )

    db.add(
        AdminActions(
            user_id=actor_id,
            action_type=action_type,
            report_id=report_id,
            review_id=review_id,
            image_id=image.image_id,
            details={
                # extra_details first so the canonical base keys below always win
                **(extra_details or {}),
                "previous_status": previous_status,
                "new_status": image.status,
                "previous_locked": previous_locked,
                "new_locked": image.locked,
                "replacement_id": image.replacement_id,
                "reason_category": image.reason_category,
                "reason": image.status_reason,
                **migration_result,
            },
        )
    )

    return migration_result
