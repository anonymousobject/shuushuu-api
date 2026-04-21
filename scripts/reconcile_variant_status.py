"""Flip medium/large status from PENDING (2) to READY (1) when the variant file exists on disk.

Repairs rows left stuck at PENDING by a pre-PR #173 worker that never flipped the
status after generating the file (see 2026-03-07 through worker-restart window).

Walks images where medium=PENDING or large=PENDING, checks the expected file on
disk, and issues batched UPDATEs only for rows whose file is present. Rows with
missing files are logged (they need a real regen via generate_variants.py).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select, update

from app.config import settings
from app.core.database import engine, get_async_session
from app.core.logging import get_logger
from app.models.image import Images, VariantStatus

logger = get_logger(__name__)


async def reconcile(*, variant: str, dry_run: bool, batch_size: int = 500) -> None:
    """Flip `variant` column from PENDING to READY for rows whose file is on disk.

    variant: 'medium' or 'large'.
    """
    column = getattr(Images, variant)
    variant_dir = Path(settings.STORAGE_PATH) / variant

    last_image_id = 0
    scanned = 0
    flipped = 0
    missing = 0

    while True:
        async with get_async_session() as db:
            stmt = (
                select(Images)
                .where(column == VariantStatus.PENDING.value)
                .where(Images.image_id > last_image_id)  # type: ignore[arg-type,operator]
                .order_by(Images.image_id)  # type: ignore[arg-type]
                .limit(batch_size)
            )
            result = await db.execute(stmt)
            rows = list(result.scalars())

        if not rows:
            break

        last_image_id = rows[-1].image_id  # type: ignore[assignment]

        flip_ids: list[int] = []
        for image in rows:
            scanned += 1
            path = variant_dir / f"{image.filename}.{image.ext}"
            if path.exists():
                flip_ids.append(image.image_id)  # type: ignore[arg-type]
            else:
                missing += 1
                logger.warning(
                    "variant_file_missing_on_disk",
                    variant=variant,
                    image_id=image.image_id,
                    path=str(path),
                )

        if flip_ids:
            if dry_run:
                for image_id in flip_ids:
                    print(f"DRY_RUN flip image_id={image_id} {variant}=PENDING -> READY")
            else:
                async with get_async_session() as db:
                    await db.execute(
                        update(Images)
                        .where(Images.image_id.in_(flip_ids))  # type: ignore[union-attr]
                        .where(column == VariantStatus.PENDING.value)
                        .values(**{variant: VariantStatus.READY.value})
                    )
                    await db.commit()
            flipped += len(flip_ids)

        logger.info(
            "reconcile_progress",
            variant=variant,
            scanned=scanned,
            flipped=flipped,
            missing=missing,
            last_image_id=last_image_id,
        )

    print(
        f"{'[dry-run] ' if dry_run else ''}{variant}: flipped {flipped}"
        f" ({missing} missing on disk) across {scanned} scanned"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant",
        choices=("medium", "large", "both"),
        default="both",
        help="Which column to reconcile (default: both).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        variants = ("medium", "large") if args.variant == "both" else (args.variant,)
        for variant in variants:
            await reconcile(variant=variant, dry_run=args.dry_run)
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
