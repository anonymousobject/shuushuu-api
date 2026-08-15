"""Tests for the image upload route."""

import os
import tempfile
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.user import Users
from app.schemas.image import SimilarImageResult
from tests.snapshot_conflict import (
    _db_error,
    _flaky_flush,
    _flaky_flush_nth,
    _snapshot_conflict_error,
)


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


@contextmanager
def _mock_upload_storage(md5: str = "abc123unique"):
    """Patch the upload route's two filesystem steps: stage, then finalize.

    Uses a per-xdist-worker temp path (not a single shared file) so parallel
    workers can't touch()/unlink() one another's file mid-request — the
    duplicate and IQDB 409 paths both unlink it, while the success path stats it.

    finalize is a no-op: the staged path is already the final path here, so the
    fake file survives for whatever the test asserts on afterwards.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    fake_path = Path(tempfile.gettempdir()) / f"fake-upload-{worker}.jpg"

    async def _stage(file, storage_path):
        # Create the fake file so cleanup code (and stat()) don't error
        fake_path.touch()
        return fake_path, "jpg", md5

    def _finalize(staged_path, storage_path, image_id, ext, date_prefix):
        return staged_path

    with (
        patch("app.api.v1.images.stage_uploaded_image", side_effect=_stage),
        patch("app.api.v1.images.finalize_uploaded_image", side_effect=_finalize),
    ):
        yield


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
            _mock_upload_storage(),
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
            _mock_upload_storage("abc123unique2"),
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
            _mock_upload_storage("abc123unique3"),
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
            _mock_upload_storage("abc123unique4"),
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
            _mock_upload_storage("abc123unique5"),
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
        with _mock_upload_storage():
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
            _mock_upload_storage("abc123unique6"),
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
            _mock_upload_storage("ml_enqueue_on"),
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
            _mock_upload_storage("ml_enqueue_off"),
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
            _mock_upload_storage("ml_enqueue_fail"),
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
        with _mock_upload_storage(existing_md5):
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
            _mock_upload_storage("ipv6upload01"),
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
            _mock_upload_storage("snapshotretry1"),
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
        """A persistent 1020 gives up after a bounded number of attempts.

        The exhausted error surfaces as the route's own 500 rather than a raw
        OperationalError: the retried unit sits inside upload's try/except, so
        the failure also rolls back and unlinks the staged file.
        """
        flush_patch, calls = _flaky_flush(100, _snapshot_conflict_error())
        with (
            _mock_upload_storage("snapshotretry2"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            flush_patch,
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 500
        assert len(calls) == 3  # bounded: no infinite retry loop

    @pytest.mark.asyncio
    async def test_upload_does_not_retry_other_db_errors(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Non-1020 database errors fail immediately with no retry."""
        flush_patch, calls = _flaky_flush(100, _db_error(1213, "Deadlock found"))
        with (
            _mock_upload_storage("snapshotretry3"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            flush_patch,
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 500
        assert len(calls) == 1  # not retried


@pytest.mark.api
class TestUploadTagLinkSnapshotConflictRetry:
    """The upload's tag-link write is exposed to ER_CHECKREAD too, and for
    longer than the temp-row INSERT: tag_links/tag_history INSERTs take locking
    reads on their FK parents, and the usage_count trigger on tag_links keeps
    the parent `tags` row moving whenever anyone else tags that tag.

    The conflict is aimed at the second explicit flush (the tag-link write)
    rather than the first (which mints the image_id), so this fails on any
    implementation that only retries the id-minting INSERT.
    """

    @pytest.mark.asyncio
    @pytest.mark.needs_commit
    async def test_upload_with_tags_retries_snapshot_conflict_and_succeeds(
        self,
        upload_client: AsyncClient,
        verified_user: Users,
        db_session: AsyncSession,
    ):
        """A transient 1020 on the tag-link write is retried and the upload succeeds."""
        from sqlalchemy import select

        from app.models.image import Images
        from app.models.tag import Tags
        from app.models.tag_link import TagLinks

        tag = Tags(title="upload_snapshot_retry", type=1)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)
        tag_id: int = tag.tag_id

        flush_patch, calls = _flaky_flush_nth(2, _snapshot_conflict_error("tag_history"))
        with (
            _mock_upload_storage("tagsnapshotretry1"),
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
                data={"tag_ids": str(tag_id), "caption": ""},
            )

        assert response.status_code == 201, response.text
        assert len(calls) >= 3  # id flush, failed tag flush, then the retry

        image_id = response.json()["image"]["image_id"]
        assert image_id > 0

        # The tag landed exactly once on the surviving image.
        links = await db_session.execute(
            select(TagLinks).where(TagLinks.image_id == image_id, TagLinks.tag_id == tag_id)
        )
        assert len(list(links.scalars().all())) == 1

        # The abandoned attempt left no image row behind.
        images = await db_session.execute(
            select(Images).where(Images.md5_hash == "tagsnapshotretry1")
        )
        assert len(list(images.scalars().all())) == 1


@pytest.mark.api
class TestUploadRealStorageRoundTrip:
    """The route with its filesystem steps UNMOCKED, against a real temp dir.

    Every other upload test replaces stage/finalize, so this is the only cover
    for the seam between them: that the name the row records is the name the
    file ends up with, and that no staged file is left behind.
    """

    @pytest.mark.asyncio
    async def test_upload_names_the_file_to_match_the_row(
        self,
        upload_client: AsyncClient,
        verified_user: Users,
        db_session: AsyncSession,
        tmp_path: Path,
        monkeypatch,
    ):
        """The saved file is YYYY-MM-DD-{image_id}.jpg and the row agrees."""
        from app.config import settings

        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

        with (
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("real_round_trip.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 201, response.text
        image = response.json()["image"]
        image_id = image["image_id"]

        # The row's filename embeds the id the INSERT minted...
        assert image["filename"].endswith(f"-{image_id}")
        assert image["ext"] == "jpg"
        # ...and dimensions came from the real file, not a mock.
        assert (image["width"], image["height"]) == (100, 100)

        # ...and the file on disk carries exactly that name.
        fullsize = tmp_path / "fullsize"
        assert (fullsize / f"{image['filename']}.jpg").exists()

        # Nothing was orphaned under a staging name.
        assert list(fullsize.glob("staged_*")) == []
