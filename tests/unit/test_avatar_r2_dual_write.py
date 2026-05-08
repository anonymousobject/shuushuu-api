"""Dual-write integration tests for avatar upload.

Exercises ``_upload_avatar`` end-to-end against a real moto S3 server (via the
shared ``setup_buckets``/``moto_session``/``moto_server`` fixtures in
``tests/unit/conftest.py``) plus the real ``db_session`` fixture from the
parent conftest. R2 behaviour is tested against a live mock — only the
fallback semantics use ``patch`` to force ``upload_bytes`` to raise.
"""

import io
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import UploadFile
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app.api.v1.users import _upload_avatar
from app.config import settings
from app.core import r2_client
from app.models.user import Users

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _png_bytes(color: str = "red") -> bytes:
    """Return a small PNG payload."""
    img = Image.new("RGB", (50, 50), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_upload(name: str, data: bytes, content_type: str = "image/png") -> UploadFile:
    """Build a Starlette/FastAPI UploadFile from in-memory bytes."""
    return UploadFile(
        file=io.BytesIO(data),
        filename=name,
        size=len(data),
        headers=Headers({"content-type": content_type}),
    )


@pytest.fixture
async def avatar_user(db_session: AsyncSession) -> Users:
    """Insert a fresh user with no avatar set."""
    user = Users(
        username="dualwrite_user",
        password="hashed",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="dualwrite@example.com",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
def avatar_settings(monkeypatch, tmp_path: Path):
    """Wire the avatar-relevant settings to tmp_path / R2 enabled / a moto bucket name.

    Tests opt in to R2_ENABLED individually (some test the R2_ENABLED=false
    branch); this fixture sets the local-storage path and the bucket name
    only.
    """
    monkeypatch.setattr(settings, "AVATAR_STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "MAX_AVATAR_SIZE", 1 * 1024 * 1024)
    monkeypatch.setattr(settings, "MAX_AVATAR_DIMENSION", 200)
    monkeypatch.setattr(settings, "R2_PUBLIC_BUCKET", "public")
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://local.test")
    return tmp_path


@pytest.fixture
def install_r2(monkeypatch, setup_buckets):
    """Install the moto-backed R2Storage as the process singleton.

    ``setup_buckets`` is the moto-wired R2Storage with ``public`` and
    ``private`` buckets pre-created. We swap it into the ``r2_client._instance``
    cache so any ``get_r2_storage()`` call returns it instead of opening a
    real aioboto3 session.
    """
    monkeypatch.setattr(r2_client, "_instance", setup_buckets)
    return setup_buckets


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_dual_write_success_sets_bit(
    avatar_user, avatar_settings, install_r2, db_session, monkeypatch, moto_session, moto_server
):
    """R2 enabled + moto bucket exists → local file + R2 object + bit=True."""
    monkeypatch.setattr(settings, "R2_ENABLED", True)

    payload = _png_bytes("red")
    upload = _make_upload("test.png", payload)

    response = await _upload_avatar(avatar_user.user_id, upload, db_session)

    # Local file present
    assert response.avatar
    assert response.avatar.endswith(".png")
    local = avatar_settings / response.avatar
    assert local.exists(), "expected local avatar file to be saved"

    # User row reflects R2 success
    await db_session.refresh(avatar_user)
    assert avatar_user.avatar == response.avatar
    assert avatar_user.avatar_in_r2 is True

    # R2 object present and content-type is image/png
    key = f"avatars/{response.avatar}"
    assert await install_r2.object_exists(bucket="public", key=key)
    async with moto_session.client("s3", endpoint_url=moto_server) as s3:
        head = await s3.head_object(Bucket="public", Key=key)
        assert head["ContentType"] == "image/png"


@pytest.mark.unit
async def test_dual_write_r2_failure_falls_back(
    avatar_user, avatar_settings, install_r2, db_session, monkeypatch, caplog
):
    """When upload_bytes raises, the request still succeeds with bit=False."""
    monkeypatch.setattr(settings, "R2_ENABLED", True)

    # Force upload_bytes to raise. We patch the bound method on the live
    # R2Storage instance the singleton points at — this is testing the
    # fallback semantics, not R2 behaviour, so a forced raise is appropriate
    # under AGENTS.md.
    async def _boom(self, bucket: str, key: str, body: bytes, content_type: str) -> None:
        raise RuntimeError("simulated R2 outage")

    with patch.object(install_r2.__class__, "upload_bytes", _boom):
        with caplog.at_level(logging.WARNING):
            payload = _png_bytes("blue")
            upload = _make_upload("test.png", payload)
            response = await _upload_avatar(avatar_user.user_id, upload, db_session)

    # Local file still saved
    assert response.avatar
    local = avatar_settings / response.avatar
    assert local.exists()

    # Bit stays False
    await db_session.refresh(avatar_user)
    assert avatar_user.avatar == response.avatar
    assert avatar_user.avatar_in_r2 is False

    # R2 object should NOT exist (the put_object never landed)
    assert not await install_r2.object_exists(bucket="public", key=f"avatars/{response.avatar}")

    # Failure log emitted
    log_messages = " ".join(record.getMessage() for record in caplog.records)
    assert "avatar_r2_upload_failed" in log_messages


@pytest.mark.unit
async def test_orphan_delete_clears_r2_when_old_in_r2(
    avatar_user, avatar_settings, install_r2, db_session, monkeypatch
):
    """Replacing an avatar where old_in_r2=True deletes the old R2 object."""
    monkeypatch.setattr(settings, "R2_ENABLED", True)

    # Pre-seed: user has an old avatar present in R2
    old_payload = _png_bytes("green")
    old_filename = "deadbeef000000000000000000000000.png"
    (avatar_settings / old_filename).write_bytes(old_payload)
    await install_r2.upload_bytes(
        bucket="public",
        key=f"avatars/{old_filename}",
        body=old_payload,
        content_type="image/png",
    )
    avatar_user.avatar = old_filename
    avatar_user.avatar_in_r2 = True
    await db_session.commit()
    await db_session.refresh(avatar_user)

    assert await install_r2.object_exists(bucket="public", key=f"avatars/{old_filename}")

    # New upload — different bytes, so different MD5 / new key
    new_payload = _png_bytes("blue")
    upload = _make_upload("new.png", new_payload)
    response = await _upload_avatar(avatar_user.user_id, upload, db_session)

    assert response.avatar != old_filename

    # Old R2 object cleaned up
    assert not await install_r2.object_exists(bucket="public", key=f"avatars/{old_filename}")
    # Old local file cleaned up
    assert not (avatar_settings / old_filename).exists()
    # New R2 object exists
    assert await install_r2.object_exists(bucket="public", key=f"avatars/{response.avatar}")


@pytest.mark.unit
async def test_orphan_delete_skips_r2_when_old_in_r2_false(
    avatar_user, avatar_settings, install_r2, db_session, monkeypatch
):
    """When old_in_r2=False, no R2 delete fires for the old key."""
    monkeypatch.setattr(settings, "R2_ENABLED", True)

    # Pre-seed: user has an old avatar locally only — no R2 object
    old_payload = _png_bytes("green")
    old_filename = "cafebabe000000000000000000000000.png"
    (avatar_settings / old_filename).write_bytes(old_payload)
    avatar_user.avatar = old_filename
    avatar_user.avatar_in_r2 = False
    await db_session.commit()
    await db_session.refresh(avatar_user)

    # Spy on delete_object so we can assert it was NOT called for the old key
    delete_calls: list[tuple[str, str]] = []
    real_delete = install_r2.delete_object

    async def _spy_delete(bucket: str, key: str) -> None:
        delete_calls.append((bucket, key))
        await real_delete(bucket=bucket, key=key)

    with patch.object(install_r2, "delete_object", _spy_delete):
        new_payload = _png_bytes("blue")
        upload = _make_upload("new.png", new_payload)
        response = await _upload_avatar(avatar_user.user_id, upload, db_session)

    assert response.avatar != old_filename

    # No R2 delete attempted for the old key
    old_key = f"avatars/{old_filename}"
    assert not any(bucket == "public" and key == old_key for bucket, key in delete_calls), (
        f"unexpected R2 delete on old key: {delete_calls!r}"
    )

    # Local old file still deleted by the orphan helper
    assert not (avatar_settings / old_filename).exists()


@pytest.mark.unit
async def test_same_md5_reupload_preserves_file(
    avatar_user, avatar_settings, install_r2, db_session, monkeypatch
):
    """Re-uploading identical bytes leaves the existing file/R2 object intact."""
    monkeypatch.setattr(settings, "R2_ENABLED", True)

    # First upload — establishes the avatar in both local and R2
    payload = _png_bytes("red")
    upload = _make_upload("first.png", payload)
    first = await _upload_avatar(avatar_user.user_id, upload, db_session)

    await db_session.refresh(avatar_user)
    assert avatar_user.avatar_in_r2 is True
    first_filename = first.avatar
    assert (avatar_settings / first_filename).exists()
    assert await install_r2.object_exists(bucket="public", key=f"avatars/{first_filename}")

    # Second upload — identical bytes, same MD5, same key
    upload2 = _make_upload("first.png", payload)
    second = await _upload_avatar(avatar_user.user_id, upload2, db_session)

    assert second.avatar == first_filename, "MD5-addressed filename must match"

    # Critical: orphan check after the commit must see the user themselves
    # still referencing first_filename (count >= 1) and skip deletion.
    assert (avatar_settings / first_filename).exists(), (
        "local file deleted despite the user still referencing it"
    )
    assert await install_r2.object_exists(bucket="public", key=f"avatars/{first_filename}"), (
        "R2 object deleted despite the user still referencing it"
    )

    await db_session.refresh(avatar_user)
    assert avatar_user.avatar == first_filename
    assert avatar_user.avatar_in_r2 is True
