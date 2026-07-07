"""Rehost phpBB attachment files through the app's storage layer and build URLs.

Mirrors the avatar pattern (app/services/avatar.py): R2 CDN URL when
R2_ENABLED, local media URL otherwise — under a dedicated `forum-archive/` key
prefix distinct from board images and `avatars/`.
"""

import shutil
from pathlib import Path

from app.config import settings
from app.core.r2_client import get_r2_storage

_PREFIX = "forum-archive"


def forum_attachment_url(physical_filename: str) -> str:
    if settings.R2_ENABLED:
        return f"{settings.R2_PUBLIC_CDN_URL}/{_PREFIX}/{physical_filename}"
    return f"{settings.IMAGE_BASE_URL}/images/{_PREFIX}/{physical_filename}"


async def rehost_attachment(physical_filename: str, source_path: Path, content_type: str) -> None:
    """Store one attachment file. Idempotent-friendly (overwrite is harmless)."""
    if settings.R2_ENABLED:
        body = source_path.read_bytes()
        await get_r2_storage().upload_bytes(
            bucket=settings.R2_PUBLIC_BUCKET,
            key=f"{_PREFIX}/{physical_filename}",
            body=body,
            content_type=content_type,
        )
    else:
        dest = Path(settings.STORAGE_PATH) / _PREFIX / physical_filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest)
