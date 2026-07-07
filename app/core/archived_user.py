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

# Cached across the process; the account never changes once created.
_archived_user_id: int | None = None


async def _lookup(db: AsyncSession) -> int | None:
    """Read the Archived User's user_id straight from the DB (uncached)."""
    result = await db.execute(
        select(Users.user_id).where(Users.username == ARCHIVED_USERNAME)  # type: ignore[call-overload]
    )
    return result.scalar_one_or_none()


async def get_archived_user_id(db: AsyncSession) -> int | None:
    """Return the Archived User's user_id (cached), or None if not created."""
    global _archived_user_id
    if _archived_user_id is None:
        _archived_user_id = await _lookup(db)
    return _archived_user_id


async def ensure_archived_user(db: AsyncSession) -> int:
    """Create the Archived User if absent (idempotent) and return its user_id.

    The direct INSERT bypasses app validation by design; the account is inactive
    ('active' = 0) and cannot log in. ``gender`` is NOT NULL without a default,
    so '' (an existing valid value) is supplied. The row is re-read from the DB
    after committing rather than trusting the module cache, so the result is
    correct even under the test suite's transaction-rollback isolation.
    """
    await db.execute(
        text(
            "INSERT INTO users (username, password, password_type, salt, email, "
            "active, admin, gender, date_joined) "
            "SELECT :username, '!', 'bcrypt', '!', 'archived@localhost', 0, 0, '', NOW() "
            "FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = :username)"
        ),
        {"username": ARCHIVED_USERNAME},
    )
    await db.commit()
    user_id = await _lookup(db)
    if user_id is None:  # the INSERT above guarantees a row exists
        raise RuntimeError("Archived User could not be created")
    return user_id
