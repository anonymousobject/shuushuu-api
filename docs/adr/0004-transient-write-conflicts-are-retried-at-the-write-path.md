# Transient write conflicts are retried at the write path

MariaDB aborts a transaction for two reasons that a fresh transaction resolves: ER_CHECKREAD (1020), raised under `innodb_snapshot_isolation=ON` when a locking statement meets a row version committed after this transaction's snapshot, and ER_LOCK_DEADLOCK (1213), raised when InnoDB breaks a lock cycle by rolling one side back. Both leave the transaction dead, and neither indicates a problem with the request. `retry_on_transient_conflict()` wraps a self-contained transactional unit, rolls back between attempts so each retry gets a fresh snapshot and no inherited locks, and replays up to three times. Write paths opt in individually rather than being covered by middleware.

## Considered Options

- **Surfacing the error** is what shipped, and a moderator flagging a repost while the ML pipeline writes suggestions eats a 500 with the migration abandoned partway (#335). For 1213 this also contradicts MariaDB's contract, which is that the deadlock victim replays.
- **Request-replay middleware** would cover every path at once, but a replayed request re-runs its non-DB side effects — R2 enqueues, rating recalculation, file writes, email. Opting in per path keeps those below the retried unit where a replay cannot reach them.
- **Ordering the lock acquisition** so the two writers cannot form a cycle removes one specific pair, at the cost of coupling the ML pipeline to the moderation path, and does nothing for the other pairs that can collide on the same rows. Narrowing the lock set is worth doing on its own merits (ADR-0005) but is a probability reduction, not a guarantee.
- **Including ER_LOCK_WAIT_TIMEOUT (1205)** was rejected: it fires only after `innodb_lock_wait_timeout` seconds, so replaying it multiplies an already pathological request latency instead of resolving a momentary collision. It still surfaces as a 500, deliberately.

## Consequences

- A wrapped callable must be DB-only, idempotent under replay, and end at its `commit()`. Anything after the commit that can raise would send a replay through a second audit row on top of a commit that already landed.
- `Session.rollback()` expires every ORM instance regardless of `expire_on_commit`, so a wrapped callable must re-fetch the rows it touches — including the user loaded by the auth dependency, which is why the retried admin paths re-`get()` the actor rather than closing over `current_user`.
- Retries are bounded at three and logged as `transient_conflict_retry` with the call site and errno, so a path that starts thrashing is visible rather than silently slow.
- Every call site added for 1020 now also absorbs deadlocks; they already satisfied the contract, so no site needed changing when 1213 was added.
- A savepoint is not a substitute for the rollback: rolling back to a savepoint keeps the transaction and its snapshot alive, so the conflict recurs.
