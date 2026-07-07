from app.config import settings
from app.services.forum_import.attachments import forum_attachment_url, rehost_attachment


def test_url_local_when_r2_disabled(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://dev.local")
    assert forum_attachment_url("abc123") == "http://dev.local/images/forum-archive/abc123"


def test_url_cdn_when_r2_enabled(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example")
    assert forum_attachment_url("abc123") == "https://cdn.example/forum-archive/abc123"


async def test_rehost_local_copies_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")
    await rehost_attachment("phys_key", src, "image/jpeg")
    dest = tmp_path / "forum-archive" / "phys_key"
    assert dest.read_bytes() == b"hello"
