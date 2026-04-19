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
from app.services.cloudflare import purge_cache_by_urls

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
        result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
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
            if await r2.object_exists(bucket=bucket, key=key):
                logger.info("r2_upload_skipped_exists", image_id=image_id, key=key)
                continue
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
        result = await db.execute(
            update(Images)
            .where(Images.image_id == image_id)  # type: ignore[arg-type]
            .where(Images.r2_location == R2Location.NONE)  # type: ignore[arg-type]
            .values(r2_location=new_location)
        )
        await db.commit()
        if result.rowcount == 0:  # type: ignore[attr-defined]
            logger.warning(
                "r2_finalize_clobbered",
                image_id=image_id,
            )
            return {"skipped": "concurrent_update"}

    logger.info(
        "r2_finalize_succeeded",
        image_id=image_id,
        r2_location=int(new_location),
    )
    return {"r2_location": int(new_location)}


def _cdn_urls_for(image: Images, variants: list[str]) -> list[str]:
    """Build the public-CDN URLs for the given variants of an image."""
    urls: list[str] = []
    for variant in variants:
        key = _variant_key(image, variant)
        urls.append(f"{settings.R2_PUBLIC_CDN_URL}/{key}")
    return urls


async def sync_image_status_job(
    ctx: dict[str, Any],
    image_id: int,
    old_status: int,
    new_status: int,
) -> dict[str, Any]:
    """Move an image's R2 objects when its status transitions across
    the public/protected boundary.

    - Early-return if r2_location=NONE (finalizer owns first-sync).
    - Early-return if both old and new statuses are on the same side
      of the public/protected boundary.
    - Otherwise: copy each existing variant to destination bucket, verify,
      delete from source. Atomically flip r2_location. If moving
      public → protected, purge the CDN URLs so nobody can fetch the old
      copy from edge cache.
    """
    bind_context(task="r2_status_sync", image_id=image_id)

    if not settings.R2_ENABLED:
        return {"skipped": "disabled"}

    async with get_async_session() as db:
        result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
        image = result.scalar_one_or_none()
        if image is None:
            return {"skipped": "image_missing"}
        if image.r2_location == R2Location.NONE:
            logger.debug(
                "r2_status_sync_skipped_not_finalized",
                image_id=image_id,
            )
            return {"skipped": "not_finalized"}

        # Derive desired location from *current* DB status, not from the
        # enqueued old/new args which may be stale if status changed again.
        dst_location = _location_for_status(image.status)
        src_location = image.r2_location
        if src_location == dst_location:
            return {"skipped": "already_correct"}

        if src_location == R2Location.PUBLIC:
            src_bucket = settings.R2_PUBLIC_BUCKET
            dst_bucket = settings.R2_PRIVATE_BUCKET
        else:
            src_bucket = settings.R2_PRIVATE_BUCKET
            dst_bucket = settings.R2_PUBLIC_BUCKET

        variants = _expected_variants(image)
        r2 = get_r2_storage()

        logger.info(
            "r2_status_transition_started",
            image_id=image_id,
            src=src_bucket,
            dst=dst_bucket,
        )

        moved_variants: list[str] = []
        for variant in variants:
            key = _variant_key(image, variant)
            if not await r2.object_exists(bucket=src_bucket, key=key):
                logger.warning(
                    "r2_status_sync_source_missing",
                    image_id=image_id,
                    bucket=src_bucket,
                    key=key,
                )
                continue
            await r2.copy_object(src_bucket=src_bucket, dst_bucket=dst_bucket, key=key)
            if not await r2.object_exists(bucket=dst_bucket, key=key):
                raise RuntimeError(f"Copy succeeded but {dst_bucket}/{key} does not exist")
            await r2.delete_object(bucket=src_bucket, key=key)
            moved_variants.append(variant)

        if not moved_variants:
            logger.warning(
                "r2_status_sync_nothing_moved",
                image_id=image_id,
                src=src_bucket,
                dst=dst_bucket,
            )
            return {"skipped": "no_objects_moved"}

        missing_variants = set(variants) - set(moved_variants)
        if missing_variants:
            logger.warning(
                "r2_status_sync_partial_move",
                image_id=image_id,
                moved=moved_variants,
                missing=sorted(missing_variants),
            )

        # Conditional flip — only update if r2_location hasn't changed since
        # we read it, avoiding clobbering a concurrent status transition.
        result = await db.execute(
            update(Images)
            .where(Images.image_id == image_id)  # type: ignore[arg-type]
            .where(Images.r2_location == src_location)  # type: ignore[arg-type]
            .values(r2_location=dst_location)
        )
        await db.commit()
        if result.rowcount == 0:  # type: ignore[attr-defined]
            logger.warning(
                "r2_status_sync_clobbered",
                image_id=image_id,
                expected_src=int(src_location),
            )
            return {"skipped": "concurrent_update"}

    # Purge CDN when going public → protected. Do this after the DB flip and
    # outside the DB transaction; a purge failure doesn't roll back the move.
    if dst_location == R2Location.PRIVATE and moved_variants:
        try:
            await purge_cache_by_urls(_cdn_urls_for(image, moved_variants))
        except Exception as e:
            logger.error(
                "r2_cdn_purge_failed_post_transition",
                image_id=image_id,
                error=str(e),
            )
            # Don't re-raise — the bucket move already committed.

    logger.info(
        "r2_status_transition_completed",
        image_id=image_id,
        moved_variants=moved_variants,
    )
    return {
        "moved_variants": moved_variants,
        "r2_location": int(dst_location),
    }


async def r2_delete_image_job(
    ctx: dict[str, Any],
    image_id: int,
    r2_location: int,
    filename: str,
    ext: str,
    variants: list[str],
) -> dict[str, Any]:
    """Delete an image's R2 objects after hard-deletion of the DB row.

    Arguments are denormalised (filename, ext, variants list) because the DB
    row is already gone by the time this runs. `r2_location` tells us which
    bucket the canonical copy lived in; NONE means nothing to do.
    """
    bind_context(task="r2_delete_image", image_id=image_id)

    if not settings.R2_ENABLED:
        return {"skipped": "disabled"}
    if r2_location == R2Location.NONE:
        return {"skipped": "never_in_r2"}

    bucket = (
        settings.R2_PUBLIC_BUCKET
        if r2_location == R2Location.PUBLIC
        else settings.R2_PRIVATE_BUCKET
    )

    r2 = get_r2_storage()
    keys: list[str] = []
    for variant in variants:
        variant_ext = "webp" if variant == "thumbs" else ext
        keys.append(f"{variant}/{filename}.{variant_ext}")

    for key in keys:
        await r2.delete_object(bucket=bucket, key=key)
        logger.info("r2_object_deleted", image_id=image_id, bucket=bucket, key=key)

    if r2_location == R2Location.PUBLIC:
        urls = [f"{settings.R2_PUBLIC_CDN_URL}/{k}" for k in keys]
        try:
            await purge_cache_by_urls(urls)
        except Exception as e:
            logger.error(
                "r2_cdn_purge_failed_post_delete",
                image_id=image_id,
                error=str(e),
            )

    return {"deleted_keys": keys}
