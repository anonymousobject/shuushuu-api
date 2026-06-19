#!/usr/bin/env python3
"""Validate curated tag_mappings against a model's selected_tags.csv vocabulary.

Detects mappings that reference external tag names no longer present in the
model's vocabulary — "orphan" mappings that would silently drop predictions
at inference time.

Usage:
    uv run python scripts/validate_tag_mappings.py --model caformer_b36.dbv4-full
    uv run python scripts/validate_tag_mappings.py --vocab /path/to/selected_tags.csv

Exits 0 if no orphans are found, 1 if any orphans are detected.
"""

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from app.config import settings
from app.core.database import get_async_session
from app.services.tag_mapping_service import find_orphan_mappings, get_mapped_external_tag_names

_MAX_ORPHANS_PRINTED = 50


def _load_vocab(csv_path: Path) -> set[str]:
    """Parse tag names from a selected_tags.csv file.

    Reads the ``name`` column by name (via csv.DictReader), which is compatible
    with both the real animetimm header (``name,category,best_threshold``) and
    the synthetic test header (``tag_id,name,category``).
    """
    names: set[str] = set()
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            name = row["name"]
            if name:
                names.add(name)
    return names


async def run(args: argparse.Namespace) -> int:
    vocab_path: Path = args.vocab

    if not vocab_path.exists():
        print(f"Error: vocab file not found: {vocab_path}", file=sys.stderr)
        return 1

    print(f"vocab: {vocab_path}")

    vocab = _load_vocab(vocab_path)

    async with get_async_session() as db:
        mapped = await get_mapped_external_tag_names(db)

    orphans = find_orphan_mappings(mapped, vocab)

    print(f"mapped tags:  {len(mapped)}")
    print(f"vocab tags:   {len(vocab)}")
    print(f"orphans:      {len(orphans)}")

    if not orphans:
        print("\nOK — all mapped tags are present in the vocab.")
        return 0

    print("\nOrphan mappings (in tag_mappings but absent from vocab):")
    for name in orphans[:_MAX_ORPHANS_PRINTED]:
        print(f"  {name}")
    if len(orphans) > _MAX_ORPHANS_PRINTED:
        print(f"  ... and {len(orphans) - _MAX_ORPHANS_PRINTED} more")

    return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate curated tag_mappings against a model's selected_tags.csv vocabulary. "
            "Exits 1 if any mappings reference tags absent from the vocab."
        )
    )

    vocab_group = parser.add_mutually_exclusive_group(required=True)
    vocab_group.add_argument(
        "--model",
        metavar="NAME",
        help=(
            "Model subdirectory name under ML_MODELS_PATH "
            f"(resolves to {settings.ML_MODELS_PATH}/<name>/selected_tags.csv)"
        ),
    )
    vocab_group.add_argument(
        "--vocab",
        metavar="PATH",
        type=Path,
        help="Explicit path to selected_tags.csv (overrides --model).",
    )

    args = parser.parse_args()

    # Resolve the vocab path from --model if --vocab was not given directly.
    if args.vocab is None:
        models_root = Path(settings.ML_MODELS_PATH)
        args.vocab = models_root / args.model / "selected_tags.csv"

    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
