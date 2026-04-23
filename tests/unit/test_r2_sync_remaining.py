"""Tests for the remaining r2_sync.py subcommands."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from scripts.r2_sync import (
    BulkBackfillDisallowedError,
    force_reupload_image,
    health,
    purge_cache_command,
    reconcile,
    resync_image,
    verify,
)


def _mock_session_cm(db_session):
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


def _attach_bulk_session(mock_r2: AsyncMock) -> AsyncMock:
    """Wire a no-op bulk_session() async CM onto an existing r2 mock."""
    bulk_cm = AsyncMock()
    bulk_cm.__aenter__ = AsyncMock(return_value=mock_r2)
    bulk_cm.__aexit__ = AsyncMock(return_value=False)
    mock_r2.bulk_session = Mock(return_value=bulk_cm)
    return mock_r2


@pytest.mark.unit
class TestReconcileGuard:
    async def test_requires_bulk_backfill_flag(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", False)
        with pytest.raises(BulkBackfillDisallowedError):
            await reconcile(stale_after=60)


@pytest.mark.unit
class TestHealth:
    @pytest.fixture(autouse=True)
    def _patch_get_session(self, db_session):
        with patch(
            "scripts.r2_sync.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            yield

    async def test_reports_unsynced_count_and_oldest_age(
        self, db_session, monkeypatch, tmp_path
    ):
        from app.models.image import Images

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        db_session.add(
            Images(
                user_id=1,
                filename="a",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                r2_location=R2Location.NONE,
            )
        )
        db_session.add(
            Images(
                user_id=1,
                filename="b",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        result = await health(output_json=True)
        assert result["unsynced_count"] == 1
        assert result["local_storage_path"] == str(tmp_path)


@pytest.mark.unit
class TestPurgeCacheCommand:
    @pytest.fixture(autouse=True)
    def _patch_get_session(self, db_session):
        with patch(
            "scripts.r2_sync.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            yield

    async def test_calls_cloudflare_with_all_variant_urls(
        self, db_session, monkeypatch
    ):
        from app.models.image import Images, VariantStatus

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")
        db_session.add(
            Images(
                image_id=42,
                user_id=1,
                filename="2026-04-17-42",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                medium=VariantStatus.READY,
                large=VariantStatus.READY,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        with patch(
            "scripts.r2_sync.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await purge_cache_command(image_id=42)
        mock_purge.assert_awaited_once()
        urls = mock_purge.await_args.args[0]
        assert len(urls) == 4


@pytest.mark.unit
class TestResyncImage:
    @pytest.fixture(autouse=True)
    def _patch_get_session(self, db_session):
        with patch(
            "scripts.r2_sync.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            yield

    async def test_prints_state_for_known_image(
        self, db_session, monkeypatch, capsys
    ):
        from app.models.image import Images

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_BUCKET", "public")
        db_session.add(
            Images(
                image_id=99,
                user_id=1,
                filename="a",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=True)
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            await resync_image(99)

        out = capsys.readouterr().out
        assert "image 99" in out
        assert "fullsize" in out and "thumbs" in out

    async def test_prints_not_found_for_missing_image(
        self, db_session, monkeypatch, capsys
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        await resync_image(99999999)
        assert "not found" in capsys.readouterr().out


@pytest.mark.unit
class TestForceReuploadImage:
    @pytest.fixture(autouse=True)
    def _patch_get_session(self, db_session):
        with patch(
            "scripts.r2_sync.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            yield

    def _seed_local_files(self, tmp_path, filename: str, ext: str) -> None:
        for variant in ("fullsize", "thumbs", "medium", "large"):
            variant_ext = "webp" if variant == "thumbs" else ext
            (tmp_path / variant).mkdir(parents=True, exist_ok=True)
            (tmp_path / variant / f"{filename}.{variant_ext}").write_bytes(b"x")

    async def test_deletes_and_reuploads_each_ready_variant(
        self, db_session, monkeypatch, tmp_path
    ):
        from app.models.image import Images, VariantStatus

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        monkeypatch.setattr(settings, "R2_PUBLIC_BUCKET", "pub")
        self._seed_local_files(tmp_path, "force-reup", "jpg")
        db_session.add(
            Images(
                image_id=50,
                user_id=1,
                filename="force-reup",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                medium=VariantStatus.READY,
                large=VariantStatus.READY,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        mock_r2 = _attach_bulk_session(AsyncMock())
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2), patch(
            "scripts.r2_sync.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await force_reupload_image(image_id=50, dry_run=False)

        assert mock_r2.delete_object.await_count == 4
        assert mock_r2.upload_file.await_count == 4
        # Public bucket -> purge CDN after reupload.
        mock_purge.assert_awaited_once()

    async def test_private_bucket_does_not_purge(
        self, db_session, monkeypatch, tmp_path
    ):
        from app.models.image import Images

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        self._seed_local_files(tmp_path, "private-reup", "jpg")
        db_session.add(
            Images(
                image_id=51,
                user_id=1,
                filename="private-reup",
                ext="jpg",
                status=ImageStatus.REVIEW,
                r2_location=R2Location.PRIVATE,
            )
        )
        await db_session.commit()

        mock_r2 = _attach_bulk_session(AsyncMock())
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2), patch(
            "scripts.r2_sync.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await force_reupload_image(image_id=51, dry_run=False)

        assert mock_r2.upload_file.await_count == 2  # fullsize + thumbs only
        mock_purge.assert_not_awaited()

    async def test_dry_run_does_not_touch_r2(
        self, db_session, monkeypatch, tmp_path
    ):
        from app.models.image import Images

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        self._seed_local_files(tmp_path, "dry-reup", "jpg")
        db_session.add(
            Images(
                image_id=52,
                user_id=1,
                filename="dry-reup",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        mock_r2 = _attach_bulk_session(AsyncMock())
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2), patch(
            "scripts.r2_sync.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await force_reupload_image(image_id=52, dry_run=True)

        mock_r2.delete_object.assert_not_awaited()
        mock_r2.upload_file.assert_not_awaited()
        mock_purge.assert_not_awaited()

    async def test_r2_location_none_refuses(
        self, db_session, monkeypatch, tmp_path, capsys
    ):
        from app.models.image import Images

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        self._seed_local_files(tmp_path, "none-reup", "jpg")
        db_session.add(
            Images(
                image_id=53,
                user_id=1,
                filename="none-reup",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                r2_location=R2Location.NONE,
            )
        )
        await db_session.commit()

        mock_r2 = _attach_bulk_session(AsyncMock())
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            await force_reupload_image(image_id=53, dry_run=False)

        mock_r2.upload_file.assert_not_awaited()
        assert "reconcile" in capsys.readouterr().out

    async def test_missing_local_file_skips_variant(
        self, db_session, monkeypatch, tmp_path
    ):
        from app.models.image import Images

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        # Seed only fullsize; leave thumbs missing on disk.
        (tmp_path / "fullsize").mkdir(parents=True, exist_ok=True)
        (tmp_path / "fullsize" / "partial-reup.jpg").write_bytes(b"x")
        db_session.add(
            Images(
                image_id=54,
                user_id=1,
                filename="partial-reup",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        mock_r2 = _attach_bulk_session(AsyncMock())
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2), patch(
            "scripts.r2_sync.purge_cache_by_urls", new_callable=AsyncMock
        ):
            await force_reupload_image(image_id=54, dry_run=False)

        assert mock_r2.upload_file.await_count == 1  # fullsize only

    async def test_prints_not_found_for_missing_image(
        self, db_session, monkeypatch, capsys
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        await force_reupload_image(image_id=99999999, dry_run=False)
        assert "not found" in capsys.readouterr().out


@pytest.mark.unit
class TestVerify:
    """verify must implement the spec's full discrepancy rules."""

    @pytest.fixture(autouse=True)
    def _patch_get_session(self, db_session):
        with patch(
            "scripts.r2_sync.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            yield

    async def test_none_with_no_object_is_clean(self, db_session, monkeypatch):
        """NONE + no object is a legitimate state (spec Operational tooling)."""
        from app.models.image import Images

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        db_session.add(
            Images(
                user_id=1,
                filename="x",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                r2_location=R2Location.NONE,
            )
        )
        await db_session.commit()

        mock_r2 = _attach_bulk_session(AsyncMock())
        mock_r2.object_exists = AsyncMock(return_value=False)
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            report = await verify(sample=None)
        assert report["discrepancies"] == []

    async def test_none_with_unexpected_object_reports_unexpected(
        self, db_session, monkeypatch
    ):
        """NONE row + object present in either bucket -> leaked upload."""
        from app.models.image import Images

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        db_session.add(
            Images(
                image_id=10,
                user_id=1,
                filename="orphan",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                r2_location=R2Location.NONE,
            )
        )
        await db_session.commit()

        mock_r2 = _attach_bulk_session(AsyncMock())
        mock_r2.object_exists = AsyncMock(
            side_effect=lambda bucket, key: bucket == settings.R2_PUBLIC_BUCKET
        )
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            report = await verify(sample=None)
        kinds = {d["kind"] for d in report["discrepancies"]}
        assert "unexpected" in kinds

    async def test_cross_bucket_orphan_reports_wrong_bucket(
        self, db_session, monkeypatch
    ):
        """PUBLIC row with copy also in private bucket -> incomplete move."""
        from app.models.image import Images

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        db_session.add(
            Images(
                image_id=11,
                user_id=1,
                filename="moved",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        mock_r2 = _attach_bulk_session(AsyncMock())
        mock_r2.object_exists = AsyncMock(return_value=True)
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            report = await verify(sample=None)
        kinds = {d["kind"] for d in report["discrepancies"]}
        assert "wrong_bucket" in kinds

    async def test_missing_from_expected_bucket_reports_missing(
        self, db_session, monkeypatch
    ):
        """PUBLIC row with object missing from public bucket -> report missing."""
        from app.models.image import Images

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        db_session.add(
            Images(
                image_id=12,
                user_id=1,
                filename="gone",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        mock_r2 = _attach_bulk_session(AsyncMock())
        mock_r2.object_exists = AsyncMock(return_value=False)
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            report = await verify(sample=None)
        kinds = {d["kind"] for d in report["discrepancies"]}
        assert "missing" in kinds
