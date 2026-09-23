"""Tests for scripts/regen_icc_png_media.py.

Background: until PR #404 the sRGB helper flattened every RGBA PNG that
carried an ICC profile, so their thumbs and medium/large variants on disk
(and in R2) lost transparency. The script finds those sources and rebuilds
their derived files with the fixed pipeline.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image, ImageCms

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from app.models.image import Images, VariantStatus
from scripts.regen_icc_png_media import (
    fetch_png_rows,
    fix_image,
    is_affected_png,
    main,
    pending_ids,
    scan,
    sync_image,
)

_SRGB_BYTES = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _write_png(path: Path, mode: str, size: tuple[int, int] = (64, 64), icc: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fill = (0, 0, 0, 0) if mode == "RGBA" else (200, 50, 50)
    img = Image.new(mode, size, color=fill)
    if mode == "RGBA":
        img.paste((200, 50, 50, 255), (size[0] // 4, size[1] // 4, size[0] // 2, size[1] // 2))
    if icc:
        img.save(path, icc_profile=_SRGB_BYTES)
    else:
        img.save(path)


def _mock_session_cm(db_session):
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


@pytest.mark.unit
class TestIsAffectedPng:
    def test_rgba_with_icc_is_affected(self, tmp_path):
        _write_png(tmp_path / "a.png", "RGBA", icc=True)
        assert is_affected_png(tmp_path / "a.png") is True

    def test_rgba_without_icc_is_not_affected(self, tmp_path):
        _write_png(tmp_path / "a.png", "RGBA", icc=False)
        assert is_affected_png(tmp_path / "a.png") is False

    def test_rgb_with_icc_is_not_affected(self, tmp_path):
        _write_png(tmp_path / "a.png", "RGB", icc=True)
        assert is_affected_png(tmp_path / "a.png") is False

    def test_header_check_ignores_decompression_bomb_limit(self, tmp_path, monkeypatch):
        """Only the header is read, so the pixel-count guard is irrelevant here.

        Prod has PNGs past Pillow's default 178M-pixel ceiling that the guard
        would otherwise turn into a crash mid-scan.
        """
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
        _write_png(tmp_path / "a.png", "RGBA", icc=True)

        assert is_affected_png(tmp_path / "a.png") is True
        assert Image.MAX_IMAGE_PIXELS == 10  # restored for everything else


@pytest.mark.unit
class TestScan:
    def test_reports_affected_and_missing_sources(self, tmp_path):
        fullsize = tmp_path / "fullsize"
        _write_png(fullsize / "2026-01-01-1.png", "RGBA", icc=True)
        _write_png(fullsize / "2026-01-01-2.png", "RGB", icc=True)
        rows = [
            (1, "2026-01-01-1", "png"),
            (2, "2026-01-01-2", "png"),
            (3, "2026-01-01-3", "png"),  # no file on disk
        ]

        result = scan(rows, str(tmp_path))

        assert result.candidates == [1]
        assert result.missing == [3]
        assert result.scanned == 3

    def test_unreadable_file_is_reported_and_scan_continues(self, tmp_path):
        fullsize = tmp_path / "fullsize"
        _write_png(fullsize / "2026-01-01-1.png", "RGBA", icc=True)
        (fullsize / "2026-01-01-2.png").write_bytes(b"definitely not a png")
        _write_png(fullsize / "2026-01-01-3.png", "RGBA", icc=True)
        rows = [(1, "2026-01-01-1", "png"), (2, "2026-01-01-2", "png"), (3, "2026-01-01-3", "png")]

        result = scan(rows, str(tmp_path))

        assert result.candidates == [1, 3]
        assert [image_id for image_id, _ in result.unreadable] == [2]
        assert "UnidentifiedImageError" in result.unreadable[0][1]


@pytest.mark.unit
class TestFetchPngRows:
    async def test_returns_only_png_rows_in_id_order(self, db_session):
        db_session.add_all(
            [
                Images(image_id=12, user_id=1, filename="2026-01-01-12", ext="png", status=1),
                Images(image_id=10, user_id=1, filename="2026-01-01-10", ext="png", status=1),
                Images(image_id=11, user_id=1, filename="2026-01-01-11", ext="jpg", status=1),
            ]
        )
        await db_session.commit()

        with patch(
            "scripts.regen_icc_png_media.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            rows = await fetch_png_rows(min_id=None)

        assert rows == [(10, "2026-01-01-10", "png"), (12, "2026-01-01-12", "png")]

    async def test_min_id_bounds_the_query(self, db_session):
        db_session.add_all(
            [
                Images(image_id=10, user_id=1, filename="2026-01-01-10", ext="png", status=1),
                Images(image_id=12, user_id=1, filename="2026-01-01-12", ext="png", status=1),
            ]
        )
        await db_session.commit()

        with patch(
            "scripts.regen_icc_png_media.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            rows = await fetch_png_rows(min_id=11)

        assert rows == [(12, "2026-01-01-12", "png")]


@pytest.mark.unit
class TestPendingIds:
    def test_skips_ids_already_in_done_file(self, tmp_path):
        candidates = tmp_path / "cands"
        candidates.write_text("1\n2\n3\n")
        done = tmp_path / "cands.done"
        done.write_text("2\n")

        assert pending_ids(candidates, done) == [1, 3]

    def test_missing_done_file_means_nothing_done(self, tmp_path):
        candidates = tmp_path / "cands"
        candidates.write_text("1\n\n3\n")

        assert pending_ids(candidates, tmp_path / "cands.done") == [1, 3]


@pytest.mark.unit
class TestFixImage:
    @pytest.fixture(autouse=True)
    def _patch_session(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        with patch(
            "scripts.regen_icc_png_media.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            yield

    async def _seed(self, db_session, tmp_path, *, size=(1600, 1600), **row_kwargs) -> Images:
        _write_png(tmp_path / "fullsize" / "2026-09-19-7.png", "RGBA", size=size, icc=True)
        image = Images(
            image_id=7,
            user_id=1,
            filename="2026-09-19-7",
            ext="png",
            status=ImageStatus.ACTIVE,
            width=size[0],
            height=size[1],
            **row_kwargs,
        )
        db_session.add(image)
        await db_session.commit()
        return image

    async def test_regenerates_thumb_and_variants_with_alpha(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path, medium=VariantStatus.NONE, large=VariantStatus.NONE)

        outcome = await fix_image(7, dry_run=False)

        assert outcome.ok, outcome.message
        with Image.open(tmp_path / "thumbs" / "2026-09-19-7.webp") as thumb:
            assert thumb.mode == "RGBA"
            assert thumb.getpixel((0, 0))[3] == 0
        with Image.open(tmp_path / "medium" / "2026-09-19-7.png") as medium:
            assert medium.mode == "RGBA"
        assert not (tmp_path / "large" / "2026-09-19-7.png").exists()

        await db_session.refresh(await db_session.get(Images, 7))
        row = await db_session.get(Images, 7)
        assert row.medium == VariantStatus.READY
        assert row.large == VariantStatus.NONE

    async def test_dry_run_touches_nothing(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path, medium=VariantStatus.NONE)

        outcome = await fix_image(7, dry_run=True)

        assert outcome.ok
        assert not (tmp_path / "thumbs").exists()
        assert not (tmp_path / "medium").exists()
        row = await db_session.get(Images, 7)
        assert row.medium == VariantStatus.NONE

    async def test_reuploads_derived_files_when_synced_to_r2(
        self, db_session, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        await self._seed(db_session, tmp_path, r2_location=R2Location.PUBLIC)

        with patch(
            "scripts.regen_icc_png_media.force_reupload_image", new_callable=AsyncMock
        ) as reupload:
            outcome = await fix_image(7, dry_run=False)

        assert outcome.ok
        reupload.assert_awaited_once_with(
            image_id=7, dry_run=False, only={"thumbs", "medium", "large"}
        )

    async def test_skips_r2_when_row_never_synced(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        await self._seed(db_session, tmp_path, r2_location=R2Location.NONE)

        with patch(
            "scripts.regen_icc_png_media.force_reupload_image", new_callable=AsyncMock
        ) as reupload:
            await fix_image(7, dry_run=False)

        reupload.assert_not_awaited()

    async def test_missing_source_is_reported_not_raised(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path)
        (tmp_path / "fullsize" / "2026-09-19-7.png").unlink()

        outcome = await fix_image(7, dry_run=False)

        assert not outcome.ok
        assert "source not found" in outcome.message

    async def test_unknown_image_is_reported_not_raised(self):
        outcome = await fix_image(424242, dry_run=False)

        assert not outcome.ok
        assert "not found" in outcome.message


@pytest.mark.unit
class TestSyncImage:
    """Push dev-regenerated derived files to prod R2 and align prod's variant statuses.

    The fix ran on the dev box (R2 disabled), so prod's medium/large columns
    are still pre-fix. What is on local disk after the fix is the truth.
    """

    @pytest.fixture(autouse=True)
    def _patch_session(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        with (
            patch(
                "scripts.regen_icc_png_media.get_async_session",
                return_value=_mock_session_cm(db_session),
            ),
            # Default: R2 holds no stale objects. Tests that need some re-patch inside.
            patch(
                "scripts.regen_icc_png_media.get_r2_storage",
                return_value=self._r2_with_objects(set()),
            ),
        ):
            yield

    async def _seed(self, db_session, tmp_path, *, local: set[str], **row_kwargs) -> None:
        for variant in local:
            ext = "webp" if variant == "thumbs" else "png"
            (tmp_path / variant).mkdir(parents=True, exist_ok=True)
            (tmp_path / variant / f"2026-09-19-8.{ext}").write_bytes(b"x")
        row_kwargs.setdefault("r2_location", R2Location.PUBLIC)
        db_session.add(
            Images(
                image_id=8,
                user_id=1,
                filename="2026-09-19-8",
                ext="png",
                status=ImageStatus.ACTIVE,
                **row_kwargs,
            )
        )
        await db_session.commit()

    async def test_statuses_follow_local_files_then_reuploads_derived(self, db_session, tmp_path):
        # prod thinks: medium READY, large NONE. dev disk after fix: no medium, has large.
        await self._seed(
            db_session,
            tmp_path,
            local={"thumbs", "large"},
            medium=VariantStatus.READY,
            large=VariantStatus.NONE,
        )

        with patch(
            "scripts.regen_icc_png_media.force_reupload_image", new_callable=AsyncMock
        ) as reupload:
            outcome = await sync_image(8, dry_run=False)

        assert outcome.ok, outcome.message
        row = await db_session.get(Images, 8)
        await db_session.refresh(row)
        assert row.medium == VariantStatus.NONE
        assert row.large == VariantStatus.READY
        assert "medium 1->0" in outcome.message and "large 0->1" in outcome.message
        reupload.assert_awaited_once_with(
            image_id=8, dry_run=False, only={"thumbs", "medium", "large"}
        )

    async def test_dry_run_reports_flips_without_writing(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path, local={"thumbs"}, medium=VariantStatus.READY)

        with patch(
            "scripts.regen_icc_png_media.force_reupload_image", new_callable=AsyncMock
        ) as reupload:
            outcome = await sync_image(8, dry_run=True)

        assert outcome.ok
        assert "medium 1->0" in outcome.message
        row = await db_session.get(Images, 8)
        await db_session.refresh(row)
        assert row.medium == VariantStatus.READY
        reupload.assert_awaited_once_with(
            image_id=8, dry_run=True, only={"thumbs", "medium", "large"}
        )

    def _r2_with_objects(self, existing: set[str]) -> AsyncMock:
        r2 = AsyncMock()
        r2.object_exists = AsyncMock(side_effect=lambda *, bucket, key: key in existing)
        return r2

    async def test_demoted_variant_object_is_deleted_and_purged(
        self, db_session, tmp_path, monkeypatch
    ):
        """A READY->NONE flip must also remove the stale (flat) object from R2.

        force_reupload_image only touches READY variants, so without this the
        pre-fix medium stays fetchable at its predictable CDN URL forever.
        """
        monkeypatch.setattr(settings, "R2_PUBLIC_BUCKET", "pub")
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example")
        await self._seed(db_session, tmp_path, local={"thumbs"}, medium=VariantStatus.READY)
        r2 = self._r2_with_objects({"medium/2026-09-19-8.png"})

        with (
            patch("scripts.regen_icc_png_media.force_reupload_image", new_callable=AsyncMock),
            patch("scripts.regen_icc_png_media.get_r2_storage", return_value=r2),
            patch(
                "scripts.regen_icc_png_media.purge_cache_by_urls", new_callable=AsyncMock
            ) as purge,
        ):
            outcome = await sync_image(8, dry_run=False)

        assert outcome.ok, outcome.message
        r2.delete_object.assert_awaited_once_with(bucket="pub", key="medium/2026-09-19-8.png")
        purge.assert_awaited_once_with(["https://cdn.example/medium/2026-09-19-8.png"])
        assert "deleted stale medium" in outcome.message

    async def test_already_demoted_variant_is_still_cleaned_up(
        self, db_session, tmp_path, monkeypatch
    ):
        """Re-running sync over ids synced before this cleanup existed prunes their orphans."""
        monkeypatch.setattr(settings, "R2_PUBLIC_BUCKET", "pub")
        await self._seed(
            db_session,
            tmp_path,
            local={"thumbs"},
            medium=VariantStatus.NONE,
            large=VariantStatus.NONE,
        )
        r2 = self._r2_with_objects({"large/2026-09-19-8.png"})

        with (
            patch("scripts.regen_icc_png_media.force_reupload_image", new_callable=AsyncMock),
            patch("scripts.regen_icc_png_media.get_r2_storage", return_value=r2),
            patch("scripts.regen_icc_png_media.purge_cache_by_urls", new_callable=AsyncMock),
        ):
            outcome = await sync_image(8, dry_run=False)

        assert outcome.ok
        r2.delete_object.assert_awaited_once_with(bucket="pub", key="large/2026-09-19-8.png")
        assert "no status change" in outcome.message and "deleted stale large" in outcome.message

    async def test_nothing_deleted_when_no_stale_object_exists(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path, local={"thumbs"}, medium=VariantStatus.NONE)
        r2 = self._r2_with_objects(set())

        with (
            patch("scripts.regen_icc_png_media.force_reupload_image", new_callable=AsyncMock),
            patch("scripts.regen_icc_png_media.get_r2_storage", return_value=r2),
            patch(
                "scripts.regen_icc_png_media.purge_cache_by_urls", new_callable=AsyncMock
            ) as purge,
        ):
            outcome = await sync_image(8, dry_run=False)

        r2.delete_object.assert_not_awaited()
        purge.assert_not_awaited()
        assert "deleted" not in outcome.message

    async def test_dry_run_reports_stale_object_without_deleting(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path, local={"thumbs"}, medium=VariantStatus.READY)
        r2 = self._r2_with_objects({"medium/2026-09-19-8.png"})

        with (
            patch("scripts.regen_icc_png_media.force_reupload_image", new_callable=AsyncMock),
            patch("scripts.regen_icc_png_media.get_r2_storage", return_value=r2),
            patch(
                "scripts.regen_icc_png_media.purge_cache_by_urls", new_callable=AsyncMock
            ) as purge,
        ):
            outcome = await sync_image(8, dry_run=True)

        r2.delete_object.assert_not_awaited()
        purge.assert_not_awaited()
        assert "would delete stale medium" in outcome.message

    async def test_private_bucket_deletes_without_purge(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "R2_PRIVATE_BUCKET", "priv")
        await self._seed(
            db_session,
            tmp_path,
            local={"thumbs"},
            medium=VariantStatus.READY,
            r2_location=R2Location.PRIVATE,
        )
        r2 = self._r2_with_objects({"medium/2026-09-19-8.png"})

        with (
            patch("scripts.regen_icc_png_media.force_reupload_image", new_callable=AsyncMock),
            patch("scripts.regen_icc_png_media.get_r2_storage", return_value=r2),
            patch(
                "scripts.regen_icc_png_media.purge_cache_by_urls", new_callable=AsyncMock
            ) as purge,
        ):
            await sync_image(8, dry_run=False)

        r2.delete_object.assert_awaited_once_with(bucket="priv", key="medium/2026-09-19-8.png")
        purge.assert_not_awaited()

    async def test_row_never_synced_to_r2_is_an_error(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path, local={"thumbs"}, r2_location=R2Location.NONE)

        with patch(
            "scripts.regen_icc_png_media.force_reupload_image", new_callable=AsyncMock
        ) as reupload:
            outcome = await sync_image(8, dry_run=False)

        assert not outcome.ok
        assert "r2_location" in outcome.message
        reupload.assert_not_awaited()

    async def test_missing_local_thumb_is_an_error(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path, local={"medium"})

        with patch(
            "scripts.regen_icc_png_media.force_reupload_image", new_callable=AsyncMock
        ) as reupload:
            outcome = await sync_image(8, dry_run=False)

        assert not outcome.ok
        assert "thumb" in outcome.message
        reupload.assert_not_awaited()

    async def test_unknown_image_is_reported_not_raised(self):
        outcome = await sync_image(424242, dry_run=False)

        assert not outcome.ok
        assert "not found" in outcome.message


@pytest.mark.unit
class TestSyncCommandGuard:
    def test_refuses_when_r2_disabled(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        done = tmp_path / "icc-png-candidates.done"
        done.write_text("1\n")

        assert main(["sync", "--done", str(done)]) == 1
        assert "R2_ENABLED" in capsys.readouterr().err
