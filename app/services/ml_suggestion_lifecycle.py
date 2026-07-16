"""Couple ML suggestion rows to the image-status lifecycle.

Pending suggestions exist only on suggestion-eligible images (ACTIVE, SPOILER —
see CONTEXT.md and ADR-0002). This module owns the transition hook called by
both status-write sites (change_image_status and the owner-facing image-update
endpoint). Repost-specific cleanup lives in app/services/repost.py.
"""

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, settings
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.services.ml_remap import remap_image_from_store


async def sync_suggestions_for_status_transition(
    db: AsyncSession,
    image_id: int,
    old_status: int,
    new_status: int,
) -> None:
    """Keep suggestion rows consistent with an image-status change.

    eligible -> ineligible: delete the image's pending rows. (Marking as REPOST
    additionally wipes reviewed rows — that lives in migrate_repost_data, which
    both repost paths share.)
    ineligible -> eligible: re-seed pending rows from the raw-prediction store
    (no inference; seeds nothing if the image has no raw predictions).

    Must be called AFTER the caller has assigned the new status to the
    in-session Images row. Flush-only; the caller owns the transaction.
    """
    was_eligible = old_status in ImageStatus.SUGGESTION_ELIGIBLE_STATUSES
    is_eligible = new_status in ImageStatus.SUGGESTION_ELIGIBLE_STATUSES
    if was_eligible == is_eligible:
        return

    if was_eligible:
        await db.execute(
            delete(MlTagSuggestions).where(
                MlTagSuggestions.image_id == image_id,  # type: ignore[arg-type]
                MlTagSuggestions.status == "pending",  # type: ignore[arg-type]
            )
        )
    else:
        await remap_image_from_store(db, image_id, settings.ML_MODEL_NAME)
