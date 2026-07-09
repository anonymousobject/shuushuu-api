"""Tests for the image upload route."""

import os
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pymysql
import pytest
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.user import Users
from app.schemas.image import SimilarImageResult


@pytest.fixture
async def verified_user(db_session: AsyncSession) -> Users:
    """Create a verified user for upload testing."""
    user = Users(
        username="uploader",
        password="hashed_password_here",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="uploader@example.com",
        active=1,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def upload_client(client: AsyncClient, verified_user: Users) -> AsyncClient:
    """Authenticated client with a verified user."""
    access_token = create_access_token(verified_user.id)
    client.headers.update({"Authorization": f"Bearer {access_token}"})
    return client


def _fake_image_bytes() -> bytes:
    """Create a minimal valid JPEG for upload tests."""
    from PIL import Image

    img = Image.new("RGB", (100, 100), color="red")
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_similar_result(image_id: int, score: float) -> SimilarImageResult:
    """Build a SimilarImageResult for test assertions."""
    return SimilarImageResult(
        image_id=image_id,
        filename=f"2025-01-01-{image_id}",
        ext="jpg",
        md5_hash="fakehash",
        filesize=1000,
        width=100,
        height=100,
        rating=0.0,
        user_id=1,
        date_added="2025-01-01T00:00:00",
        status=1,
        locked=0,
        posts=0,
        favorites=0,
        bayesian_rating=0.0,
        num_ratings=0,
        medium=0,
        large=0,
        similarity_score=score,
    )


def _mock_save_uploaded_image(md5: str = "abc123unique"):
    """Create an AsyncMock for save_uploaded_image that returns a fake path.

    Uses a per-xdist-worker temp path (not a single shared file) so parallel
    workers can't touch()/unlink() one another's file mid-request — the
    duplicate and IQDB 409 paths both unlink it, while the success path stats it.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    fake_path = Path(tempfile.gettempdir()) / f"fake-upload-{worker}.jpg"

    async def _save(file, storage_path, image_id):
        # Create the fake file so cleanup code (and stat()) don't error
        fake_path.touch()
        return fake_path, "jpg", md5

    return patch("app.api.v1.images.save_uploaded_image", side_effect=_save)


class TestUploadIQDBDuplicateDetection:
    """Tests for IQDB near-duplicate detection during upload."""

    @pytest.mark.asyncio
    async def test_upload_returns_409_when_similar_images_found(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload returns 409 with similar images when IQDB finds near-duplicates."""
        hydrated = [
            _make_similar_result(42, 95.5),
            _make_similar_result(99, 91.0),
        ]

        with (
            _mock_save_uploaded_image(),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[{"image_id": 42, "score": 95.5}, {"image_id": 99, "score": 91.0}],
            ),
            patch(
                "app.api.v1.images._hydrate_similar_images",
                new_callable=AsyncMock,
                return_value=hydrated,
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 409
        data = response.json()
        assert "similar_images" in data
        assert len(data["similar_images"]) == 2
        assert data["similar_images"][0]["image_id"] == 42
        assert data["similar_images"][0]["similarity_score"] == 95.5
        assert data["similar_images"][1]["image_id"] == 99
        assert data["similar_images"][1]["similarity_score"] == 91.0

    @pytest.mark.asyncio
    async def test_upload_succeeds_with_confirm_similar(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload succeeds when confirm_similar=true, skipping IQDB check."""
        mock_iqdb = AsyncMock(return_value=[])

        with (
            _mock_save_uploaded_image("abc123unique2"),
            patch("app.api.v1.images.check_iqdb_similarity", mock_iqdb),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": "", "confirm_similar": "true"},
            )

        assert response.status_code == 201
        # IQDB should not have been called
        mock_iqdb.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_succeeds_when_no_iqdb_matches(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload succeeds normally when IQDB finds no near-duplicates."""
        with (
            _mock_save_uploaded_image("abc123unique3"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 201
        data = response.json()
        assert "similar_images" not in data

    @pytest.mark.asyncio
    async def test_upload_stores_and_returns_miscmeta(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload with miscmeta parameter stores it and returns it in the response."""
        with (
            _mock_save_uploaded_image("abc123unique4"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": "", "miscmeta": "pixiv: 12345"},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["image"]["miscmeta"] == "pixiv: 12345"

    @pytest.mark.asyncio
    async def test_upload_persists_and_returns_source_url(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload with source_url stores it and returns it in the response."""
        with (
            _mock_save_uploaded_image("abc123unique5"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={
                    "tag_ids": "",
                    "source_url": "https://www.pixiv.net/artworks/138823691",
                },
            )

        assert response.status_code == 201, response.text
        data = response.json()
        assert data["image"]["source_url"] == "https://www.pixiv.net/artworks/138823691"

    @pytest.mark.asyncio
    async def test_upload_rejects_non_http_source_url(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload rejects a source_url that isn't http(s) with a 422."""
        with _mock_save_uploaded_image():
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "source_url": "javascript:alert(1)"},
            )

        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_upload_whitespace_source_url_normalizes_to_none(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload with whitespace-only source_url normalizes it to None."""
        with (
            _mock_save_uploaded_image("abc123unique6"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "source_url": "   "},
            )

        assert response.status_code == 201, response.text
        data = response.json()
        assert data["image"]["source_url"] is None


class TestUploadMLTagSuggestions:
    """Tests for ML tag suggestion job enqueueing on upload."""

    @pytest.mark.asyncio
    async def test_upload_enqueues_ml_job_when_flag_enabled(
        self, upload_client: AsyncClient, verified_user: Users, monkeypatch
    ):
        """When ML_TAG_SUGGESTIONS_ENABLED=True, upload enqueues generate_ml_tag_suggestions."""
        from app.config import settings

        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

        enqueue_mock = AsyncMock()

        with (
            _mock_save_uploaded_image("ml_enqueue_on"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", enqueue_mock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 201
        ml_calls = [
            call
            for call in enqueue_mock.call_args_list
            if call.args and call.args[0] == "generate_ml_tag_suggestions"
        ]
        assert len(ml_calls) == 1, (
            f"Expected 1 ml job call, got {len(ml_calls)}: {enqueue_mock.call_args_list}"
        )
        image_id = response.json()["image"]["image_id"]
        assert ml_calls[0].kwargs.get("image_id") == image_id
        assert ml_calls[0].kwargs.get("_defer_by") is None  # runs immediately, no defer

    @pytest.mark.asyncio
    async def test_upload_does_not_enqueue_ml_job_when_flag_disabled(
        self, upload_client: AsyncClient, verified_user: Users, monkeypatch
    ):
        """When ML_TAG_SUGGESTIONS_ENABLED=False (default), upload does not enqueue the ml job."""
        from app.config import settings

        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", False)

        enqueue_mock = AsyncMock()

        with (
            _mock_save_uploaded_image("ml_enqueue_off"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", enqueue_mock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 201
        ml_calls = [
            call
            for call in enqueue_mock.call_args_list
            if call.args and call.args[0] == "generate_ml_tag_suggestions"
        ]
        assert len(ml_calls) == 0, f"Expected no ml job calls, got: {enqueue_mock.call_args_list}"

    @pytest.mark.asyncio
    async def test_upload_succeeds_when_ml_enqueue_fails(
        self, upload_client: AsyncClient, verified_user: Users, monkeypatch
    ):
        """Upload still succeeds (201) when the ml tag suggestion enqueue raises an exception."""
        from app.config import settings

        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

        def _side_effect(job_name, **kwargs):
            if job_name == "generate_ml_tag_suggestions":
                raise RuntimeError("arq unavailable")
            return None

        enqueue_mock = AsyncMock(side_effect=_side_effect)

        with (
            _mock_save_uploaded_image("ml_enqueue_fail"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", enqueue_mock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 201
        non_ml_calls = [
            c
            for c in enqueue_mock.call_args_list
            if not (c.args and c.args[0] == "generate_ml_tag_suggestions")
        ]
        assert non_ml_calls, "other enqueue jobs should still have been called"


class TestUploadMD5DuplicateDetection:
    """Tests for exact-duplicate (MD5) detection during upload."""

    @pytest.mark.asyncio
    async def test_upload_returns_409_with_existing_image_id_on_md5_duplicate(
        self, upload_client: AsyncClient, test_image, verified_user: Users
    ):
        """An exact MD5 duplicate returns 409 carrying the existing image's ID as a
        structured field (so the frontend can link to it), alongside the
        human-readable detail message.
        """
        # Capture before the call: the duplicate path rolls back the (shared, in
        # tests) session, which would expire the fixture instance afterwards.
        existing_md5 = test_image.md5_hash
        expected_id = test_image.image_id

        # save_uploaded_image is mocked to yield the md5 of an image that already
        # exists in the DB (the test_image fixture), triggering the duplicate path.
        with _mock_save_uploaded_image(existing_md5):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("dup.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 409, response.text
        data = response.json()
        assert data["existing_image_id"] == expected_id
        # detail remains a human-readable string carrying the id
        assert "detail" in data
        assert str(expected_id) in data["detail"]


class TestUploadClientIPHandling:
    """Tests for client IP header handling on upload."""

    @pytest.mark.asyncio
    async def test_upload_succeeds_for_ipv6_client(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload succeeds when X-Forwarded-For carries an IPv6 address.

        Cloudflare forwards the real client IP via X-Forwarded-For; IPv6
        addresses are up to 39 chars (45 with zone-id), so the Images.ip
        column must accommodate them.
        """
        ipv6 = "2600:6c63:ff0:6810:c042:21d5:bfed:9bae"
        with (
            _mock_save_uploaded_image("ipv6upload01"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
                headers={"X-Forwarded-For": ipv6},
            )

        assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_images_source_url_roundtrip(db_session: AsyncSession):
    """source_url column persists and reads back."""
    from app.models.image import Images

    image = Images(
        filename="source-url-roundtrip.jpg",
        ext="jpg",
        md5_hash="d41d8cd98f00b204e9800998ecf8427e",
        filesize=123,
        user_id=1,
        source_url="https://www.pixiv.net/artworks/138823691",
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    assert image.source_url == "https://www.pixiv.net/artworks/138823691"


def _db_error(errno: int, message: str) -> OperationalError:
    """Build the sqlalchemy error the aiomysql/pymysql driver raises for `errno`."""
    return OperationalError(
        "INSERT INTO images ...", None, pymysql.err.OperationalError(errno, message)
    )


def _snapshot_conflict_error() -> OperationalError:
    """The error MariaDB raises under innodb_snapshot_isolation (ER_CHECKREAD)."""
    return _db_error(1020, "Record has changed since last read in table 'images'")


def _flaky_flush(fail_times: int, error: OperationalError):
    """Patch AsyncSession.flush to raise `error` for the first `fail_times`
    calls, then delegate to the real flush. Only the route's explicit
    `await db.flush()` goes through AsyncSession.flush (autoflush runs inside
    the sync Session), so the first intercepted call is the temp-row INSERT.
    Returns (patch_ctx, calls) where calls records each intercepted flush."""
    real_flush = AsyncSession.flush
    calls: list[int] = []

    async def flush(self, *args, **kwargs):
        calls.append(1)
        if len(calls) <= fail_times:
            raise error
        await real_flush(self, *args, **kwargs)

    return patch.object(AsyncSession, "flush", flush), calls


class TestUploadSnapshotConflictRetry:
    """Concurrent uploads trip MariaDB ER_CHECKREAD (errno 1020) on the temp-row
    INSERT: with innodb_snapshot_isolation=ON, a locking insert that meets index
    entries committed after this transaction's snapshot aborts instead of
    proceeding, and every in-flight upload writes identical placeholder values
    into the same index positions. The upload route must retry on a fresh
    snapshot instead of surfacing a 500."""

    @pytest.mark.asyncio
    @pytest.mark.needs_commit
    async def test_upload_retries_snapshot_conflict_and_succeeds(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """A transient 1020 on the temp-row INSERT is retried and the upload succeeds.

        needs_commit: the retry performs a real session rollback to obtain a
        fresh snapshot; under the default SAVEPOINT isolation that rollback
        would unwind the fixture's user row too (FK 1452 on the retried
        INSERT), which can't happen in production where the user is durably
        committed.
        """
        flush_patch, calls = _flaky_flush(1, _snapshot_conflict_error())
        with (
            _mock_save_uploaded_image("snapshotretry1"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
            flush_patch,
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 201, response.text
        assert response.json()["image"]["image_id"] > 0
        assert len(calls) >= 2  # failed attempt + successful retry

    @pytest.mark.asyncio
    async def test_upload_gives_up_after_bounded_retries(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """A persistent 1020 propagates after a bounded number of attempts."""
        flush_patch, calls = _flaky_flush(100, _snapshot_conflict_error())
        with (
            _mock_save_uploaded_image("snapshotretry2"),
            flush_patch,
            pytest.raises(OperationalError),
        ):
            await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert len(calls) == 3  # bounded: no infinite retry loop

    @pytest.mark.asyncio
    async def test_upload_does_not_retry_other_db_errors(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Non-1020 database errors propagate immediately with no retry."""
        flush_patch, calls = _flaky_flush(100, _db_error(1213, "Deadlock found"))
        with (
            _mock_save_uploaded_image("snapshotretry3"),
            flush_patch,
            pytest.raises(OperationalError),
        ):
            await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert len(calls) == 1  # not retried
