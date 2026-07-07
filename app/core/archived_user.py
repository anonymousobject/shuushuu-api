"""The shared 'Archived User' system account used for unmapped phpBB posters.

The account is *not* seeded by a migration; it is created on demand by the
import tooling via ``ensure_archived_user`` so that databases which never run an
import stay untouched (and so the test harness, which rebuilds an empty users
table, never collides with it).
"""

from sqlalchemy import select, text
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

    gender is NOT NULL without a default; '' is an existing valid value.
    """
    existing = await get_archived_user_id(db)
    if existing is None:
        await db.execute(
            text(
                "INSERT INTO users (username, password, password_type, salt, email, active, "
                "admin, gender, date_joined) "
                "SELECT :u, '!', 'bcrypt', '!', 'archived@localhost', 0, 0, '', NOW() FROM DUAL "
                "WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = :u)"
            ),
            {"u": ARCHIVED_USERNAME},
        )
        await db.commit()
        existing = await get_archived_user_id(db)
    assert existing is not None
    return existing
