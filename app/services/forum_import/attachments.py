"""Rehost phpBB attachment files through the app's storage layer and build URLs.

Mirrors the avatar pattern (app/services/avatar.py): R2 CDN URL when
R2_ENABLED, local media URL otherwise — under a dedicated `forum-archive/` key
prefix distinct from board images and `avatars/`.

The stored object keeps the phpBB *physical* filename (an extensionless
`{id}_{hash}` name) but gains the original file's extension, so a static host
(dev nginx) can infer a Content-Type and a downloaded file carries a usable
extension. On R2 we additionally set Content-Disposition so downloads use the
original human-readable filename.
"""

import shutil
from pathlib import Path
from urllib.parse import quote

from app.config import settings
from app.core.r2_client import get_r2_storage

_PREFIX = "forum-archive"


def _storage_key(physical_filename: str, real_filename: str) -> str:
    """phpBB physical filename plus the original file's (lowercased) extension.

    Returns the physical filename unchanged when the original has no extension.
    """
    ext = Path(real_filename).suffix.lower()
    return f"{physical_filename}{ext}"


def _content_disposition(real_filename: str) -> str:
    """RFC 6266 header so downloads use the original filename; `inline` so images
    still render in-browser. Emits an ASCII fallback plus a UTF-8 `filename*`."""
    ascii_name = real_filename.encode("ascii", "replace").decode("ascii").replace('"', "'")
    encoded = quote(real_filename, safe="")
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def forum_attachment_url(physical_filename: str, real_filename: str) -> str:
    key = _storage_key(physical_filename, real_filename)
    if settings.R2_ENABLED:
        return f"{settings.R2_PUBLIC_CDN_URL}/{_PREFIX}/{key}"
    return f"{settings.IMAGE_BASE_URL}/images/{_PREFIX}/{key}"


async def rehost_attachment(
    physical_filename: str, real_filename: str, source_path: Path, content_type: str
) -> None:
    """Store one attachment file. Idempotent-friendly (overwrite is harmless)."""
    key = _storage_key(physical_filename, real_filename)
    if settings.R2_ENABLED:
        body = source_path.read_bytes()
        await get_r2_storage().upload_bytes(
            bucket=settings.R2_PUBLIC_BUCKET,
            key=f"{_PREFIX}/{key}",
            body=body,
            content_type=content_type,
            content_disposition=_content_disposition(real_filename),
        )
    else:
        dest = Path(settings.STORAGE_PATH) / _PREFIX / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest)
