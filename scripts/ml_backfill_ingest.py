#!/usr/bin/env python3
"""Ingest offline ML inference results into the database.

Stage 3 of the offline backfill (see docs/ml-tag-suggestions.md). Reads JSONL
produced by ml_backfill_infer.py and stores pending MlTagSuggestions rows
through the shared pipeline (map external tags → resolve aliases/hierarchy →
drop redundant → insert). Runs next to the database; the GPU host only ever
produced files.

Resumable via a checkpoint file of processed image IDs. A row that fails (e.g.
its image was deleted since the manifest was built) is logged and skipped — the
run continues.

Usage:
    uv run python scripts/ml_backfill_ingest.py results.jsonl
    uv run python scripts/ml_backfill_ingest.py r0.jsonl r1.jsonl --checkpoint ingest.done
"""

import argparse
import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.core.database import get_async_session
from app.services.ml_backfill import ingest_results, iter_results, load_image_ids, write_results


async def run(args: argparse.Namespace) -> None:
    checkpoint = Path(args.checkpoint)
    already_done = load_image_ids(checkpoint)

    def mark_done(image_id: int) -> None:
        # Append after each successful image so a crash resumes where it stopped.
        write_results(checkpoint, [{"image_id": image_id}])

    def all_results() -> Iterator[dict[str, Any]]:
        for path in args.results:
            yield from iter_results(Path(path))

    print(f"resuming past {len(already_done)} already-ingested images")
    async with get_async_session() as db:
        stats = await ingest_results(
            db, all_results(), skip_ids=already_done, on_processed=mark_done
        )

    print(
        f"processed={stats.processed} created={stats.created} "
        f"skipped={stats.skipped} errors={len(stats.errors)}"
    )
    for err in stats.errors[:20]:
        print(f"  error: {err}")
    if len(stats.errors) > 20:
        print(f"  ... and {len(stats.errors) - 20} more")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest ML inference results into the database."
    )
    parser.add_argument("results", nargs="+", help="Result JSONL file(s) from ml_backfill_infer.py")
    parser.add_argument(
        "--checkpoint",
        default="ml_backfill_ingest.done",
        help="JSONL checkpoint of processed image IDs (default: ml_backfill_ingest.done)",
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
