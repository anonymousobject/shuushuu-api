#!/usr/bin/env python3
"""Export a manifest of images to run ML tag-suggestion inference on.

Stage 1 of the offline backfill (see docs/ml-tag-suggestions.md). Writes a JSONL
file of {image_id, filename, ext} rows that ml_backfill_infer.py consumes. This
is the only stage that needs the database on the inference host's behalf — the
manifest is copied to the GPU host, which never touches the DB.

Usage:
    uv run python scripts/ml_backfill_manifest.py --out manifest.jsonl
    uv run python scripts/ml_backfill_manifest.py --out m.jsonl --missing-theme --exclude-existing
    uv run python scripts/ml_backfill_manifest.py --out m.jsonl --all-statuses --limit 100
    uv run python scripts/ml_backfill_manifest.py --out delta.jsonl \\
        --exclude-results results_shard0.jsonl --exclude-results results_shard1.jsonl
"""

import argparse
import asyncio
import sys
from pathlib import Path

from app.config import ImageStatus
from app.core.database import get_async_session
from app.services.ml_backfill import fetch_manifest_rows, load_image_ids, write_results


async def run(args: argparse.Namespace) -> None:
    exclude_paths = [Path(p) for p in (args.exclude_results or [])]
    for path in exclude_paths:
        if not path.exists():
            print(f"error: --exclude-results file not found: {path}", file=sys.stderr)
            sys.exit(1)

    exclude_image_ids: set[int] = set()
    for path in exclude_paths:
        exclude_image_ids |= load_image_ids(path)

    status = None if args.all_statuses else args.status
    async with get_async_session() as db:
        rows = await fetch_manifest_rows(
            db,
            status=status,
            missing_theme=args.missing_theme,
            exclude_existing=args.exclude_existing,
            exclude_image_ids=exclude_image_ids if exclude_image_ids else None,
            min_id=args.min_id,
            limit=args.limit,
        )
    out = Path(args.out)
    out.unlink(missing_ok=True)  # always write a fresh manifest
    write_results(out, rows)

    if exclude_paths:
        n_files = len(exclude_paths)
        n_excluded = len(exclude_image_ids)
        print(
            f"Wrote {len(rows)} image rows to {out} "
            f"(excluded {n_excluded} already covered by {n_files} results file(s))"
        )
    else:
        print(f"Wrote {len(rows)} image rows to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export an image manifest for the ML tag-suggestion backfill."
    )
    parser.add_argument("--out", required=True, help="Output JSONL path")
    parser.add_argument(
        "--status",
        type=int,
        default=ImageStatus.ACTIVE,
        help="Only images with this status (default: 1=active)",
    )
    parser.add_argument(
        "--all-statuses",
        action="store_true",
        help="Include every status (overrides --status)",
    )
    parser.add_argument(
        "--missing-theme",
        action="store_true",
        help="Only images that have no theme tags yet",
    )
    parser.add_argument(
        "--exclude-existing",
        action="store_true",
        help="Skip images that already have ML suggestion rows",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of rows")
    parser.add_argument(
        "--exclude-results",
        action="append",
        metavar="RESULTS_JSONL",
        help=(
            "Skip image IDs already present in this results JSONL file. "
            "Can be given multiple times; IDs from all files are unioned."
        ),
    )
    parser.add_argument(
        "--min-id",
        type=int,
        default=None,
        metavar="IMAGE_ID",
        help="Only images with image_id >= this value (catch-up since a known id)",
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
