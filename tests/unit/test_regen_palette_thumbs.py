"""Tests for scripts/regen_palette_thumbs.py.

Background: until PR #409 create_thumbnail converted palette sources (GIF,
pngquant PNG) straight to RGB, which paints every transparent pixel with the
transparent index's colour. Thumbs on disk (and in R2) for those sources
have that colour baked in. Variants were never affected, so only thumbs
are rebuilt.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from app.models.image import Images, VariantStatus
from scripts.regen_palette_thumbs import (
    fetch_rows,
    fix_image,
    is_palette_transparent,
    main,
    sync_image,
)

_PALETTE = [255, 0, 255, 200, 50, 50] + [0] * (256 * 3 - 6)


def _write_palette_image(
    path: Path, *, transparent: bool = True, size: tuple[int, int] = (64, 64)
) -> None:
    """Palette image with a magenta index 0 and a red block of index 1."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("P", size)
    img.putpalette(_PALETTE)
    img.paste(1, (size[0] // 4, size[1] // 4, size[0] // 2, size[1] // 2))
    if transparent:
        img.save(path, transparency=0)
    else:
        img.save(path)


def _mock_session_cm(db_session):
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


@pytest.mark.unit
class TestIsPaletteTransparent:
    def test_gif_with_transparent_index_is_affected(self, tmp_path):
        _write_palette_image(tmp_path / "a.gif")
        assert is_palette_transparent(tmp_path / "a.gif") is True

    def test_png_with_transparent_index_is_affected(self, tmp_path):
        _write_palette_image(tmp_path / "a.png")
        assert is_palette_transparent(tmp_path / "a.png") is True

    def test_palette_without_transparency_is_not_affected(self, tmp_path):
        _write_palette_image(tmp_path / "a.gif", transparent=False)
        assert is_palette_transparent(tmp_path / "a.gif") is False

    def test_rgba_png_is_not_affected(self, tmp_path):
        Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(tmp_path / "a.png")
        assert is_palette_transparent(tmp_path / "a.png") is False

    def test_header_check_ignores_decompression_bomb_limit(self, tmp_path, monkeypatch):
        _write_palette_image(tmp_path / "a.gif")
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)

        assert is_palette_transparent(tmp_path / "a.gif") is True
        assert Image.MAX_IMAGE_PIXELS == 10


@pytest.mark.unit
class TestFetchRows:
    async def test_returns_gif_and_png_rows_in_id_order(self, db_session):
        for image_id, ext in ((3, "png"), (1, "gif"), (2, "jpg"), (4, "GIF")):
            db_session.add(
                Images(
                    image_id=image_id,
                    user_id=1,
                    filename=f"2026-10-05-{image_id}",
                    ext=ext,
                    status=ImageStatus.ACTIVE,
                )
            )
        await db_session.commit()

        with patch(
            "scripts.regen_palette_thumbs.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            rows = await fetch_rows(min_id=None)

        assert rows == [
            (1, "2026-10-05-1", "gif"),
            (3, "2026-10-05-3", "png"),
            (4, "2026-10-05-4", "GIF"),
        ]

    async def test_min_id_bounds_the_query(self, db_session):
        for image_id in (1, 2, 3):
            db_session.add(
                Images(
                    image_id=image_id,
                    user_id=1,
                    filename=f"2026-10-05-{image_id}",
                    ext="gif",
                    status=ImageStatus.ACTIVE,
                )
            )
        await db_session.commit()

        with patch(
            "scripts.regen_palette_thumbs.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            rows = await fetch_rows(min_id=2)

        assert [image_id for image_id, _, _ in rows] == [2, 3]


@pytest.mark.unit
class TestFixImage:
    @pytest.fixture(autouse=True)
    def _patch_session(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        with patch(
            "scripts.regen_palette_thumbs.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            yield

    async def _seed(self, db_session, tmp_path, **row_kwargs) -> Images:
        size = (1600, 1600)
        _write_palette_image(tmp_path / "fullsize" / "2026-10-05-7.gif", size=size)
        image = Images(
            image_id=7,
            user_id=1,
            filename="2026-10-05-7",
            ext="gif",
            status=ImageStatus.ACTIVE,
            width=size[0],
            height=size[1],
            **row_kwargs,
        )
        db_session.add(image)
        await db_session.commit()
        return image

    async def test_regenerates_thumb_with_alpha_and_leaves_variants_alone(
        self, db_session, tmp_path
    ):
        await self._seed(db_session, tmp_path, medium=VariantStatus.READY, large=VariantStatus.NONE)

        outcome = await fix_image(7, dry_run=False)

        assert outcome.ok, outcome.message
        with Image.open(tmp_path / "thumbs" / "2026-10-05-7.webp") as thumb:
            assert thumb.mode == "RGBA"
            assert thumb.getpixel((0, 0))[3] == 0
        assert not (tmp_path / "medium").exists()
        assert not (tmp_path / "large").exists()
        row = await db_session.get(Images, 7)
        await db_session.refresh(row)
        assert row.medium == VariantStatus.READY
        assert row.large == VariantStatus.NONE

    async def test_dry_run_touches_nothing(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path)

        outcome = await fix_image(7, dry_run=True)

        assert outcome.ok
        assert not (tmp_path / "thumbs").exists()

    async def test_reuploads_thumb_only_when_synced_to_r2(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        await self._seed(db_session, tmp_path, r2_location=R2Location.PUBLIC)

        with patch(
            "scripts.regen_palette_thumbs.force_reupload_image", new_callable=AsyncMock
        ) as reupload:
            outcome = await fix_image(7, dry_run=False)

        assert outcome.ok
        reupload.assert_awaited_once_with(image_id=7, dry_run=False, only={"thumbs"})

    async def test_skips_r2_when_row_never_synced(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        await self._seed(db_session, tmp_path, r2_location=R2Location.NONE)

        with patch(
            "scripts.regen_palette_thumbs.force_reupload_image", new_callable=AsyncMock
        ) as reupload:
            await fix_image(7, dry_run=False)

        reupload.assert_not_awaited()

    async def test_missing_source_is_reported_not_raised(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path)
        (tmp_path / "fullsize" / "2026-10-05-7.gif").unlink()

        outcome = await fix_image(7, dry_run=False)

        assert not outcome.ok
        assert "source not found" in outcome.message

    async def test_unknown_image_is_reported_not_raised(self):
        outcome = await fix_image(424242, dry_run=False)

        assert not outcome.ok
        assert "not found" in outcome.message


@pytest.mark.unit
class TestSyncImage:
    """Push dev-regenerated thumbs to prod R2. No variant statuses to align."""

    @pytest.fixture(autouse=True)
    def _patch_session(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        with patch(
            "scripts.regen_palette_thumbs.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            yield

    async def _seed(self, db_session, tmp_path, *, local_thumb: bool = True, **row_kwargs) -> None:
        if local_thumb:
            (tmp_path / "thumbs").mkdir(parents=True, exist_ok=True)
            (tmp_path / "thumbs" / "2026-10-05-8.webp").write_bytes(b"x")
        row_kwargs.setdefault("r2_location", R2Location.PUBLIC)
        db_session.add(
            Images(
                image_id=8,
                user_id=1,
                filename="2026-10-05-8",
                ext="gif",
                status=ImageStatus.ACTIVE,
                **row_kwargs,
            )
        )
        await db_session.commit()

    async def test_reuploads_thumb_only(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path)

        with patch(
            "scripts.regen_palette_thumbs.force_reupload_image", new_callable=AsyncMock
        ) as reupload:
            outcome = await sync_image(8, dry_run=False)

        assert outcome.ok, outcome.message
        reupload.assert_awaited_once_with(image_id=8, dry_run=False, only={"thumbs"})

    async def test_dry_run_is_passed_through(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path)

        with patch(
            "scripts.regen_palette_thumbs.force_reupload_image", new_callable=AsyncMock
        ) as reupload:
            outcome = await sync_image(8, dry_run=True)

        assert outcome.ok
        assert "would sync" in outcome.message
        reupload.assert_awaited_once_with(image_id=8, dry_run=True, only={"thumbs"})

    async def test_row_never_synced_to_r2_is_an_error(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path, r2_location=R2Location.NONE)

        outcome = await sync_image(8, dry_run=False)

        assert not outcome.ok
        assert "r2_location=NONE" in outcome.message

    async def test_missing_local_thumb_is_an_error(self, db_session, tmp_path):
        await self._seed(db_session, tmp_path, local_thumb=False)

        outcome = await sync_image(8, dry_run=False)

        assert not outcome.ok
        assert "local thumb missing" in outcome.message

    async def test_unknown_image_is_reported_not_raised(self):
        outcome = await sync_image(424242, dry_run=False)

        assert not outcome.ok
        assert "not found" in outcome.message


@pytest.mark.unit
class TestSyncCommandGuard:
    def test_refuses_when_r2_disabled(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        done = tmp_path / "palette-thumb-candidates.done"
        done.write_text("1\n")

        assert main(["sync", "--done", str(done)]) == 1
        assert "R2_ENABLED" in capsys.readouterr().err
