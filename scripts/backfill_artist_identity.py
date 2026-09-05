#!/usr/bin/env python3
"""Backfill artist identity (site/external_id) on tag_external_links.

Dry-run by default; --apply to write. See
docs/plans/2026-08-01-external-artist-identity-design.md §5.

Usage:
    uv run python scripts/backfill_artist_identity.py            # dry run
    uv run python scripts/backfill_artist_identity.py --apply
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import get_async_session
from app.services.artist_identity_backfill import run_backfill


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()

    async with get_async_session() as db:
        report = await run_backfill(db, apply=args.apply)

    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"[{mode}] links parsed in place:        {report.links_parsed}")
    print(f"[{mode}] links created from aliases:   {report.links_created_from_aliases}")
    print(f"[{mode}] links created from descs:     {report.links_created_from_desc}")
    print(f"[{mode}] links created from titles:    {report.links_created_from_titles}")
    print(f"[{mode}] links created from desc text: {report.links_created_from_desc_text}")
    print(f"artist tags still without identity:  {report.artist_tags_without_identity}")
    print(f"\nanomalies ({len(report.anomalies)}):")
    for line in report.anomalies:
        print(f"  - {line}")


if __name__ == "__main__":
    asyncio.run(main())
