"""R2 operational tooling.

Subcommands:
    split-existing       — one-time move protected images from public bucket to private
    backfill-locations   — one-shot flip r2_location for existing rows (gated)
    reconcile            — heal: upload missing R2 objects from local FS (gated)
    image                — inspect/re-sync a single image
    verify               — audit R2 vs DB state (read-only)
    purge-cache          — manually purge CDN for one image
    health               — report unsynced counts and storage usage (read-only)

Guarded by R2_ENABLED=true (all commands). backfill-locations and reconcile
additionally require R2_ALLOW_BULK_BACKFILL=true to prevent staging from
mass-uploading prod-imported images to its small staging bucket.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class R2SyncError(Exception):
    """Base for r2_sync CLI errors."""


class R2DisabledError(R2SyncError):
    """Raised when R2_ENABLED=false."""


class BulkBackfillDisallowedError(R2SyncError):
    """Raised when R2_ALLOW_BULK_BACKFILL=false."""


def require_r2_enabled() -> None:
    if not settings.R2_ENABLED:
        raise R2DisabledError(
            "R2_ENABLED=false. Enable R2 in config before running r2_sync commands."
        )


def require_bulk_backfill() -> None:
    require_r2_enabled()
    if not settings.R2_ALLOW_BULK_BACKFILL:
        raise BulkBackfillDisallowedError(
            "R2_ALLOW_BULK_BACKFILL=false. This command walks the DB for "
            "unsynced rows and uploads local files to R2; on staging this "
            "would mass-upload the prod dataset. Set the flag true only in "
            "prod's steady-state config."
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="R2 operational tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    se = sub.add_parser("split-existing")
    se.add_argument("--dry-run", action="store_true")
    sub.add_parser("backfill-locations")
    rec = sub.add_parser("reconcile")
    rec.add_argument("--stale-after", type=int, default=600)
    img = sub.add_parser("image")
    img.add_argument("image_id", type=int)
    ver = sub.add_parser("verify")
    ver.add_argument("--sample", type=int, default=None)
    pc = sub.add_parser("purge-cache")
    pc.add_argument("image_id", type=int)
    h = sub.add_parser("health")
    h.add_argument("--json", action="store_true")

    return parser


async def _dispatch(args: argparse.Namespace) -> int:
    require_r2_enabled()
    raise NotImplementedError(
        f"Subcommand '{args.command}' is not yet implemented in this chunk."
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except R2SyncError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
