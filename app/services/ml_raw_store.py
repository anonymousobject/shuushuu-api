"""
ML raw store services.

Utilities for populating the raw ML prediction dictionary tables
(ml_external_tags, ml_models) from offline artefacts such as the
animetimm ``selected_tags.csv`` vocabulary file.
"""

import csv
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.ml_raw_prediction import MlExternalTags

logger = get_logger(__name__)


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
        select(MlExternalTags.name).where(
            MlExternalTags.name.in_(list(file_entries.keys()))  # type: ignore[union-attr]
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
