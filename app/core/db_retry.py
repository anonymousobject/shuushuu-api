"""Retry helper for transient Postgres write conflicts. See ADR-0004.

Two different errors, one remedy — end the transaction and replay the unit on a
fresh one:

**40P01 (deadlock_detected)** — two transactions take locks on the same rows or
index entries in opposite orders and Postgres breaks the cycle by aborting one.
Retrying is not a workaround here, it *is* the contract: Postgres expects the
victim to replay. Confirmed site: flagging a repost (whose migration updates
``ml_tag_suggestions`` across the original's tags) while the ML pipeline
inserts suggestions for a neighbouring image.

**40001 (serialization_failure)** — a write would break the transaction's
snapshot under REPEATABLE READ or SERIALIZABLE. The app runs at the READ
COMMITTED default, so this is rare, but Postgres documents the same remedy
for it, so it takes the same path.

Both leave the transaction aborted, so both need the same rollback-and-replay.
55P03 (lock_not_available, raised when ``lock_timeout`` elapses) is
deliberately NOT included: it fires only after the timeout, so replaying it
multiplies an already pathological request latency rather than resolving a
momentary collision.

A savepoint is NOT sufficient for either — rolling back to a savepoint keeps
the transaction (and its snapshot) alive, and Postgres refuses further
statements in an aborted transaction anyway.

Usage — wrap a *self-contained transactional unit* and retry it:

    async def _apply() -> Thing:
        row = await db.get(Thing, thing_id)   # (re)fetch INSIDE the unit
        ...mutate/insert...
        await db.commit()                     # or flush
        return row

    thing = await retry_on_transient_conflict(db, _apply, what="thing_update")

Rules for the callable:
- Re-fetch rows inside it. The rollback between attempts expires every ORM
  instance in the session, and touching an expired attribute on an async
  session raises; closures over previously-loaded instances are bugs. This
  includes the authenticated user loaded by the auth dependency.
- DB work only. Non-DB side effects (file writes, redis/arq enqueues, email)
  would be repeated on retry; keep them outside the callable.
- Idempotent under replay. A retry re-runs the whole unit against a clean
  slate, so any count it derives must be derived again, not accumulated.
- Non-conflict errors (HTTPException, other DB errors) propagate unchanged.

This is deliberately opt-in per write path rather than request-replay
middleware: replaying a whole request would re-run its non-DB side effects.

The SQLSTATE lives on ``exc.orig.sqlstate``: SQLAlchemy's asyncpg adapter
copies it there when it translates the driver error, and wraps the result as
a plain ``DBAPIError`` (not ``OperationalError``), so that is what is caught.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

DEADLOCK_SQLSTATE = "40P01"
SERIALIZATION_FAILURE_SQLSTATE = "40001"
TRANSIENT_CONFLICT_SQLSTATES = frozenset({DEADLOCK_SQLSTATE, SERIALIZATION_FAILURE_SQLSTATE})
TRANSIENT_CONFLICT_ATTEMPTS = 3


def _conflict_sqlstate(exc: DBAPIError) -> str | None:
    """The SQLSTATE `exc` carries, or None when the driver error has none."""
    sqlstate = getattr(exc.orig, "sqlstate", None)
    return sqlstate if isinstance(sqlstate, str) else None


def is_transient_conflict(exc: DBAPIError) -> bool:
    """True when `exc` is a conflict a fresh transaction can resolve."""
    return _conflict_sqlstate(exc) in TRANSIENT_CONFLICT_SQLSTATES


async def retry_on_transient_conflict[T](
    db: AsyncSession,
    fn: Callable[[], Awaitable[T]],
    *,
    what: str,
    attempts: int = TRANSIENT_CONFLICT_ATTEMPTS,
) -> T:
    """Run `fn`, retrying up to `attempts` times on transient write conflicts.

    Rolls back between attempts so each retry runs in a fresh transaction
    (fresh snapshot, no inherited locks). Exhausted retries and non-conflict
    errors re-raise. `what` names the call site in the retry log line.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except DBAPIError as e:
            if not is_transient_conflict(e) or attempt == attempts:
                raise
            await db.rollback()
            logger.warning(
                "transient_conflict_retry",
                what=what,
                attempt=attempt,
                sqlstate=_conflict_sqlstate(e),
            )
    raise AssertionError("unreachable")  # pragma: no cover
