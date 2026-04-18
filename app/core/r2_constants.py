"""Shared constants for R2 integration.

Kept in its own module to avoid import cycles between models, schemas,
services, and the scripts/r2_sync.py CLI.
"""

from enum import IntEnum

from app.config import ImageStatus

# Image statuses served from the public R2 bucket. Any other status is protected
# and lives in the private bucket (accessed via presigned URLs).
PUBLIC_IMAGE_STATUSES_FOR_R2: frozenset[int] = frozenset(
    {ImageStatus.ACTIVE, ImageStatus.SPOILER, ImageStatus.REPOST}
)

# Key prefixes under each R2 bucket. Matches the local FS layout so the
# one-time bucket split requires no key rewriting.
R2_VARIANTS: tuple[str, ...] = ("fullsize", "thumbs", "medium", "large")


class R2Location(IntEnum):
    """Where an image's R2 objects physically live.

    NONE    — not yet synced to R2 (pending finalizer, or R2 disabled).
    PUBLIC  — canonical copy lives in R2_PUBLIC_BUCKET.
    PRIVATE — canonical copy lives in R2_PRIVATE_BUCKET.
    """

    NONE = 0
    PUBLIC = 1
    PRIVATE = 2
