"""Tests for ``scripts/r2_sync.py banners-backfill`` subcommand.

Exercises ``cmd_banners_backfill`` end-to-end against a real moto S3 server
(via the shared ``setup_buckets``/``moto_session``/``moto_server`` fixtures
in ``tests/unit/conftest.py``) plus the real ``db_session`` fixture from the
parent conftest. ``get_async_session`` is patched to yield the test session
so the script's DB reads see the seeded banners; ``r2_client._instance`` is
swapped for the moto-wired R2Storage so ``get_r2_storage()`` returns it.
"""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import r2_client
from app.models.misc import Banners, BannerSize
from scripts.r2_sync import cmd_banners_backfill

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _mock_session_cm(db_session: AsyncSession):
    """Async-context-manager mock that yields the real test session."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=db_session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.fixture
def banner_settings(monkeypatch, tmp_path: Path) -> Path:
    """Wire the banner-relevant settings to tmp_path / public bucket / R2 enabled."""
    monkeypatch.setattr(settings, "BANNER_STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "R2_PUBLIC_BUCKET", "public")
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", True)
    return tmp_path


@pytest.fixture
def install_r2(monkeypatch, setup_buckets):
    """Install the moto-backed R2Storage as the ``get_r2_storage()`` singleton."""
    monkeypatch.setattr(r2_client, "_instance", setup_buckets)
    return setup_buckets


@pytest.fixture
def patch_session(db_session: AsyncSession):
    """Make ``scripts.r2_sync.get_async_session()`` yield the test session."""
    with patch(
        "scripts.r2_sync.get_async_session",
        return_value=_mock_session_cm(db_session),
    ):
        yield


def _seed_local(banner_dir: Path, *paths: str) -> None:
    """Write small placeholder bytes for each banner subpath."""
    for path in paths:
        target = banner_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG-banner-bytes")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_backfill_full_image_banner(
    banner_settings,
    install_r2,
    patch_session,
    db_session,
):
    """One-piece banner with full_image on disk → bit flips, R2 object exists."""
    _seed_local(banner_settings, "eva/full.jpg")

    banner = Banners(
        name="eva",
        size=BannerSize.small,
        full_image="eva/full.jpg",
    )
    db_session.add(banner)
    await db_session.commit()
    await db_session.refresh(banner)

    report = await cmd_banners_backfill(dry_run=False)
    assert report["processed"] == 1
    assert report["flipped"] == 1
    assert report["skipped_missing_local"] == 0

    await db_session.refresh(banner)
    assert banner.in_r2 is True
    assert await install_r2.object_exists(
        bucket="public", key="banners/eva/full.jpg"
    )


@pytest.mark.unit
async def test_backfill_three_part_banner(
    banner_settings,
    install_r2,
    patch_session,
    db_session,
    moto_session,
    moto_server,
):
    """Three-part banner with all files on disk → bit flips, all 3 R2 objects exist."""
    _seed_local(
        banner_settings,
        "hw/l.png",
        "hw/m.png",
        "hw/r.png",
    )

    banner = Banners(
        name="hw",
        size=BannerSize.large,
        left_image="hw/l.png",
        middle_image="hw/m.png",
        right_image="hw/r.png",
    )
    db_session.add(banner)
    await db_session.commit()
    await db_session.refresh(banner)

    report = await cmd_banners_backfill(dry_run=False)
    assert report["processed"] == 1
    assert report["flipped"] == 1

    await db_session.refresh(banner)
    assert banner.in_r2 is True

    # All three R2 objects exist with image/png content type
    async with moto_session.client("s3", endpoint_url=moto_server) as s3:
        for path in ("hw/l.png", "hw/m.png", "hw/r.png"):
            head = await s3.head_object(
                Bucket="public", Key=f"banners/{path}"
            )
            assert head["ContentType"] == "image/png"


@pytest.mark.unit
async def test_backfill_three_part_partial_missing_skips_row(
    banner_settings,
    install_r2,
    patch_session,
    db_session,
    caplog,
):
    """Only 2 of 3 files on disk → no part uploaded, bit stays False."""
    # Seed only left and middle; intentionally omit the right file.
    _seed_local(banner_settings, "partial/l.png", "partial/m.png")

    banner = Banners(
        name="partial",
        size=BannerSize.large,
        left_image="partial/l.png",
        middle_image="partial/m.png",
        right_image="partial/r.png",  # not on disk
    )
    db_session.add(banner)
    await db_session.commit()
    await db_session.refresh(banner)

    with caplog.at_level(logging.WARNING):
        report = await cmd_banners_backfill(dry_run=False)

    assert report["processed"] == 1
    assert report["flipped"] == 0
    assert report["skipped_missing_local"] == 1

    await db_session.refresh(banner)
    assert banner.in_r2 is False

    # Critically: do NOT upload left/middle either — all-or-nothing per row.
    for path in ("partial/l.png", "partial/m.png", "partial/r.png"):
        assert not await install_r2.object_exists(
            bucket="public", key=f"banners/{path}"
        ), f"unexpected R2 object for {path} (should be all-or-nothing)"

    # Warning logged for the missing path
    log_messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "banner_local_missing" in log_messages


@pytest.mark.unit
async def test_backfill_idempotent_skips_existing(
    banner_settings,
    install_r2,
    patch_session,
    db_session,
):
    """Pre-existing R2 part → no double-upload of that part, bit still flips."""
    _seed_local(
        banner_settings,
        "idem/l.png",
        "idem/m.png",
        "idem/r.png",
    )

    # Pre-upload one part with distinct bytes so we can detect a re-upload.
    await install_r2.upload_bytes(
        bucket="public",
        key="banners/idem/m.png",
        body=b"PRE-EXISTING-MIDDLE",
        content_type="image/png",
    )

    banner = Banners(
        name="idem",
        size=BannerSize.large,
        left_image="idem/l.png",
        middle_image="idem/m.png",
        right_image="idem/r.png",
    )
    db_session.add(banner)
    await db_session.commit()
    await db_session.refresh(banner)

    # Spy on upload_bytes so we can count calls and ensure the pre-existing
    # part isn't re-uploaded.
    real_upload = install_r2.__class__.upload_bytes
    upload_keys: list[str] = []

    async def _spy_upload(self, bucket: str, key: str, body: bytes, content_type: str) -> None:
        upload_keys.append(key)
        await real_upload(self, bucket=bucket, key=key, body=body, content_type=content_type)

    with patch.object(install_r2.__class__, "upload_bytes", _spy_upload):
        report = await cmd_banners_backfill(dry_run=False)

    assert report["processed"] == 1
    assert report["flipped"] == 1

    await db_session.refresh(banner)
    assert banner.in_r2 is True

    # Only the two missing parts were uploaded; pre-existing one was skipped.
    assert sorted(upload_keys) == sorted(
        ["banners/idem/l.png", "banners/idem/r.png"]
    )

    # Pre-existing object preserved (its bytes were not overwritten).
    async with install_r2._acquire_client() as s3:
        obj = await s3.get_object(Bucket="public", Key="banners/idem/m.png")
        body = await obj["Body"].read()
    assert body == b"PRE-EXISTING-MIDDLE"


@pytest.mark.unit
async def test_backfill_dry_run_writes_nothing(
    banner_settings,
    install_r2,
    patch_session,
    db_session,
):
    """--dry-run → no R2 objects created, bit stays False."""
    _seed_local(banner_settings, "dry/full.jpg")

    banner = Banners(
        name="dry",
        size=BannerSize.small,
        full_image="dry/full.jpg",
    )
    db_session.add(banner)
    await db_session.commit()
    await db_session.refresh(banner)

    report = await cmd_banners_backfill(dry_run=True)
    assert report["processed"] == 1
    assert report["flipped"] == 0
    assert report["skipped_missing_local"] == 0
    assert report["failed_rows"] == 0
    assert report["would_upload_parts"] == 1
    assert report["would_flip_rows"] == 1

    await db_session.refresh(banner)
    assert banner.in_r2 is False
    assert not await install_r2.object_exists(
        bucket="public", key="banners/dry/full.jpg"
    )


@pytest.mark.unit
async def test_backfill_dry_run_reports_would_upload_parts(
    banner_settings,
    install_r2,
    patch_session,
    db_session,
):
    """Two unbackfilled banners (1 full + 1 three-part), dry-run → 4 would-parts, 2 would-rows."""
    _seed_local(banner_settings, "wu_full/full.jpg")
    _seed_local(
        banner_settings,
        "wu_3p/l.png",
        "wu_3p/m.png",
        "wu_3p/r.png",
    )

    banner_full = Banners(
        name="wu_full",
        size=BannerSize.small,
        full_image="wu_full/full.jpg",
    )
    banner_3p = Banners(
        name="wu_3p",
        size=BannerSize.large,
        left_image="wu_3p/l.png",
        middle_image="wu_3p/m.png",
        right_image="wu_3p/r.png",
    )
    db_session.add(banner_full)
    db_session.add(banner_3p)
    await db_session.commit()
    await db_session.refresh(banner_full)
    await db_session.refresh(banner_3p)

    report = await cmd_banners_backfill(dry_run=True)

    assert report["processed"] == 2
    assert report["flipped"] == 0
    assert report["skipped_missing_local"] == 0
    assert report["failed_rows"] == 0
    assert report["would_upload_parts"] == 4
    assert report["would_flip_rows"] == 2

    # No R2 writes
    for path in ("wu_full/full.jpg", "wu_3p/l.png", "wu_3p/m.png", "wu_3p/r.png"):
        assert not await install_r2.object_exists(
            bucket="public", key=f"banners/{path}"
        )

    # No DB writes
    await db_session.refresh(banner_full)
    await db_session.refresh(banner_3p)
    assert banner_full.in_r2 is False
    assert banner_3p.in_r2 is False


@pytest.mark.unit
async def test_backfill_dry_run_three_part_mixed(
    banner_settings,
    install_r2,
    patch_session,
    db_session,
):
    """Three-part banner: 1 part pre-existing in R2, 2 parts not.

    Dry-run should report:
      - ``would_upload_parts == 2`` (the two missing parts)
      - ``would_flip_rows == 1`` (row would flip because all parts are
        either already present or would-upload)
      - ``in_r2`` stays False (dry-run suppresses the actual UPDATE)
    """
    _seed_local(
        banner_settings,
        "drymix/l.png",
        "drymix/m.png",
        "drymix/r.png",
    )

    # Pre-upload only the middle part; left and right would need uploading.
    await install_r2.upload_bytes(
        bucket="public",
        key="banners/drymix/m.png",
        body=b"PRE-EXISTING-MIDDLE",
        content_type="image/png",
    )

    banner = Banners(
        name="drymix",
        size=BannerSize.large,
        left_image="drymix/l.png",
        middle_image="drymix/m.png",
        right_image="drymix/r.png",
    )
    db_session.add(banner)
    await db_session.commit()
    await db_session.refresh(banner)

    report = await cmd_banners_backfill(dry_run=True)

    assert report["processed"] == 1
    assert report["flipped"] == 0
    assert report["skipped_missing_local"] == 0
    assert report["failed_rows"] == 0
    assert report["would_upload_parts"] == 2
    assert report["would_flip_rows"] == 1

    await db_session.refresh(banner)
    assert banner.in_r2 is False

    # The two missing parts were not actually uploaded.
    for path in ("drymix/l.png", "drymix/r.png"):
        assert not await install_r2.object_exists(
            bucket="public", key=f"banners/{path}"
        )

    # Pre-existing middle object preserved (its bytes were not overwritten).
    async with install_r2._acquire_client() as s3:
        obj = await s3.get_object(Bucket="public", Key="banners/drymix/m.png")
        body = await obj["Body"].read()
    assert body == b"PRE-EXISTING-MIDDLE"


@pytest.mark.unit
async def test_backfill_continues_on_part_upload_failure(
    banner_settings,
    install_r2,
    patch_session,
    db_session,
    caplog,
):
    """Two banners; 2nd's upload raises → 1st flipped, 2nd stays false, failed_rows == 1."""
    _seed_local(banner_settings, "ok/full.jpg")
    _seed_local(banner_settings, "boom/full.jpg")

    banner_ok = Banners(
        name="ok",
        size=BannerSize.small,
        full_image="ok/full.jpg",
    )
    banner_boom = Banners(
        name="boom",
        size=BannerSize.small,
        full_image="boom/full.jpg",
    )
    db_session.add(banner_ok)
    db_session.add(banner_boom)
    await db_session.commit()
    await db_session.refresh(banner_ok)
    await db_session.refresh(banner_boom)

    real_upload = install_r2.__class__.upload_bytes

    async def _flaky_upload(self, bucket: str, key: str, body: bytes, content_type: str) -> None:
        if key == "banners/boom/full.jpg":
            raise RuntimeError("simulated banner upload failure")
        await real_upload(self, bucket=bucket, key=key, body=body, content_type=content_type)

    with patch.object(install_r2.__class__, "upload_bytes", _flaky_upload):
        with caplog.at_level(logging.WARNING):
            report = await cmd_banners_backfill(dry_run=False)

    assert report["processed"] == 2
    assert report["flipped"] == 1
    assert report["failed_rows"] == 1
    assert report["skipped_missing_local"] == 0
    assert report["would_upload_parts"] == 0
    assert report["would_flip_rows"] == 0

    await db_session.refresh(banner_ok)
    await db_session.refresh(banner_boom)
    assert banner_ok.in_r2 is True
    assert banner_boom.in_r2 is False

    # The failure log was captured.
    log_messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "banner_r2_backfill_failed" in log_messages
