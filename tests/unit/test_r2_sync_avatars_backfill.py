"""Tests for ``scripts/r2_sync.py avatars-backfill`` subcommand.

Exercises ``cmd_avatars_backfill`` end-to-end against a real moto S3 server
(via the shared ``setup_buckets``/``moto_session``/``moto_server`` fixtures in
``tests/unit/conftest.py``) plus the real ``db_session`` fixture from the
parent conftest. ``get_async_session`` is patched to yield the test session
so the script's DB reads see the seeded users; ``r2_client._instance`` is
swapped for the moto-wired R2Storage so ``get_r2_storage()`` returns it.
"""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import r2_client
from app.models.user import Users
from scripts.r2_sync import (
    BulkBackfillDisallowedError,
    cmd_avatars_backfill,
    require_bulk_backfill,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _mock_session_cm(db_session: AsyncSession):
    """Async-context-manager mock that yields the real test session.

    Mirrors the helper in ``tests/unit/test_r2_sync_remaining.py`` — the
    backfill code uses ``async with get_async_session() as db:``, so we need
    to swap that callable for one whose result yields ``db_session`` from
    ``__aenter__``.
    """
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=db_session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.fixture
def avatar_settings(monkeypatch, tmp_path: Path) -> Path:
    """Wire the avatar-relevant settings to tmp_path / public bucket / R2 enabled."""
    monkeypatch.setattr(settings, "AVATAR_STORAGE_PATH", str(tmp_path))
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


def _make_user(
    db_session: AsyncSession,
    *,
    username: str,
    email: str,
    avatar: str = "",
    avatar_in_r2: bool = False,
) -> Users:
    user = Users(
        username=username,
        password="hashed",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email=email,
        avatar=avatar,
        avatar_in_r2=avatar_in_r2,
    )
    db_session.add(user)
    return user


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_backfill_uploads_and_flips_bit(
    avatar_settings,
    install_r2,
    patch_session,
    db_session,
    moto_session,
    moto_server,
):
    """Two users, both bit=False, both files on disk → both bit=True + uploaded."""
    fname_a = "alpha000000000000000000000000aaaa.png"
    fname_b = "beta0000000000000000000000000bbbb.png"
    (avatar_settings / fname_a).write_bytes(b"\x89PNG-a")
    (avatar_settings / fname_b).write_bytes(b"\x89PNG-b")

    user_a = _make_user(
        db_session, username="bf_a", email="bf_a@x.test", avatar=fname_a
    )
    user_b = _make_user(
        db_session, username="bf_b", email="bf_b@x.test", avatar=fname_b
    )
    await db_session.commit()

    report = await cmd_avatars_backfill(dry_run=False, concurrency=4)
    assert report["uploaded"] == 2
    assert report["skipped_existing"] == 0
    assert report["missing_local"] == 0

    await db_session.refresh(user_a)
    await db_session.refresh(user_b)
    assert user_a.avatar_in_r2 is True
    assert user_b.avatar_in_r2 is True

    # Both R2 objects exist with image/png content type
    async with moto_session.client("s3", endpoint_url=moto_server) as s3:
        for fname in (fname_a, fname_b):
            head = await s3.head_object(Bucket="public", Key=f"avatars/{fname}")
            assert head["ContentType"] == "image/png"


@pytest.mark.unit
async def test_backfill_idempotent_skips_existing(
    avatar_settings,
    install_r2,
    patch_session,
    db_session,
):
    """Pre-existing R2 object → no re-upload, but bit still flips to True."""
    fname = "idem0000000000000000000000000000.png"
    (avatar_settings / fname).write_bytes(b"local-bytes")

    # Pre-seed R2 with different bytes so we can detect a re-upload by content.
    await install_r2.upload_bytes(
        bucket="public",
        key=f"avatars/{fname}",
        body=b"r2-pre-existing-bytes",
        content_type="image/png",
    )

    user = _make_user(
        db_session, username="bf_idem", email="bf_idem@x.test", avatar=fname
    )
    await db_session.commit()

    # Spy on upload_bytes to assert the script did NOT call it.
    with patch.object(
        install_r2.__class__,
        "upload_bytes",
        AsyncMock(side_effect=AssertionError("upload_bytes must not be called")),
    ):
        report = await cmd_avatars_backfill(dry_run=False, concurrency=2)

    assert report["uploaded"] == 0
    assert report["skipped_existing"] == 1
    assert report["missing_local"] == 0

    await db_session.refresh(user)
    assert user.avatar_in_r2 is True


@pytest.mark.unit
async def test_backfill_skips_when_local_missing(
    avatar_settings,
    install_r2,
    patch_session,
    db_session,
    caplog,
):
    """Avatar filename in DB but no local file → log + bit stays False."""
    fname = "missing0000000000000000000000000.png"
    # Intentionally do NOT write the file to disk.

    user = _make_user(
        db_session, username="bf_miss", email="bf_miss@x.test", avatar=fname
    )
    await db_session.commit()

    with caplog.at_level(logging.WARNING):
        report = await cmd_avatars_backfill(dry_run=False, concurrency=2)

    assert report["uploaded"] == 0
    assert report["skipped_existing"] == 0
    assert report["missing_local"] == 1

    await db_session.refresh(user)
    assert user.avatar_in_r2 is False

    # No R2 object created
    assert not await install_r2.object_exists(
        bucket="public", key=f"avatars/{fname}"
    )

    # Warning log emitted
    log_messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "avatar_local_missing" in log_messages


@pytest.mark.unit
async def test_backfill_dry_run_writes_nothing(
    avatar_settings,
    install_r2,
    patch_session,
    db_session,
):
    """--dry-run → no R2 object, bit stays False."""
    fname = "dryrun00000000000000000000000000.png"
    (avatar_settings / fname).write_bytes(b"\x89PNG-dry")

    user = _make_user(
        db_session, username="bf_dry", email="bf_dry@x.test", avatar=fname
    )
    await db_session.commit()

    report = await cmd_avatars_backfill(dry_run=True, concurrency=2)

    # No actual upload; the would-be upload is reflected in `would_upload`.
    assert report["uploaded"] == 0
    assert report["skipped_existing"] == 0
    assert report["missing_local"] == 0
    assert report["would_upload"] == 1
    assert report["failed"] == 0

    await db_session.refresh(user)
    assert user.avatar_in_r2 is False
    assert not await install_r2.object_exists(
        bucket="public", key=f"avatars/{fname}"
    )


@pytest.mark.unit
async def test_backfill_dry_run_reports_would_upload(
    avatar_settings,
    install_r2,
    patch_session,
    db_session,
):
    """Two unbackfilled users in dry-run → would_upload == 2, no writes."""
    fname_a = "wouldup0000000000000000000000aaa.png"
    fname_b = "wouldup0000000000000000000000bbb.png"
    (avatar_settings / fname_a).write_bytes(b"\x89PNG-a")
    (avatar_settings / fname_b).write_bytes(b"\x89PNG-b")

    user_a = _make_user(
        db_session, username="bf_wa", email="bf_wa@x.test", avatar=fname_a
    )
    user_b = _make_user(
        db_session, username="bf_wb", email="bf_wb@x.test", avatar=fname_b
    )
    await db_session.commit()

    report = await cmd_avatars_backfill(dry_run=True, concurrency=4)

    assert report["would_upload"] == 2
    assert report["uploaded"] == 0
    assert report["skipped_existing"] == 0
    assert report["missing_local"] == 0
    assert report["failed"] == 0

    await db_session.refresh(user_a)
    await db_session.refresh(user_b)
    assert user_a.avatar_in_r2 is False
    assert user_b.avatar_in_r2 is False
    for fname in (fname_a, fname_b):
        assert not await install_r2.object_exists(
            bucket="public", key=f"avatars/{fname}"
        )


@pytest.mark.unit
async def test_backfill_continues_on_upload_failure(
    avatar_settings,
    install_r2,
    patch_session,
    db_session,
    caplog,
):
    """One upload raises → run continues; that user not flipped, others are."""
    fname_a = "fail0000000000000000000000000aaa.png"
    fname_b = "fail0000000000000000000000000bbb.png"
    fname_c = "fail0000000000000000000000000ccc.png"
    for fname in (fname_a, fname_b, fname_c):
        (avatar_settings / fname).write_bytes(b"\x89PNG-" + fname[:1].encode())

    user_a = _make_user(
        db_session, username="bf_fa", email="bf_fa@x.test", avatar=fname_a
    )
    user_b = _make_user(
        db_session, username="bf_fb", email="bf_fb@x.test", avatar=fname_b
    )
    user_c = _make_user(
        db_session, username="bf_fc", email="bf_fc@x.test", avatar=fname_c
    )
    await db_session.commit()

    real_upload = install_r2.__class__.upload_bytes
    call_count = {"n": 0}

    async def _flaky_upload(self, bucket: str, key: str, body: bytes, content_type: str) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated upload failure")
        await real_upload(self, bucket=bucket, key=key, body=body, content_type=content_type)

    with patch.object(install_r2.__class__, "upload_bytes", _flaky_upload):
        with caplog.at_level(logging.WARNING):
            # concurrency=1 makes ordering deterministic so we know exactly
            # which call is the 2nd (and which user lands in `failed`).
            report = await cmd_avatars_backfill(dry_run=False, concurrency=1)

    assert report["uploaded"] == 2
    assert report["failed"] == 1
    assert report["skipped_existing"] == 0
    assert report["missing_local"] == 0
    assert report["would_upload"] == 0

    # Refresh all three users; exactly two have the bit set.
    await db_session.refresh(user_a)
    await db_session.refresh(user_b)
    await db_session.refresh(user_c)
    flipped = [u.avatar_in_r2 for u in (user_a, user_b, user_c)]
    assert sum(1 for f in flipped if f is True) == 2
    assert sum(1 for f in flipped if f is False) == 1

    # The failure log was captured.
    log_messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "avatar_r2_backfill_failed" in log_messages


@pytest.mark.unit
async def test_backfill_dry_run_existing_object_does_not_flip(
    avatar_settings,
    install_r2,
    patch_session,
    db_session,
):
    """Pre-existing R2 object + dry-run → skipped_existing >= 1, bit stays False.

    Locks in the dry-run-suppresses-flip behavior: even though
    ``skipped_existing`` outcomes do append to ``flip_user_ids`` internally,
    the outer ``if not dry_run`` guard prevents the batched UPDATE from
    running.
    """
    fname = "dryexist000000000000000000000000.png"
    (avatar_settings / fname).write_bytes(b"local-bytes")

    # Pre-seed R2 so the script's object_exists() check returns True.
    await install_r2.upload_bytes(
        bucket="public",
        key=f"avatars/{fname}",
        body=b"r2-pre-existing-bytes",
        content_type="image/png",
    )

    user = _make_user(
        db_session,
        username="bf_dryexist",
        email="bf_dryexist@x.test",
        avatar=fname,
    )
    await db_session.commit()

    report = await cmd_avatars_backfill(dry_run=True, concurrency=2)

    assert report["skipped_existing"] >= 1
    assert report["uploaded"] == 0
    assert report["would_upload"] == 0
    assert report["missing_local"] == 0
    assert report["failed"] == 0

    await db_session.refresh(user)
    assert user.avatar_in_r2 is False


@pytest.mark.unit
def test_backfill_refuses_without_bulk_flag(monkeypatch):
    """R2_ALLOW_BULK_BACKFILL=false → require_bulk_backfill raises."""
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", False)
    with pytest.raises(BulkBackfillDisallowedError):
        require_bulk_backfill()
