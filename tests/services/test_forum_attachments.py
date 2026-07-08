from app.config import settings
from app.services.forum_import.attachments import forum_attachment_url, rehost_attachment


def test_url_local_appends_extension(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://dev.local")
    assert (
        forum_attachment_url("abc123", "Goodie a threat.png")
        == "http://dev.local/images/forum-archive/abc123.png"
    )


def test_url_cdn_appends_lowercased_extension(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example")
    assert (
        forum_attachment_url("abc123", "PHOTO.JPG")
        == "https://cdn.example/forum-archive/abc123.jpg"
    )


def test_url_no_extension_leaves_key_bare(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://dev.local")
    assert (
        forum_attachment_url("abc123", "README")
        == "http://dev.local/images/forum-archive/abc123"
    )


async def test_rehost_local_copies_file_with_extension(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")
    await rehost_attachment("phys_key", "picture.jpeg", src, "image/jpeg")
    dest = tmp_path / "forum-archive" / "phys_key.jpeg"
    assert dest.read_bytes() == b"hello"


async def test_rehost_r2_sets_extension_key_and_disposition(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_BUCKET", "bkt")
    captured: dict = {}

    class FakeStorage:
        async def upload_bytes(
            self, *, bucket, key, body, content_type, content_disposition=None
        ):
            captured.update(
                bucket=bucket,
                key=key,
                body=body,
                content_type=content_type,
                content_disposition=content_disposition,
            )

    monkeypatch.setattr(
        "app.services.forum_import.attachments.get_r2_storage", lambda: FakeStorage()
    )
    src = tmp_path / "s"
    src.write_bytes(b"x")
    await rehost_attachment("711_hash", "Goodie a threat.png", src, "image/png")

    assert captured["bucket"] == "bkt"
    assert captured["key"] == "forum-archive/711_hash.png"
    assert captured["body"] == b"x"
    assert captured["content_type"] == "image/png"
    # Original filename preserved for the download name; inline so images render.
    assert captured["content_disposition"].startswith("inline;")
    assert 'filename="Goodie a threat.png"' in captured["content_disposition"]
