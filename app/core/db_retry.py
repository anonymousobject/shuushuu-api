"""Retry helper for transient MariaDB write conflicts. See ADR-0004.

Two different errors, one remedy — end the transaction and replay the unit on a
fresh one:

**1020 (ER_CHECKREAD)** — with ``innodb_snapshot_isolation=ON`` (MariaDB 11.8),
a locking statement that meets row/index versions committed *after* the
transaction's snapshot aborts instead of returning stale data. The snapshot is
pinned by the request's first read — the auth query — so the conflict window
spans nearly the whole request, and any two requests writing the same rows (or
inserting into the same index positions) concurrently can collide. Confirmed
sites: the upload temp-row INSERT into ``images``; concurrent ``PATCH
/users/me`` UPDATEs of one ``users`` row; ``ml_raw_predictions`` ingest racing
the suggestion pipeline.

**1213 (ER_LOCK_DEADLOCK)** — two transactions take locks on the same rows or
index gaps in opposite orders and InnoDB breaks the cycle by rolling one back.
Retrying is not a workaround here, it *is* the contract: MariaDB expects the
victim to replay. Confirmed site: flagging a repost (whose migration updates
``ml_tag_suggestions`` across the original's tags) while the ML pipeline
inserts suggestions for a neighbouring image.

Both leave the transaction dead, so both need the same rollback-and-replay.
1205 (ER_LOCK_WAIT_TIMEOUT) is deliberately NOT included: it fires only after
``innodb_lock_wait_timeout`` seconds, so replaying it multiplies an already
pathological request latency rather than resolving a momentary collision.

A savepoint is NOT sufficient for either — rolling back to a savepoint keeps
the transaction (and its snapshot) alive, so the conflict would recur.

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
"""

from collections.abc import Awaitable, Callable

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

SNAPSHOT_CONFLICT_ERRNO = 1020
DEADLOCK_ERRNO = 1213
TRANSIENT_CONFLICT_ERRNOS = frozenset({SNAPSHOT_CONFLICT_ERRNO, DEADLOCK_ERRNO})
TRANSIENT_CONFLICT_ATTEMPTS = 3


def _conflict_errno(exc: OperationalError) -> int | None:
    """The MariaDB errno `exc` carries, or None when it carries none."""
    args = getattr(exc.orig, "args", None)
    if not args:
        return None
    errno = args[0]
    return errno if isinstance(errno, int) else None


def is_transient_conflict(exc: OperationalError) -> bool:
    """True when `exc` is a conflict a fresh transaction can resolve."""
    return _conflict_errno(exc) in TRANSIENT_CONFLICT_ERRNOS


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
        except OperationalError as e:
            if not is_transient_conflict(e) or attempt == attempts:
                raise
            await db.rollback()
            logger.warning(
                "transient_conflict_retry",
                what=what,
                attempt=attempt,
                errno=_conflict_errno(e),
            )
    raise AssertionError("unreachable")  # pragma: no cover
