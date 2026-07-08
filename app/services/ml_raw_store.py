"""
ML raw store services.

Utilities for populating the raw ML prediction dictionary tables
(ml_external_tags, ml_models) from offline artefacts such as the
animetimm ``selected_tags.csv`` vocabulary file, and for bulk-ingesting
raw per-image predictions into ``ml_raw_predictions``.
"""

import csv
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions

logger = get_logger(__name__)

_BATCH_SIZE = 5_000


async def populate_external_tags(db: AsyncSession, csv_path: Path) -> int:
    """
    Upsert the ML model's tag vocabulary from a CSV file into ``ml_external_tags``.

    Reads ``name`` and ``category`` columns by name (via :class:`csv.DictReader`),
    so the function is compatible with both the synthetic test header
    (``tag_id,name,category``) and the real animetimm header
    (``name,category,best_threshold``).

    Only rows whose ``name`` is not already present in the table are inserted.
    Duplicate names within a single CSV pass are silently de-duplicated.
    ``category`` is coerced to ``int``.

    Args:
        db: Async database session (will be committed on success).
        csv_path: Path to the CSV file to read.

    Returns:
        The number of new rows actually inserted (0 on a repeat run).
    """
    # Read CSV entries by name, de-duplicating within the file.
    file_entries: dict[str, int] = {}
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            name = row["name"]
            category = int(row["category"])
            if name not in file_entries:
                file_entries[name] = category

    if not file_entries:
        logger.info("populate_external_tags_empty_csv", path=str(csv_path))
        return 0

    # Fetch names already in the DB so we only insert missing ones.
    existing_result = await db.execute(
        select(MlExternalTags.name).where(  # type: ignore[call-overload]
            MlExternalTags.name.in_(list(file_entries.keys()))  # type: ignore[attr-defined]
        )
    )
    existing_names: set[str] = {row[0] for row in existing_result.all()}

    to_insert = [
        MlExternalTags(name=name, category=category)
        for name, category in file_entries.items()
        if name not in existing_names
    ]

    if to_insert:
        db.add_all(to_insert)
        await db.commit()

    count = len(to_insert)
    logger.info(
        "populate_external_tags_done",
        path=str(csv_path),
        inserted=count,
        skipped=len(file_entries) - count,
    )
    return count


async def ingest_raw_predictions(
    db: AsyncSession,
    records: list[dict[str, Any]],
) -> int:
    """Bulk-insert raw ML predictions into ``ml_raw_predictions``.

    For each record ``{image_id, predictions: [{external_tag, confidence,
    model_version, ...}]}`` the function:

    1. Builds a ``name → external_tag_id`` lookup from ``ml_external_tags``
       (one query).
    2. Upserts any unseen ``model_version`` values into ``ml_models``.
    3. Collects ``(image_id, model_id, external_tag_id, confidence)`` rows,
       skipping predictions whose ``external_tag`` is not in the dictionary.
    4. Bulk-inserts in batches of up to :data:`_BATCH_SIZE` rows using
       ``INSERT IGNORE`` so re-running is idempotent.  The sum of
       ``rowcount`` values across batches is returned; this equals 0 on a
       pure re-run because existing composite-PK rows are silently skipped.

    Args:
        db: Async database session (committed on success).
        records: List of per-image prediction records.

    Returns:
        Number of rows actually inserted (0 if all rows already existed).
    """
    # --- 1. Build name → external_tag_id map (one query) ---
    ext_tag_result = await db.execute(select(MlExternalTags.name, MlExternalTags.id))  # type: ignore[call-overload]
    ext_tag_map: dict[str, int] = {row[0]: row[1] for row in ext_tag_result.all()}

    # --- 2. Collect model versions and upsert into ml_models ---
    model_versions: set[str] = set()
    for record in records:
        for pred in record.get("predictions", []):
            mv = pred.get("model_version")
            if mv:
                model_versions.add(mv)

    # Fetch already-existing model rows.
    model_id_map: dict[str, int] = {}
    if model_versions:
        existing_models = await db.execute(
            select(MlModels.name, MlModels.id).where(  # type: ignore[call-overload]
                MlModels.name.in_(list(model_versions))  # type: ignore[attr-defined]
            )
        )
        model_id_map = {row[0]: row[1] for row in existing_models.all()}

        # Insert any missing model names.
        new_model_names = model_versions - model_id_map.keys()
        if new_model_names:
            new_models = [MlModels(name=name) for name in sorted(new_model_names)]
            db.add_all(new_models)
            await db.flush()
            for m in new_models:
                model_id_map[m.name] = m.id  # type: ignore[assignment]

    # --- 3. Collect rows to insert ---
    rows: list[dict[str, Any]] = []
    unknown_tag_count = 0
    unknown_tag_sample: set[str] = set()
    unknown_model_count = 0
    unknown_model_sample: set[str] = set()
    for record in records:
        image_id: int = record["image_id"]
        for pred in record.get("predictions", []):
            ext_name: str = pred.get("external_tag", "")
            ext_id = ext_tag_map.get(ext_name)
            if ext_id is None:
                unknown_tag_count += 1
                if len(unknown_tag_sample) < 10:
                    unknown_tag_sample.add(ext_name)
                continue
            mv = pred.get("model_version", "")
            model_id = model_id_map.get(mv)
            if model_id is None:
                unknown_model_count += 1
                if len(unknown_model_sample) < 10:
                    unknown_model_sample.add(mv)
                continue
            rows.append(
                {
                    "image_id": image_id,
                    "model_id": model_id,
                    "external_tag_id": ext_id,
                    "confidence": float(pred["confidence"]),
                }
            )

    if unknown_tag_count > 0:
        logger.warning(
            "ingest_raw_predictions_unknown_tags",
            count=unknown_tag_count,
            sample=sorted(unknown_tag_sample),
        )
    if unknown_model_count > 0:
        logger.warning(
            "ingest_raw_predictions_unknown_model",
            count=unknown_model_count,
            sample=sorted(unknown_model_sample),
        )

    # --- 4. Bulk INSERT IGNORE in batches ---
    total_inserted = 0
    for start in range(0, len(rows), _BATCH_SIZE):
        batch = rows[start : start + _BATCH_SIZE]
        stmt = mysql_insert(MlRawPredictions).values(batch).prefix_with("IGNORE")
        res = await db.execute(stmt)
        total_inserted += res.rowcount  # type: ignore[attr-defined]

    if rows:
        await db.commit()

    logger.info(
        "ingest_raw_predictions_done",
        records=len(records),
        rows_collected=len(rows),
        inserted=total_inserted,
    )
    return total_inserted
