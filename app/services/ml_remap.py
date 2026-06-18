"""
ML remap service.

Re-maps raw predictions from the ml_raw_predictions store into pending
MlTagSuggestions rows using the current state of tag_mappings. This is the
cheap re-map path: instead of re-running GPU inference, we re-apply the
mapping/resolution/redundancy pipeline to predictions that are already stored.

The key difference from store_predictions:
- remap_image scopes its stale-pending cleanup to ``model_name`` only, so
  pending rows produced by a different model (e.g. swinv2 live-path rows) are
  never deleted during a caformer re-map (cross-source clobber guard).
- remap_image does NOT reset approved suggestions whose tag was removed from
  the image (store_predictions does; remap_image intentionally does not).
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.services.ml_suggestion_pipeline import compute_implied_suggestions

logger = get_logger(__name__)


async def remap_image(
    db: AsyncSession, image_id: int, predictions: list[dict[str, Any]], model_name: str
) -> int:
    """Re-map raw predictions into ml_tag_suggestions: regenerate the pending set
    from current mappings, preserve approved/rejected, never re-suggest a
    dismissed tag. Returns the number of pending rows added.

    The delete step is scoped to ``model_name``: because ml_tag_suggestions has a
    UNIQUE(image_id, tag_id) constraint, re-map scopes its reconcile to its own
    model_version so it never deletes pending rows produced by a different model
    (e.g. swinv2 live path rows are safe during a caformer re-map).
    """
    implied, _applied = await compute_implied_suggestions(db, image_id, predictions)
    implied_by_tag = {p["tag_id"]: p for p in implied}

    existing = list(
        (await db.execute(select(MlTagSuggestions).where(MlTagSuggestions.image_id == image_id)))
        .scalars()
        .all()
    )
    existing_tag_ids = {s.tag_id for s in existing}

    # Delete stale pending for THIS model only (tag no longer implied).
    deleted = 0
    for s in existing:
        if (
            s.status == "pending"
            and s.model_version == model_name
            and s.tag_id not in implied_by_tag
        ):
            await db.delete(s)
            deleted += 1

    # Add pending for implied tags with no existing row (any status).
    # "existing_tag_ids" covers all statuses: approved/rejected rows are
    # intentionally skipped so dismissed tags stay dismissed.
    added = 0
    for tag_id, p in implied_by_tag.items():
        if tag_id in existing_tag_ids:
            # Preserve every existing row regardless of status: approved/rejected
            # stay as-is (dismissed stays dismissed), and a still-implied pending
            # row from this model is kept without refreshing its confidence
            # (deliberate v1 simplification — re-map is cheap, not a rescore).
            continue
        db.add(
            MlTagSuggestions(
                image_id=image_id,
                tag_id=tag_id,
                confidence=p["confidence"],
                model_version=p["model_version"],
                status="pending",
            )
        )
        added += 1

    await db.commit()
    logger.info(
        "ml_remap_image_reconciled",
        image_id=image_id,
        model_version=model_name,
        added=added,
        deleted=deleted,
    )
    return added


async def remap_image_from_store(db: AsyncSession, image_id: int, model_name: str) -> int:
    """Read this image's raw predictions for ``model_name`` from ml_raw_predictions,
    then call remap_image. This is the normal operational entry point for the CLI."""
    rows = (
        await db.execute(
            select(MlExternalTags.name, MlRawPredictions.confidence)
            .join(MlRawPredictions, MlRawPredictions.external_tag_id == MlExternalTags.id)
            .join(MlModels, MlModels.id == MlRawPredictions.model_id)
            .where(
                MlRawPredictions.image_id == image_id,
                MlModels.name == model_name,
            )
        )
    ).all()

    predictions = [
        {"external_tag": name, "confidence": conf, "model_version": model_name}
        for name, conf in rows
    ]
    logger.info(
        "ml_remap_predictions_fetched",
        image_id=image_id,
        model_version=model_name,
        prediction_count=len(predictions),
    )
    return await remap_image(db, image_id, predictions, model_name)
