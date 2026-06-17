#!/usr/bin/env python3
"""Ingest offline WD-tagger inference results into the raw prediction store.

Reads JSONL produced by the WD-tagger inference script and stores rows in
``ml_raw_predictions`` (the lossless raw store), skipping rows whose
composite PK already exists so re-runs are safe.

Before inserting predictions the script populates ``ml_external_tags`` from
the model's ``selected_tags.csv`` vocabulary file so that tag-name → id
mapping is always up to date.

Usage:
    uv run python scripts/ml_raw_ingest.py results.jsonl --model caformer_b36.dbv4-full
    uv run python scripts/ml_raw_ingest.py r0.jsonl r1.jsonl --model wd-swinv2-tagger-v3
"""

import argparse
import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.database import get_async_session
from app.services.ml_backfill import iter_results
from app.services.ml_raw_store import ingest_raw_predictions, populate_external_tags

_BATCH_SIZE = 1_000  # records buffered per ingest call (distinct from the service's rows-per-INSERT batch size)


def _all_results(paths: list[str]) -> Iterator[dict[str, Any]]:
    for path in paths:
        yield from iter_results(Path(path))


async def run(args: argparse.Namespace) -> None:
    model_name: str = args.model
    models_root = Path(settings.ML_MODELS_PATH)
    csv_path = models_root / model_name / "selected_tags.csv"

    print(f"model: {model_name}")
    print(f"vocab: {csv_path}")

    total_populated = 0
    total_inserted = 0
    total_records = 0

    async with get_async_session() as db:
        populated = await populate_external_tags(db, csv_path)
        total_populated = populated
        print(f"external_tags populated={populated}")

        # Stream records in batches so we don't load the entire JSONL into RAM.
        batch: list[dict[str, Any]] = []
        for record in _all_results(args.results):
            batch.append(record)
            total_records += 1
            if len(batch) >= _BATCH_SIZE:
                inserted = await ingest_raw_predictions(db, batch)
                total_inserted += inserted
                batch = []

        if batch:
            inserted = await ingest_raw_predictions(db, batch)
            total_inserted += inserted

    print(
        f"records={total_records} inserted={total_inserted} "
        f"(external_tags_populated={total_populated})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest WD-tagger inference results into the raw ML prediction store."
    )
    parser.add_argument(
        "results",
        nargs="+",
        help="Result JSONL file(s) from the WD-tagger inference script",
    )
    parser.add_argument(
        "--model",
        default=settings.ML_MODEL_NAME,
        help=(
            "Model subdirectory name under ML_MODELS_PATH (used to locate "
            "selected_tags.csv). "
            f"Default: {settings.ML_MODEL_NAME}"
        ),
    )
    args = parser.parse_args()
    for path in args.results:
        if not Path(path).exists():
            parser.error(f"results file not found: {path}")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
