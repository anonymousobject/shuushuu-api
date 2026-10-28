# Transient write conflicts are retried at the write path

Postgres aborts a transaction for two reasons that a fresh transaction resolves: `deadlock_detected` (SQLSTATE 40P01), raised for the transaction Postgres aborts to break a lock cycle, and `serialization_failure` (40001), raised when a write would break the snapshot under REPEATABLE READ or SERIALIZABLE. Both leave the transaction aborted, and neither indicates a problem with the request. `retry_on_transient_conflict()` wraps a self-contained transactional unit, rolls back between attempts so each retry gets a fresh snapshot and no inherited locks, and replays up to three times. Write paths opt in individually rather than being covered by middleware.

This ADR was written against MariaDB (errnos 1020 and 1213). After the 2026-08-22 cutover the helper was found to be inert on Postgres: SQLAlchemy's asyncpg adapter wraps a Postgres error as `DBAPIError` with the SQLSTATE on `orig.sqlstate`, not as `OperationalError` with an integer errno. The contract is unchanged; the match is now by SQLSTATE.

## Considered Options

- **Surfacing the error** is what shipped, and a moderator flagging a repost while the ML pipeline writes suggestions eats a 500 with the migration abandoned partway (#335). For 40P01 this also contradicts Postgres's contract, which is that the deadlock victim replays.
- **Request-replay middleware** would cover every path at once, but a replayed request re-runs its non-DB side effects — R2 enqueues, rating recalculation, file writes, email. Opting in per path keeps those below the retried unit where a replay cannot reach them.
- **Ordering the lock acquisition** so the two writers cannot form a cycle removes one specific pair, at the cost of coupling the ML pipeline to the moderation path, and does nothing for the other pairs that can collide on the same rows. Narrowing the lock set is worth doing on its own merits (ADR-0005) but is a probability reduction, not a guarantee.
- **Including `lock_not_available` (55P03)** was rejected: it fires only after `lock_timeout` elapsed, so replaying it multiplies an already pathological request latency instead of resolving a momentary collision. It still surfaces as a 500, deliberately.

## Consequences

- A wrapped callable must be DB-only, idempotent under replay, and end at its `commit()`. Anything after the commit that can raise would send a replay through a second audit row on top of a commit that already landed.
- `Session.rollback()` expires every ORM instance regardless of `expire_on_commit`, so a wrapped callable must re-fetch the rows it touches — including the user loaded by the auth dependency, which is why the retried admin paths re-`get()` the actor rather than closing over `current_user`.
- Retries are bounded at three and logged as `transient_conflict_retry` with the call site and SQLSTATE, so a path that starts thrashing is visible rather than silently slow.
- Every call site opted in for MariaDB's snapshot conflicts already satisfied the contract, so none needed changing when the match moved to SQLSTATE.
- A savepoint is not a substitute for the rollback: rolling back to a savepoint keeps the transaction and its snapshot alive, so the conflict recurs.
