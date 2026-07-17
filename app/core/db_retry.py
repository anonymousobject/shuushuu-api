"""Retry helper for MariaDB snapshot-isolation conflicts (ER_CHECKREAD, errno 1020).

With ``innodb_snapshot_isolation=ON`` (MariaDB 11.8), a locking
statement that meets row/index versions committed *after* the transaction's
snapshot aborts with error 1020 instead of returning stale data. The snapshot
is pinned by the request's first read — the auth query — so the conflict
window spans nearly the whole request, and any two requests writing the same
rows (or inserting into the same index positions) concurrently can collide.
Confirmed sites: the upload temp-row INSERT into ``images``; concurrent
``PATCH /users/me`` UPDATEs of one ``users`` row.

The conflict is transient: a rollback ends the transaction, and the retry's
new transaction gets a fresh snapshot that sees the other writer's rows. A
savepoint is NOT sufficient — rolling back to a savepoint keeps the
transaction (and its snapshot) alive, so the conflict would recur.

Usage — wrap a *self-contained transactional unit* and retry it:

    async def _apply() -> Thing:
        row = await db.get(Thing, thing_id)   # (re)fetch INSIDE the unit
        ...mutate/insert...
        await db.commit()                     # or flush
        return row

    thing = await retry_on_snapshot_conflict(db, _apply, what="thing_update")

Rules for the callable:
- Re-fetch rows inside it. The rollback between attempts expires every ORM
  instance in the session, and touching an expired attribute on an async
  session raises; closures over previously-loaded instances are bugs.
- DB work only. Non-DB side effects (file writes, redis/arq enqueues, email)
  would be repeated on retry; keep them outside the callable.
- Non-conflict errors (HTTPException, other DB errors) propagate unchanged.

This is deliberately opt-in per write path rather than request-replay
middleware: replaying a whole request would re-run its non-DB side effects.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

SNAPSHOT_CONFLICT_ERRNO = 1020
SNAPSHOT_CONFLICT_ATTEMPTS = 3


def is_snapshot_conflict(exc: OperationalError) -> bool:
    """True when `exc` wraps MariaDB ER_CHECKREAD (errno 1020)."""
    args = getattr(exc.orig, "args", None)
    if not args:
        return False
    return bool(args[0] == SNAPSHOT_CONFLICT_ERRNO)


async def retry_on_snapshot_conflict[T](
    db: AsyncSession,
    fn: Callable[[], Awaitable[T]],
    *,
    what: str,
    attempts: int = SNAPSHOT_CONFLICT_ATTEMPTS,
) -> T:
    """Run `fn`, retrying up to `attempts` times on snapshot conflicts.

    Rolls back between attempts so each retry runs in a fresh transaction
    (fresh snapshot). Exhausted retries and non-conflict errors re-raise.
    `what` names the call site in the retry log line.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except OperationalError as e:
            if not is_snapshot_conflict(e) or attempt == attempts:
                raise
            await db.rollback()
            logger.warning("snapshot_conflict_retry", what=what, attempt=attempt)
    raise AssertionError("unreachable")  # pragma: no cover
