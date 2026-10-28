"""The shared 'Archived User' system account used for unmapped phpBB posters.

The account is *not* seeded by a migration; it is created on demand by the
import tooling via ``ensure_archived_user`` so that databases which never run an
import stay untouched (and so the test harness, which rebuilds an empty users
table, never collides with it).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Users

ARCHIVED_USERNAME = "Archived User"


async def get_archived_user_id(db: AsyncSession) -> int | None:
    """Return the Archived User's user_id, or None if the account doesn't exist.

    Deliberately NOT cached: the lookup is a single indexed query, and caching a
    user_id in a module global is unsafe under the test suite's rollback isolation
    (a stale id from a rolled-back test could leak into a later one and mis-fire
    the display override).
    """
    result = await db.execute(
        select(Users.user_id).where(Users.username == ARCHIVED_USERNAME)  # type: ignore[call-overload]
    )
    return result.scalar_one_or_none()


async def ensure_archived_user(db: AsyncSession) -> int:
    """Create the Archived User if absent (idempotent); return its user_id. Commits.

    Inserted through the model, not raw SQL: most users columns are NOT NULL
    with only Python-side defaults. The account is inactive (cannot log in).
    """
    existing = await get_archived_user_id(db)
    if existing is None:
        db.add(
            Users(
                username=ARCHIVED_USERNAME,
                password="!",
                password_type="bcrypt",
                salt="!",
                email="archived@localhost",
                active=0,
                admin=0,
            )
        )
        await db.commit()
        existing = await get_archived_user_id(db)
    assert existing is not None
    return existing
