"""ARQ jobs for R2 sync: finalize, status transitions, deletions."""

from pathlib import Path as FilePath
from typing import Any

from arq import Retry
from sqlalchemy import select, update

from app.config import settings
from app.core.database import get_async_session
from app.core.logging import bind_context, get_logger
from app.core.r2_client import get_r2_storage
from app.core.r2_constants import (
    PUBLIC_IMAGE_STATUSES_FOR_R2,
    R2Location,
)
from app.models.image import Images, VariantStatus

logger = get_logger(__name__)


def _bucket_for_status(status: int) -> str:
    """Public bucket for public statuses, private bucket otherwise."""
    return (
        settings.R2_PUBLIC_BUCKET
        if status in PUBLIC_IMAGE_STATUSES_FOR_R2
        else settings.R2_PRIVATE_BUCKET
    )


def _location_for_status(status: int) -> R2Location:
    return R2Location.PUBLIC if status in PUBLIC_IMAGE_STATUSES_FOR_R2 else R2Location.PRIVATE


def _expected_variants(image: Images) -> list[str]:
    """Variants that should exist on disk for this image."""
    variants = ["fullsize", "thumbs"]
    if image.medium == VariantStatus.READY:
        variants.append("medium")
    if image.large == VariantStatus.READY:
        variants.append("large")
    return variants


def _variant_key(image: Images, variant: str) -> str:
    ext = "webp" if variant == "thumbs" else image.ext
    return f"{variant}/{image.filename}.{ext}"


def _local_path(image: Images, variant: str) -> FilePath:
    ext = "webp" if variant == "thumbs" else image.ext
    return FilePath(settings.STORAGE_PATH) / variant / f"{image.filename}.{ext}"


async def r2_finalize_upload_job(ctx: dict[str, Any], image_id: int) -> dict[str, Any]:
    """First-time sync of a newly uploaded image to R2.

    - Reads current image row to pick the right bucket based on status.
    - Verifies all expected variant files exist on disk; retries if any are missing.
    - Uploads every expected variant to the chosen bucket.
    - Atomically flips r2_location to PUBLIC or PRIVATE.

    No-ops when R2 is disabled or the image is already synced.
    """
    bind_context(task="r2_finalize_upload", image_id=image_id)

    if not settings.R2_ENABLED:
        logger.debug("r2_finalize_skipped_disabled", image_id=image_id)
        return {"skipped": "disabled"}

    async with get_async_session() as db:
        result = await db.execute(select(Images).where(Images.image_id == image_id))
        image = result.scalar_one_or_none()
        if image is None:
            logger.warning("r2_finalize_image_missing", image_id=image_id)
            return {"skipped": "image_missing"}
        if image.r2_location != R2Location.NONE:
            logger.debug(
                "r2_finalize_skipped_already_synced",
                image_id=image_id,
                r2_location=image.r2_location,
            )
            return {"skipped": "already_synced"}

        variants = _expected_variants(image)
        # Verify all expected files exist — otherwise let the job retry so
        # in-flight variant jobs have time to complete.
        for variant in variants:
            path = _local_path(image, variant)
            if not path.exists():
                logger.info(
                    "r2_finalize_retry_missing_variant",
                    image_id=image_id,
                    variant=variant,
                    path=str(path),
                )
                raise Retry(defer=ctx.get("job_try", 1) * 30)

        bucket = _bucket_for_status(image.status)
        r2 = get_r2_storage()
        for variant in variants:
            key = _variant_key(image, variant)
            path = _local_path(image, variant)
            logger.info(
                "r2_upload_started",
                image_id=image_id,
                bucket=bucket,
                key=key,
            )
            await r2.upload_file(bucket=bucket, key=key, path=path)
            logger.info(
                "r2_upload_succeeded",
                image_id=image_id,
                bucket=bucket,
                key=key,
            )

        # Atomic flip. Re-check r2_location to avoid clobbering a concurrent
        # finalize (arq at-most-once + DB row as lock).
        new_location = _location_for_status(image.status)
        await db.execute(
            update(Images)
            .where(Images.image_id == image_id)
            .where(Images.r2_location == R2Location.NONE)
            .values(r2_location=new_location)
        )
        await db.commit()

    logger.info(
        "r2_finalize_succeeded",
        image_id=image_id,
        r2_location=int(new_location),
    )
    return {"r2_location": int(new_location)}
