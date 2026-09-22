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
    pending_ids,
    scan,
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
