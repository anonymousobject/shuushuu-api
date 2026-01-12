"""
Permission sync: Ensure database perms table matches Permission enum.

This module is the mechanism that makes the Permission enum the single
source of truth for permissions. On startup, it:
- Inserts any permissions in enum but not in DB
- Warns about orphan permissions in DB but not in enum
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.permissions import Permission
from app.models.permissions import Perms

logger = get_logger(__name__)


async def sync_permissions(db: AsyncSession) -> None:
    """
    Ensure database perms table matches Permission enum.

    - Inserts any permissions in enum but not in DB
    - Warns about orphan permissions in DB but not in enum
    - Idempotent - safe to run on every startup

    Args:
        db: Database session
    """
    enum_titles = {p.value for p in Permission}

    # Get all existing permissions from DB
    result = await db.execute(select(Perms))
    db_perms = {p.title: p for p in result.scalars().all()}
    db_titles = set(db_perms.keys())

    # Insert missing permissions
    missing = enum_titles - db_titles
    for perm in Permission:
        if perm.value in missing:
            db.add(Perms(title=perm.value, desc=perm.description))
            logger.info("permission_seeded", title=perm.value)

    # Warn about orphans (in DB but not in enum)
    orphans = db_titles - enum_titles
    for title in orphans:
        logger.warning(
            "orphan_permission",
            title=title,
            hint="Permission exists in DB but not in code",
        )

    await db.commit()
    logger.info("permissions_synced", total=len(enum_titles), added=len(missing))
