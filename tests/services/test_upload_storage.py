"""Tests for the upload's two filesystem steps against a real filesystem.

The route tests mock both of these, so the naming contract they establish —
staged file first, permanent YYYY-MM-DD-{image_id}.{ext} name only once the row
exists — is only checked here.
"""

import logging
import os
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.services.upload import finalize_uploaded_image, stage_uploaded_image


def _jpeg_bytes(color: str = "red") -> bytes:
    from PIL import Image

    img = Image.new("RGB", (64, 48), color=color)
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _upload(name: str = "photo.JPG", color: str = "red") -> UploadFile:
    """A real multipart-style upload: staging validates content_type too."""
    return UploadFile(
        filename=name,
        file=BytesIO(_jpeg_bytes(color)),
        headers=Headers({"content-type": "image/jpeg"}),
    )


@pytest.mark.unit
class TestStageUploadedImage:
    async def test_stages_under_a_temporary_name_and_hashes(self, tmp_path: Path):
        """The staged file exists, carries no permanent name, and is hashed."""
        staged_path, ext, md5_hash = await stage_uploaded_image(_upload(), str(tmp_path))

        assert staged_path.exists()
        assert staged_path.parent == tmp_path / "fullsize"
        assert staged_path.name.startswith("staged_")
        assert ext == "jpg"  # lowercased from .JPG
        assert len(md5_hash) == 32

    async def test_concurrent_uploads_of_one_filename_get_distinct_paths(self, tmp_path: Path):
        """Two uploads sharing an original filename must not share a staging path.

        The staged file now outlives the duplicate and IQDB checks, so a shared
        name would let one request overwrite or unlink another's bytes.
        """
        first, _, first_md5 = await stage_uploaded_image(_upload(color="red"), str(tmp_path))
        second, _, second_md5 = await stage_uploaded_image(_upload(color="blue"), str(tmp_path))

        assert first != second
        assert first.exists() and second.exists()
        assert first_md5 != second_md5  # neither clobbered the other

    async def test_staged_bytes_are_fsynced(self, tmp_path: Path, monkeypatch):
        """The full upload is forced to disk before staging returns.

        The caller commits the image row next; without the fsync a host crash
        inside the writeback window leaves a row pointing at a zero-byte file.
        The spy records the file's size at fsync time, so an fsync issued
        before the buffered write is flushed fails this too.
        """
        synced_sizes: list[int] = []
        real_fsync = os.fsync

        def _spy(fd: int) -> None:
            synced_sizes.append(os.fstat(fd).st_size)
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", _spy)

        staged_path, _, _ = await stage_uploaded_image(_upload(), str(tmp_path))

        assert synced_sizes == [staged_path.stat().st_size]

    async def test_rejects_a_non_image_and_leaves_nothing_behind(self, tmp_path: Path):
        """Bytes that aren't an image are refused and the staged file removed.

        Content type and extension both claim JPEG, so this fails on PIL's
        decode — the check that actually matters, since the other two are
        user-controlled.
        """
        bogus = UploadFile(
            filename="notreally.jpg",
            file=BytesIO(b"this is not a jpeg"),
            headers=Headers({"content-type": "image/jpeg"}),
        )

        with pytest.raises(HTTPException) as exc_info:
            await stage_uploaded_image(bogus, str(tmp_path))

        assert exc_info.value.status_code == 400
        assert list((tmp_path / "fullsize").glob("staged_*")) == []

    @pytest.mark.parametrize(
        "name",
        [
            "137109272_p0_master1200.jpg",
            "137109272_p3_square1200.jpg",
            "137109272_p0_custom1200.jpg",
            # Shapes seen on real uploads: a browser's duplicate-download
            # suffix, a Finder copy, and an uploader's own prefix.
            "46430881_p0_master1200 (1).jpg",
            "42676174_p0_master1200 copy.jpg",
            "ch46657230_p1_master1200.jpg",
        ],
    )
    async def test_rejects_pixiv_downsized_rendition(
        self, tmp_path: Path, name: str, caplog: pytest.LogCaptureFixture
    ):
        """Pixiv's 1200px re-compressed previews are refused before anything is written."""
        with caplog.at_level(logging.INFO), pytest.raises(HTTPException) as exc_info:
            await stage_uploaded_image(_upload(name=name), str(tmp_path))

        assert exc_info.value.status_code == 422
        assert "original" in exc_info.value.detail
        assert not (tmp_path / "fullsize").exists()
        # Logged with the filename, so false positives can be found later.
        log_messages = " ".join(record.getMessage() for record in caplog.records)
        assert "pixiv_downsized_upload_refused" in log_messages
        assert name in log_messages

    @pytest.mark.parametrize("name", ["137109272_p0.jpg", "137109272_p12.png", "master1200.jpg"])
    async def test_accepts_pixiv_original_and_lookalike_names(self, tmp_path: Path, name: str):
        """A pixiv original ({id}_p{n}.{ext}) and names merely mentioning master1200 pass."""
        staged_path, _, _ = await stage_uploaded_image(_upload(name=name), str(tmp_path))

        assert staged_path.exists()


@pytest.mark.unit
class TestFinalizeUploadedImage:
    async def test_renames_to_the_permanent_name(self, tmp_path: Path):
        """The staged file takes its YYYY-MM-DD-{image_id}.{ext} name in place."""
        staged_path, ext, _ = await stage_uploaded_image(_upload(), str(tmp_path))

        final_path = finalize_uploaded_image(staged_path, str(tmp_path), 1116164, ext, "2026-05-18")

        assert final_path.name == "2026-05-18-1116164.jpg"
        assert final_path.parent == tmp_path / "fullsize"
        assert final_path.exists()
        assert not staged_path.exists()

    async def test_uses_the_caller_s_date_prefix(self, tmp_path: Path):
        """The prefix comes from the caller so the name matches the DB filename.

        The route records `{date_prefix}-{image_id}` on the row and passes the
        same prefix here; recomputing the date inside would let an upload that
        straddles local midnight write a name the row disagrees with.
        """
        staged_path, ext, _ = await stage_uploaded_image(_upload(), str(tmp_path))

        final_path = finalize_uploaded_image(staged_path, str(tmp_path), 42, ext, "1999-12-31")

        assert final_path.name == "1999-12-31-42.jpg"
        assert final_path.stem == "1999-12-31-42"  # what the route stores as filename
