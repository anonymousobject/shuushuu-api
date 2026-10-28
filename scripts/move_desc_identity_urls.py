#!/usr/bin/env python3
"""Move pixiv-identity text out of artist descs, into the owning link's URL.

Dry-run by default; --apply to write. See
docs/plans/2026-08-01-external-artist-identity-design.md and
app/services/artist_identity_desc_mover.py for the full policy (verbatim URL
preservation, separator tidying, anomaly rules).

Usage:
    uv run python scripts/move_desc_identity_urls.py            # dry run
    uv run python scripts/move_desc_identity_urls.py --apply
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import get_async_session
from app.services.artist_identity_desc_mover import run_desc_mover

# Tags.desc is VARCHAR(200) -- a legitimate value can never exceed this, so
# this is a safety cap for corrupt/oversized data, not a display truncation.
# The report is the mod-review artifact; truncating at a shorter width would
# hide a corrupted tail from the very review this report exists for.
_SAMPLE_WIDTH = 200


def _truncate(text: str | None, width: int = _SAMPLE_WIDTH) -> str:
    if text is None:
        return "<NULL>"
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()

    async with get_async_session() as db:
        report = await run_desc_mover(db, apply=args.apply)

    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"[{mode}] descs cleaned (URL):            {report.descs_cleaned}")
    print(f"[{mode}] bare-text descs stripped:        {report.bare_text_stripped}")
    print(f"[{mode}] descs emptied to NULL:           {report.descs_emptied}")
    print(f"[{mode}] links rewritten to verbatim URL: {report.links_rewritten_to_verbatim}")
    print(f"[{mode}] verbatim rewrites skipped (dup): {report.links_verbatim_skipped_duplicate}")

    print(f"\nsample changes ({len(report.samples)}) -- review before --apply:")
    for sample in report.samples:
        print(f"  tag {sample.tag_id} '{sample.title}':")
        print(f"    desc before: {_truncate(sample.before)!r}")
        print(f"    desc after:  {_truncate(sample.after)!r}")
        if sample.link_url_before is not None:
            print(f"    link before: {sample.link_url_before!r}")
            print(f"    link after:  {sample.link_url_after!r}")

    print(f"\nanomalies ({len(report.anomalies)}):")
    for line in report.anomalies:
        print(f"  - {line}")


if __name__ == "__main__":
    asyncio.run(main())
