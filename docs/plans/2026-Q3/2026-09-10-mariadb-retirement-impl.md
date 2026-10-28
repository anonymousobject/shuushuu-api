# MariaDB Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every trace of MariaDB from the codebase so Postgres is the only database the app, tests, CI, compose, and migrations know about, and fix the two live Postgres defects the transition left behind.

**Architecture:** Four PRs in strict order. PR 1 makes the transient-conflict retry match Postgres SQLSTATEs. PR 2 ports the nightly affinity rebuild to one Postgres transaction. PR 3 removes MariaDB from CI, compose, conftest, dependencies, and the Alembic layout so the repo runs only on Postgres. PR 4 deletes the MariaDB arm of every dialect branch and the MySQL-specific machinery. Docs and ADRs land on `main` directly afterwards.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2 async on asyncpg, Alembic, pytest + pytest-xdist, docker compose, GitHub Actions, `gh` CLI.

**Spec:** `docs/plans/2026-Q3/2026-09-10-mariadb-retirement-design.md`

## Global Constraints

- Postgres is the only backend. Every `is_postgres` branch keeps its Postgres arm verbatim; no behavior change rides along with a deletion (spec decision 1).
- Order is strict: PR 1 → PR 2 → PR 3 → PR 4 → docs. Each PR must be green on its own.
- Tests are deleted only where the spec's "Tests deleted" table lists them. Nothing else is deleted or skipped.
- Point-in-time docs (`docs/plans/**`) are never edited. The runbook gets a two-line note and nothing else.
- Every commit message ends with the session's two attribution lines:
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01YJ1ryWZMt6jVJ8iEN9f7PQ`. Every PR body ends with the generated-with footer and the session URL.
- `app/` stays at zero mypy errors: `uv run mypy app/`. Scripts you edit must be clean individually: `uv run mypy scripts/<file>.py`.
- Until PR 3 lands, local test runs use `./run-tests.sh --pg <path>` (serial) or `./run-tests.sh --pg` (parallel full suite). From PR 3 on, drop `--pg`. The dev-stack Postgres container must be up: `docker compose up -d postgres`.
- Work each PR in its own worktree (`superpowers:using-git-worktrees`, `.worktrees/<branch>`), copying `.env` in. When several agents run tests at once, give each a distinct `TEST_DATABASE_URL` database name (`shuushuu_pytest_<suffix>`).
- Before pushing any branch that touches `alembic/`, run `uv run alembic heads` and confirm exactly one head.

---

# PR 1 — retry helper on Postgres

Branch: `fix/db-retry-postgres`. Spec decision 2.

Background for the implementer: SQLAlchemy's asyncpg adapter translates an asyncpg `PostgresError` into its own `AsyncAdapt_asyncpg_dbapi.Error`, copies the SQLSTATE onto `.sqlstate` and `.pgcode`, and SQLAlchemy wraps that as plain `sqlalchemy.exc.DBAPIError`. So a Postgres deadlock is a `DBAPIError` whose `orig.sqlstate == "40P01"`. The current helper catches `OperationalError` and reads an integer errno from `orig.args[0]`, so it never matches.

### Task 1.1: Prove the defect with a real deadlock

**Files:**
- Create: `tests/integration/test_db_retry_deadlock.py`

**Interfaces:**
- Consumes: `retry_on_transient_conflict(db, fn, *, what)` from `app/core/db_retry.py`; the `engine` and `db_session` fixtures from `tests/conftest.py`.

- [ ] **Step 1: Write the failing integration test**

```python
"""The retry helper against a real Postgres deadlock (app/core/db_retry.py).

The unit tests fabricate the adapter's error; this provokes the genuine one.
Two sessions update the same two users rows in opposite order. Postgres
detects the cycle after deadlock_timeout (1s by default) and aborts one side
with SQLSTATE 40P01; the aborted side must replay on a fresh transaction and
succeed.
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db_retry import retry_on_transient_conflict

# needs_commit: users 1-3 are really committed, so both sessions see them.
pytestmark = [pytest.mark.integration, pytest.mark.needs_commit]


async def test_deadlock_victim_replays_and_succeeds(db_session, engine):
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # Both sides must hold their first row before either asks for the second,
    # or there is no cycle. Only the first attempt waits: a replay runs after
    # the winner committed and just proceeds.
    first_locks_taken = asyncio.Barrier(2)
    calls = {"a": 0, "b": 0}

    async def cross_update(name: str, first: int, second: int) -> str:
        async with sessions() as db:

            async def unit() -> str:
                calls[name] += 1
                await db.execute(
                    text("UPDATE users SET location = :v WHERE user_id = :id"),
                    {"v": name, "id": first},
                )
                if calls[name] == 1:
                    await first_locks_taken.wait()
                await db.execute(
                    text("UPDATE users SET location = :v WHERE user_id = :id"),
                    {"v": name, "id": second},
                )
                await db.commit()
                return name

            return await retry_on_transient_conflict(db, unit, what=f"deadlock_{name}")

    results = await asyncio.gather(cross_update("a", 1, 2), cross_update("b", 2, 1))

    assert sorted(results) == ["a", "b"]
    # Exactly one side was the victim and replayed once.
    assert sorted(calls.values()) == [1, 2]
```

- [ ] **Step 2: Run it and confirm it fails for the right reason**

Run: `./run-tests.sh --pg tests/integration/test_db_retry_deadlock.py -v`
Expected: FAIL. `asyncio.gather` raises `sqlalchemy.exc.DBAPIError` with `deadlock detected` in the message, because the helper's `except OperationalError` never sees it.

- [ ] **Step 3: Commit the red test**

```bash
git add tests/integration/test_db_retry_deadlock.py
git commit -m "test: a real Postgres deadlock is not retried"
```

### Task 1.2: Unit tests and the shared fabrication helper

**Files:**
- Modify: `tests/transient_conflict.py`
- Modify: `tests/unit/test_db_retry.py`

**Interfaces:**
- Produces: `_db_error(sqlstate: str, message: str) -> DBAPIError`, `_deadlock_error() -> DBAPIError`, `_serialization_error() -> DBAPIError` in `tests/transient_conflict.py`. The `_flaky_commit`, `_flaky_flush`, `_flaky_flush_nth` helpers keep their signatures but take a `DBAPIError`.
- Produces (app side, implemented in Task 1.3): `is_transient_conflict(exc: DBAPIError) -> bool`, `TRANSIENT_CONFLICT_SQLSTATES`, `DEADLOCK_SQLSTATE = "40P01"`, `SERIALIZATION_FAILURE_SQLSTATE = "40001"`.

- [ ] **Step 1: Replace the top of `tests/transient_conflict.py`**

Replace everything from the module docstring through `_deadlock_error` (the three `_flaky_*` helpers stay, with their `error: OperationalError` annotations changed to `error: DBAPIError`):

```python
"""Helpers for testing the Postgres transient-conflict retry (app/core/db_retry.py).

Two errors get the same rollback-and-replay treatment:

- 40P01 deadlock_detected: two transactions took row locks in opposite orders
  and Postgres aborted one to break the cycle.
- 40001 serialization_failure: a write would break the transaction's snapshot
  (REPEATABLE READ / SERIALIZABLE; rare under the READ COMMITTED default).

Write paths wrap their transactional unit in ``retry_on_transient_conflict`` so
either one is retried on a fresh transaction instead of surfacing a 500.

These helpers inject the error into a route's explicit flush or its commit,
which is what lets a test exercise the real helper without racing two live
requests.
"""

from unittest.mock import patch

from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession


def _db_error(sqlstate: str, message: str) -> DBAPIError:
    """Build the error SQLAlchemy raises for a Postgres error with `sqlstate`.

    The asyncpg adapter translates asyncpg's PostgresError into its own dbapi
    Error, copying the SQLSTATE onto .sqlstate/.pgcode, and SQLAlchemy wraps
    that as a plain DBAPIError (not OperationalError) — which is what the
    helper must catch.
    """
    orig = AsyncAdapt_asyncpg_dbapi.Error(message)
    orig.sqlstate = orig.pgcode = sqlstate
    return DBAPIError("UPDATE ...", None, orig)


def _deadlock_error() -> DBAPIError:
    """The error Postgres raises for the transaction it aborts to break a lock cycle."""
    return _db_error("40P01", "deadlock detected")


def _serialization_error() -> DBAPIError:
    """The error Postgres raises when a write would break the transaction's snapshot."""
    return _db_error("40001", "could not serialize access due to concurrent update")
```

- [ ] **Step 2: Rewrite `tests/unit/test_db_retry.py`**

```python
"""Tests for the transient-conflict retry helper (app/core/db_retry.py)."""

import pytest
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.db_retry import is_transient_conflict, retry_on_transient_conflict
from tests.transient_conflict import _db_error, _deadlock_error, _serialization_error


class _StubSession:
    """Records rollback calls; the helper must roll back between attempts."""

    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


class TestIsTransientConflict:
    def test_matches_deadlock_40p01(self):
        assert is_transient_conflict(_deadlock_error()) is True

    def test_matches_serialization_failure_40001(self):
        assert is_transient_conflict(_serialization_error()) is True

    def test_rejects_unique_violation(self):
        # Duplicate key is a real data conflict, not a transient lock conflict:
        # replaying it produces the same error, so it must surface to the caller.
        assert is_transient_conflict(_db_error("23505", "duplicate key value")) is False

    def test_rejects_lock_timeout(self):
        # 55P03 fires only after lock_timeout elapsed, so replaying it multiplies
        # an already pathological latency — see the module docstring.
        assert is_transient_conflict(_db_error("55P03", "canceling statement due to lock timeout")) is False

    def test_rejects_error_without_sqlstate(self):
        assert is_transient_conflict(DBAPIError("STATEMENT", None, Exception())) is False

    def test_rejects_operational_error_without_sqlstate(self):
        # A driver-level OperationalError (connection dropped) carries no SQLSTATE.
        assert is_transient_conflict(OperationalError("STATEMENT", None, Exception("gone"))) is False


class TestRetryOnTransientConflict:
    @pytest.mark.asyncio
    async def test_returns_value_on_first_success_without_rollback(self):
        db = _StubSession()

        async def fn() -> str:
            return "ok"

        assert await retry_on_transient_conflict(db, fn, what="test") == "ok"
        assert db.rollbacks == 0

    @pytest.mark.asyncio
    async def test_retries_deadlock_with_rollback_between_attempts(self):
        db = _StubSession()
        calls = 0

        async def fn() -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise _deadlock_error()
            return "ok"

        assert await retry_on_transient_conflict(db, fn, what="test") == "ok"
        assert calls == 3
        assert db.rollbacks == 2  # one per failed attempt

    @pytest.mark.asyncio
    async def test_retries_serialization_failure_with_rollback_between_attempts(self):
        db = _StubSession()
        calls = 0

        async def fn() -> str:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise _serialization_error()
            return "ok"

        assert await retry_on_transient_conflict(db, fn, what="test") == "ok"
        assert calls == 2
        assert db.rollbacks == 1

    @pytest.mark.asyncio
    async def test_reraises_after_attempts_exhausted(self):
        db = _StubSession()
        calls = 0

        async def fn() -> None:
            nonlocal calls
            calls += 1
            raise _deadlock_error()

        with pytest.raises(DBAPIError):
            await retry_on_transient_conflict(db, fn, what="test")
        assert calls == 3  # default bound
        assert db.rollbacks == 2  # no rollback after the final, re-raised failure

    @pytest.mark.asyncio
    async def test_does_not_retry_other_db_errors(self):
        db = _StubSession()
        calls = 0

        async def fn() -> None:
            nonlocal calls
            calls += 1
            raise _db_error("23505", "duplicate key value")

        with pytest.raises(DBAPIError):
            await retry_on_transient_conflict(db, fn, what="test")
        assert calls == 1
        assert db.rollbacks == 0

    @pytest.mark.asyncio
    async def test_does_not_swallow_non_db_errors(self):
        db = _StubSession()

        async def fn() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await retry_on_transient_conflict(db, fn, what="test")
        assert db.rollbacks == 0

    @pytest.mark.asyncio
    async def test_attempts_override(self):
        db = _StubSession()
        calls = 0

        async def fn() -> None:
            nonlocal calls
            calls += 1
            raise _deadlock_error()

        with pytest.raises(DBAPIError):
            await retry_on_transient_conflict(db, fn, what="test", attempts=5)
        assert calls == 5
        assert db.rollbacks == 4
```

- [ ] **Step 3: Run the unit tests and confirm they fail**

Run: `uv run pytest tests/unit/test_db_retry.py -v`
Expected: the `matches_*` and `retries_*` tests FAIL (`is_transient_conflict` returns False; the retry re-raises on the first attempt). The `rejects_*` tests may already pass.

- [ ] **Step 4: Commit the red tests**

```bash
git add tests/transient_conflict.py tests/unit/test_db_retry.py
git commit -m "test: retry helper unit tests speak Postgres SQLSTATE"
```

### Task 1.3: Implement SQLSTATE matching and move the call-site tests

**Files:**
- Modify: `app/core/db_retry.py`
- Modify: `tests/api/v1/test_users.py:3590-3700` (two tests)
- Modify: `tests/api/v1/test_ml_tag_suggestions.py:1610-1710`
- Modify: `tests/services/test_ml_suggestion_review_bulk.py:420-490`
- Modify: `tests/api/v1/test_upload.py:495-560`

- [ ] **Step 1: Rewrite `app/core/db_retry.py`**

Replace the whole module with:

```python
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
```

- [ ] **Step 2: Run the unit and integration tests**

Run: `uv run pytest tests/unit/test_db_retry.py -v && ./run-tests.sh --pg tests/integration/test_db_retry_deadlock.py -v`
Expected: all PASS.

- [ ] **Step 3: Move the call-site tests onto the shared helper**

In `tests/api/v1/test_users.py` (two tests near line 3596 and 3673), `tests/api/v1/test_ml_tag_suggestions.py` (near line 1617), and `tests/services/test_ml_suggestion_review_bulk.py` (near line 425): each test builds its error inline as

```python
                raise OperationalError(
                    "UPDATE users ...",
                    None,
                    pymysql.err.OperationalError(
                        1020, "Record has changed since last read in table 'users'"
                    ),
                )
```

(the statement string and table name vary per file). In each, replace the whole `raise OperationalError(...)` expression with `raise _deadlock_error()`, delete the local `import pymysql` and `from sqlalchemy.exc import OperationalError` lines, and add `from tests.transient_conflict import _deadlock_error` next to the test's other local imports. Where a test asserts `pytest.raises(OperationalError)` (the always-conflict test in `test_users.py`), change it to `pytest.raises(DBAPIError)` and import `DBAPIError` from `sqlalchemy.exc`. Update each docstring's "trip MariaDB ER_CHECKREAD (errno 1020)" wording to "hit a Postgres deadlock (SQLSTATE 40P01)".

In `tests/api/v1/test_upload.py`, replace every `_snapshot_conflict_error()` call with `_deadlock_error()` and fix its import from `tests.transient_conflict`; update the docstring near line 505 the same way. Then confirm nothing else references the removed names:

Run: `grep -rn '_snapshot_conflict_error\|pymysql\|errno' tests/ | grep -v '\.pyc'`
Expected: no output.

- [ ] **Step 4: Run the affected suites**

Run: `./run-tests.sh --pg tests/api/v1/test_users.py tests/api/v1/test_upload.py tests/api/v1/test_ml_tag_suggestions.py tests/services/test_ml_suggestion_review_bulk.py tests/api/v1/test_image_reposts_endpoint.py -q`
Expected: all PASS.

- [ ] **Step 5: Type-check and commit**

Run: `uv run mypy app/`
Expected: `Success: no issues found`.

```bash
git add app/core/db_retry.py tests/
git commit -m "fix(db_retry): match Postgres SQLSTATEs, not MariaDB errnos

A Postgres deadlock reaches the helper as DBAPIError with the code on
orig.sqlstate; the errno match never fired, so ADR-0004's retry was
inert since cutover and deadlocks surfaced as 500s."
```

### Task 1.4: Rewrite ADR-0004

**Files:**
- Modify: `docs/adr/0004-transient-write-conflicts-are-retried-at-the-write-path.md`

- [ ] **Step 1: Replace the ADR's first paragraph and the affected bullets**

Replace the opening paragraph with:

```markdown
Postgres aborts a transaction for two reasons that a fresh transaction resolves: `deadlock_detected` (SQLSTATE 40P01), raised for the transaction Postgres aborts to break a lock cycle, and `serialization_failure` (40001), raised when a write would break the snapshot under REPEATABLE READ or SERIALIZABLE. Both leave the transaction aborted, and neither indicates a problem with the request. `retry_on_transient_conflict()` wraps a self-contained transactional unit, rolls back between attempts so each retry gets a fresh snapshot and no inherited locks, and replays up to three times. Write paths opt in individually rather than being covered by middleware.

This ADR was written against MariaDB (errnos 1020 and 1213). After the 2026-08-22 cutover the helper was found to be inert on Postgres: SQLAlchemy's asyncpg adapter wraps a Postgres error as `DBAPIError` with the SQLSTATE on `orig.sqlstate`, not as `OperationalError` with an integer errno. The contract is unchanged; the match is now by SQLSTATE.
```

In "Considered Options", replace the first bullet's second sentence ("For 1213 this also contradicts MariaDB's contract...") with "For 40P01 this also contradicts Postgres's contract, which is that the deadlock victim replays." Replace the last bullet with:

```markdown
- **Including `lock_not_available` (55P03)** was rejected: it fires only after `lock_timeout` elapsed, so replaying it multiplies an already pathological request latency instead of resolving a momentary collision. It still surfaces as a 500, deliberately.
```

In "Consequences", replace "logged as `transient_conflict_retry` with the call site and errno" with "logged as `transient_conflict_retry` with the call site and SQLSTATE", and replace the bullet beginning "Every call site added for 1020" with:

```markdown
- Every call site opted in for MariaDB's snapshot conflicts already satisfied the contract, so none needed changing when the match moved to SQLSTATE.
```

- [ ] **Step 2: Commit**

```bash
git add docs/adr/0004-transient-write-conflicts-are-retried-at-the-write-path.md
git commit -m "docs(adr-0004): the retry contract is matched by SQLSTATE"
```

### Task 1.5: Full suite and PR

- [ ] **Step 1: Run the full suite in parallel**

Run: `./run-tests.sh --pg`
Expected: all pass, no errors in output.

- [ ] **Step 2: Open the PR**

Run: `git push -u origin fix/db-retry-postgres && gh pr create --title "fix(db_retry): retry Postgres deadlocks, not MariaDB errnos" --body-file -` with a body that states the defect (inert since cutover), the fix (DBAPIError + SQLSTATE 40P01/40001), the new real-deadlock integration test, and the ADR-0004 rewrite, ending with the attribution footer.

- [ ] **Step 3: Wait for CI, merge, then continue with PR 2 from an updated `main`.**

---

# PR 2 — affinity rebuild on Postgres

Branch: `feat/affinity-postgres`. Spec decision 3.

### Task 2.1: Un-mark the tests and re-express the lock tests

**Files:**
- Modify: `tests/services/test_user_tag_affinity.py`
- Modify: `tests/tasks/test_taste_profile_job.py:14-18`

- [ ] **Step 1: Remove the marker**

Replace

```python
# mariadb_only: refresh_user_tag_affinity raises NotImplementedError off-MariaDB
# by design (GET_LOCK, ENGINE=InnoDB helper tables).
pytestmark = [pytest.mark.integration, pytest.mark.needs_commit, pytest.mark.mariadb_only]
```

with

```python
pytestmark = [pytest.mark.integration, pytest.mark.needs_commit]
```

- [ ] **Step 2: Replace `test_lock_skip_returns_sentinel`**

```python
async def test_lock_skip_returns_sentinel(db_session, engine):
    # A second connection holds the advisory lock in an open transaction ->
    # refresh skips with -1. Advisory locks are per-session, so a fresh
    # connection() checkout from the shared per-test `engine` is a distinct
    # holder.
    from sqlalchemy import text as sqla_text

    db_name = (await db_session.execute(sqla_text("SELECT current_database()"))).scalar()
    lock_name = f"{_LOCK_PREFIX}:{db_name}"
    async with engine.connect() as other:
        got = (
            await other.execute(
                sqla_text("SELECT pg_try_advisory_xact_lock(hashtext(:n))"), {"n": lock_name}
            )
        ).scalar()
        assert got is True
        n = await refresh_user_tag_affinity(db_session, **REFRESH_KW)
        assert n == -1
        await other.rollback()
```

- [ ] **Step 3: Replace `test_lock_released_after_mid_run_failure`**

```python
async def test_lock_released_after_mid_run_failure(db_session, engine):
    # Forces a real (not mocked) mid-run failure: a second connection holds
    # user_tag_affinity in ACCESS EXCLUSIVE mode inside an open transaction,
    # so the service's DELETE blocks and, with lock_timeout set on the service
    # session, genuinely errors. This verifies the failure propagates (isn't
    # silently swallowed) AND that the advisory lock isn't leaked: a second
    # refresh on the SAME session, once the blocker releases, must succeed
    # rather than returning the locked-out sentinel (-1). The SET rides in the
    # aborted transaction, so the rollback in the service's finally clears it.
    from sqlalchemy import text as sqla_text
    from sqlalchemy.exc import DBAPIError

    async with engine.connect() as blocker:
        await blocker.execute(sqla_text("LOCK TABLE user_tag_affinity IN ACCESS EXCLUSIVE MODE"))
        await db_session.execute(sqla_text("SET lock_timeout = '1s'"))
        with pytest.raises(DBAPIError, match="lock timeout"):
            await refresh_user_tag_affinity(db_session, **REFRESH_KW)
        await blocker.rollback()  # release the table lock

    n = await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    assert n >= 0  # lock was released after the failure, not leaked
```

- [ ] **Step 4: Fix the job test's docstring**

In `tests/tasks/test_taste_profile_job.py`, change "the docker-compose-internal `mariadb` host" to "the docker-compose-internal `postgres` host".

- [ ] **Step 5: Run and confirm red**

Run: `./run-tests.sh --pg tests/services/test_user_tag_affinity.py -v`
Expected: every refresh test FAILS with `NotImplementedError: refresh_user_tag_affinity is MariaDB-only`.

- [ ] **Step 6: Commit**

```bash
git add tests/services/test_user_tag_affinity.py tests/tasks/test_taste_profile_job.py
git commit -m "test: affinity rebuild tests run on Postgres"
```

### Task 2.2: Port the service

**Files:**
- Modify: `app/services/user_tag_affinity.py`
- Modify: `app/models/user_tag_affinity.py:11-20` (docstring)
- Modify: `tests/integration/test_fk_constraint_names.py:98-102` (comment)

- [ ] **Step 1: Replace the module docstring**

```python
"""Refresh the precomputed per-user tag-affinity table (taste profiles).

For each eligible user (>= min_events favorites+ratings+uploads) and each tag
with minimum support, stores positive-pool counts (favorites ∪ uploads,
deduped), rating stats, a popularity-normalized lift, a per-user-mean-centered
rating delta, and the blended affinity used by /images/recommended.

One transaction: temp helper tables, the batched aggregation into the live
table after clearing it, and the commit. Readers keep seeing the previous rows
until the commit. The main aggregation is batched by user-id ranges — the
unbatched join is ~75M intermediate rows (5.7M favorites × ~13 tags/image).
A transaction-scoped advisory lock serializes the nightly cron and a manual
run; it and the temp tables vanish with the transaction, so a mid-run failure
leaves nothing behind.
"""
```

- [ ] **Step 2: Replace `_BATCH_INSERT`**

Integer division is the trap: `pool_cnt / pool_size` is integer division on Postgres, so every ratio is cast to float first.

```python
_BATCH_INSERT = """
INSERT INTO user_tag_affinity
    (user_id, tag_id, pool_cnt, fav_count, upload_count, rated_count,
     rating_avg, lift, rating_delta, affinity)
SELECT
    agg.user_id, agg.tag_id, agg.pool_cnt, agg.fav_count, agg.upload_count, agg.rated_count,
    agg.rating_sum::float / NULLIF(agg.rated_count, 0) AS rating_avg,
    CASE WHEN agg.pool_cnt > 0 AND u.pool_size > 0
         THEN (agg.pool_cnt::float / u.pool_size) / ((vc.vc + :k)::float / :n) END AS lift,
    agg.rating_sum::float / NULLIF(agg.rated_count, 0) - u.user_mean AS rating_delta,
    COALESCE(CASE WHEN agg.pool_cnt >= :min_support
                  THEN LN((agg.pool_cnt::float / u.pool_size) / ((vc.vc + :k)::float / :n)) END, 0)
    + :beta * COALESCE(CASE WHEN agg.rated_count >= :min_support
                            THEN agg.rating_sum::float / agg.rated_count - u.user_mean END, 0)
      AS affinity
FROM (
    SELECT y.user_id, y.tag_id,
           SUM(y.pool) AS pool_cnt, SUM(y.fav) AS fav_count, SUM(y.upl) AS upload_count,
           SUM(y.rated) AS rated_count, SUM(y.rsum) AS rating_sum
    FROM (
        SELECT p.user_id, vl.tag_id,
               1 AS pool, p.is_fav AS fav, p.is_upl AS upl, 0 AS rated, 0 AS rsum
        FROM _taste_pool p JOIN _taste_vl vl ON vl.image_id = p.image_id
        WHERE p.user_id BETWEEN :lo AND :hi
        UNION ALL
        SELECT r.user_id, vl.tag_id, 0, 0, 0, 1, r.rating
        FROM image_ratings r
        JOIN _taste_elig e ON e.user_id = r.user_id
        JOIN _taste_vl vl ON vl.image_id = r.image_id
        WHERE r.user_id BETWEEN :lo AND :hi
    ) y
    GROUP BY y.user_id, y.tag_id
    HAVING SUM(y.pool) >= :min_support OR SUM(y.rated) >= :min_support
) agg
JOIN _taste_users u ON u.user_id = agg.user_id
JOIN _taste_vc vc ON vc.tag_id = agg.tag_id
"""
```

- [ ] **Step 3: Replace `refresh_user_tag_affinity`**

Delete the `_HELPERS` tuple (temp tables need no cleanup list). Replace the function body from its docstring to the end of the module:

```python
async def refresh_user_tag_affinity(
    db: AsyncSession,
    *,
    min_support: int,
    smoothing_k: int,
    beta: float,
    min_events: int,
    batch_size: int,
) -> int:
    """Rebuild the user_tag_affinity table; return the number of rows written.

    Serialized by a transaction-scoped advisory lock so the nightly cron and a
    manual run cannot collide. Returns the sentinel ``-1`` (callers should treat
    ``< 0`` as "skipped") without touching any tables if another refresh already
    holds the lock.
    """
    # Advisory-lock keys are server-global; scope to the current database so
    # pytest-xdist per-worker DBs get independent locks while production's
    # single DB still serializes cron + manual runs.
    db_name = (await db.execute(text("SELECT current_database()"))).scalar()
    lock_name = f"{_LOCK_PREFIX}:{db_name}"
    locked = (
        await db.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:n))"), {"n": lock_name}
        )
    ).scalar()
    if not locked:
        logger.info("user_tag_affinity_refresh_skipped_locked")
        return -1
    try:
        # 1. canonical, visible links
        await _exec(
            db,
            """
            CREATE TEMP TABLE _taste_vl ON COMMIT DROP AS
            SELECT DISTINCT tl.image_id, COALESCE(t.alias_of, t.tag_id) AS tag_id
            FROM tag_links tl
            JOIN images i ON i.image_id = tl.image_id
            JOIN tags   t ON t.tag_id   = tl.tag_id
            WHERE i.status IN :public_statuses
            """,
            {"public_statuses": _PUBLIC},
        )
        await _exec(db, "ALTER TABLE _taste_vl ADD PRIMARY KEY (image_id, tag_id)")
        await _exec(db, "CREATE INDEX ON _taste_vl (tag_id)")

        # 2. visible tagged images, per-canonical-tag counts, and N
        await _exec(
            db, "CREATE TEMP TABLE _taste_vi ON COMMIT DROP AS SELECT DISTINCT image_id FROM _taste_vl"
        )
        await _exec(db, "ALTER TABLE _taste_vi ADD PRIMARY KEY (image_id)")
        await _exec(
            db,
            "CREATE TEMP TABLE _taste_vc ON COMMIT DROP AS "
            "SELECT tag_id, COUNT(*) AS vc FROM _taste_vl GROUP BY tag_id",
        )
        await _exec(db, "ALTER TABLE _taste_vc ADD PRIMARY KEY (tag_id)")
        n = (await db.execute(text("SELECT COUNT(*) FROM _taste_vi"))).scalar() or 0

        # 3. eligible users (raw event counts)
        await _exec(
            db,
            """
            CREATE TEMP TABLE _taste_elig ON COMMIT DROP AS
            SELECT user_id FROM (
                SELECT user_id FROM favorites
                UNION ALL SELECT user_id FROM image_ratings
                UNION ALL SELECT user_id FROM images WHERE user_id IS NOT NULL
            ) ev GROUP BY user_id HAVING COUNT(*) >= :min_events
            """,
            {"min_events": min_events},
        )
        await _exec(db, "ALTER TABLE _taste_elig ADD PRIMARY KEY (user_id)")

        # 4. deduped positive pool (favorites ∪ uploads), visible only
        await _exec(
            db,
            """
            CREATE TEMP TABLE _taste_pool ON COMMIT DROP AS
            SELECT x.user_id, x.image_id, MAX(x.is_fav) AS is_fav, MAX(x.is_upl) AS is_upl
            FROM (
                SELECT f.user_id, f.image_id, 1 AS is_fav, 0 AS is_upl
                FROM favorites f JOIN _taste_elig e ON e.user_id = f.user_id
                UNION ALL
                SELECT i.user_id, i.image_id, 0 AS is_fav, 1 AS is_upl
                FROM images i JOIN _taste_elig e ON e.user_id = i.user_id
            ) x JOIN _taste_vi vi ON vi.image_id = x.image_id
            GROUP BY x.user_id, x.image_id
            """,
        )
        await _exec(db, "ALTER TABLE _taste_pool ADD PRIMARY KEY (user_id, image_id)")

        # 5. per-user scalars (pool size, mean rating over visible images)
        await _exec(
            db,
            """
            CREATE TEMP TABLE _taste_users ON COMMIT DROP AS
            SELECT e.user_id,
                (SELECT COUNT(*) FROM _taste_pool p WHERE p.user_id = e.user_id) AS pool_size,
                (SELECT AVG(r.rating)::float FROM image_ratings r
                  JOIN _taste_vi vi ON vi.image_id = r.image_id
                  WHERE r.user_id = e.user_id) AS user_mean
            FROM _taste_elig e
            """,
        )
        await _exec(db, "ALTER TABLE _taste_users ADD PRIMARY KEY (user_id)")

        # 6. clear the live table inside the transaction: readers keep the old
        #    rows until commit, and the schema (PK, lookup index, defaults)
        #    stays exactly what the migration chain created.
        await _exec(db, "DELETE FROM user_tag_affinity")

        # 7. batched aggregation: contiguous user-id ranges over the sorted
        #    eligible ids, so BETWEEN lo AND hi covers exactly one chunk.
        user_ids = [
            r[0]
            for r in (
                await db.execute(text("SELECT user_id FROM _taste_elig ORDER BY user_id"))
            ).all()
        ]
        for start in range(0, len(user_ids), batch_size):
            chunk = user_ids[start : start + batch_size]
            await _exec(
                db,
                _BATCH_INSERT,
                {
                    "lo": chunk[0],
                    "hi": chunk[-1],
                    "k": smoothing_k,
                    "n": n,
                    "beta": beta,
                    "min_support": min_support,
                },
            )

        n_rows = (await db.execute(text("SELECT COUNT(*) FROM user_tag_affinity"))).scalar() or 0
        await db.commit()
        return n_rows
    finally:
        # A mid-run failure leaves the transaction aborted; rolling it back
        # releases the advisory lock and drops the temp tables so the same
        # session can run again. A healthy post-commit session tolerates
        # rollback() fine, so this is a no-op on the success path.
        try:
            await db.rollback()
        except Exception:
            pass
```

- [ ] **Step 4: Run the service tests**

Run: `./run-tests.sh --pg tests/services/test_user_tag_affinity.py tests/tasks/test_taste_profile_job.py -v`
Expected: all PASS. If a value test fails with lift or affinity of `0.0`, an integer division survived: find the ratio in `_BATCH_INSERT` without `::float`.

- [ ] **Step 5: Fix the two stale descriptions**

In `app/models/user_tag_affinity.py`, replace the docstring's second paragraph:

```python
    Rebuilt nightly by refresh_user_tag_affinity inside one transaction (delete
    then batched insert); treat as read-only outside the refresh job. No FKs by
    design: the full rebuild maintains consistency, and FK checks on the bulk
    insert would only slow it. Only rows meeting min support are stored:
    pool_cnt >= TASTE_MIN_SUPPORT or rated_count >= TASTE_MIN_SUPPORT.
```

In `tests/integration/test_fk_constraint_names.py`, replace the comment lines

```python
# user_tag_affinity stays FK-less BY DESIGN — its nightly staging-table swap
# (CREATE TABLE ... LIKE, app/services/user_tag_affinity.py) does not copy FKs,
# so one added here would silently vanish at the next rebuild.
```

with

```python
# user_tag_affinity stays FK-less BY DESIGN (see its model docstring): the
# nightly full rebuild keeps it consistent without per-row FK checks.
```

- [ ] **Step 6: Type-check, run the recommendation and user tests, commit**

Run: `uv run mypy app/ && ./run-tests.sh --pg tests/services/ tests/api/v1/test_users.py tests/api/v1/test_images.py -q`
Expected: mypy clean; all PASS.

```bash
git add app/services/user_tag_affinity.py app/models/user_tag_affinity.py tests/integration/test_fk_constraint_names.py
git commit -m "feat(affinity): port the nightly rebuild to Postgres

One transaction with temp helper tables, a transaction-scoped advisory
lock, and delete-then-insert instead of the rename swap. Ratios are cast
to float: integer division would have zeroed lift and affinity."
```

### Task 2.3: Full suite and PR

- [ ] **Step 1:** Run `./run-tests.sh --pg`. Expected: all pass.
- [ ] **Step 2:** Push and open the PR titled `feat(affinity): port the nightly rebuild to Postgres`, body describing the port and that the cron has failed nightly since 2026-08-22, with the attribution footer.
- [ ] **Step 3:** After merge, verify on the dev stack that a manual run completes: `docker compose exec arq-worker uv run --no-project python -c "import asyncio; from app.tasks.taste_profile import refresh_user_tag_affinity_job; asyncio.run(refresh_user_tag_affinity_job({}))"` and check the arq log for `user_tag_affinity_refreshed`.

---

# PR 3 — the repo runs only on Postgres

Branch: `chore/mariadb-retirement-infra`. Spec decisions 4–9. This PR also deletes the transition tooling (`scripts/pg_migration/`, `scripts/postgres_poc.py`, `scripts/compare_schemas.py`): the first depends on aiomysql, the second on the bootstrap shims PR 4 removes, and the third is a MariaDB-only diff tool. All three remain in git history.

### Task 3.1: One Alembic chain at `alembic/`

**Files:**
- Delete: `alembic/` (75 files), `alembic.pg.ini`
- Move: `alembic_pg/` → `alembic/`
- Modify: `alembic/env.py:1-7` (docstring), `.gitignore:58-59`, `tests/conftest.py:236-255`, `scripts/db_utils.py:275-290`, `tests/unit/test_db_utils.py:195-205`, `Makefile:198-216`, `docker-compose.yml:155-157,247-249`, `.github/workflows/ci.yml:209-210`, `scripts/gen_pg_baseline.py:10-21`
- Delete: `tests/integration/test_schema_sync.py` (MariaDB chain comparator)
- Move: `tests/integration/test_pg_schema_sync.py` → `tests/integration/test_schema_sync.py`

- [ ] **Step 1: Move the chain**

```bash
git rm -r -q alembic alembic.pg.ini
rm -rf alembic            # leftover __pycache__
git mv alembic_pg alembic
```

Run: `uv run alembic heads`
Expected: exactly one line, `e20bac5f3ac3 (head)`. (`[tool.alembic]` in pyproject already names `alembic`, so no config change.)

- [ ] **Step 2: Fix the references**

- `alembic/env.py`: replace the docstring's first three lines with `"""Alembic environment. Runs on the async asyncpg driver (this repo installs no sync Postgres driver), so migrations execute through run_sync. See ADR-0010 for the frozen baseline."""`.
- `.gitignore`: `!alembic_pg/versions/*.sql` → `!alembic/versions/*.sql`.
- `tests/conftest.py`: in `_setup_postgres_test_database`, `alembic_cfg.set_main_option("script_location", "alembic_pg")` → `"alembic"`; in its docstring, "Schema comes from the POSTGRES Alembic chain (alembic_pg/), mirroring the MariaDB path:" → "Schema comes from the Alembic chain:".
- `scripts/db_utils.py` and `tests/unit/test_db_utils.py`: delete the two list elements `"-c",` and `"alembic.pg.ini",` in each.
- `Makefile` `prod-migrate`: drop ` -c alembic.pg.ini` from the command and replace the comment block from "The -c alembic.pg.ini is" through "release of PR #370)." with "Migrations must be".
- `docker-compose.yml`: delete the `- ./alembic_pg:/app/alembic_pg` and `- ./alembic.pg.ini:/app/alembic.pg.ini:ro` lines under both `api` and `arq-worker`.
- `.github/workflows/ci.yml` lines 209-210: replace with `# --schema-sync runs the schema-sync test (models vs the alembic chain).`
- `scripts/gen_pg_baseline.py`: `"alembic_pg"` → `"alembic"` in `OUT`, and `alembic_pg/` → `alembic/` in the docstring (twice).

- [ ] **Step 3: Replace the MariaDB schema-sync test with the Postgres one**

```bash
git rm -q tests/integration/test_schema_sync.py
git mv tests/integration/test_pg_schema_sync.py tests/integration/test_schema_sync.py
```

In the moved file: replace the docstring with

```python
"""Models vs the migration chain (alembic/).

One database is built from the models (build_pg_schema — create_all, citext,
triggers) and one from `alembic upgrade head`; their catalogs must be
identical. A model change without a matching migration turns this red.

Run with: pytest tests/integration/test_schema_sync.py --schema-sync -v
"""
```

delete the `pytest.mark.postgres_only,` line from `pytestmark`, change the subprocess command to `["uv", "run", "alembic", "upgrade", "head"]`, the assertion message to `f"alembic upgrade failed:\n{result.stderr}"`, the test docstring to `"""create_all-from-models and the chain must produce identical schemas."""`, and rename the test to `test_models_match_migration_chain`.

- [ ] **Step 4: Verify**

Run: `./run-tests.sh --pg tests/integration/test_schema_sync.py --schema-sync -v && uv run pytest tests/unit/test_db_utils.py -q && grep -rn 'alembic_pg\|alembic\.pg\.ini' --include='*.py' --include='*.yml' --include='*.toml' --include='Makefile' --include='.gitignore' . | grep -v '^./docs/\|^./.venv/\|^./.worktrees/'`
Expected: both test runs PASS; the grep prints only `scripts/pg_migration/migrate.py:60`, which Task 3.5 deletes.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore(alembic): one chain at alembic/

Deletes the MariaDB chain and alembic.pg.ini and moves the Postgres
chain into place. Revision ids are unchanged, so prod needs nothing;
a bare 'alembic upgrade head' now targets the right chain."
```

### Task 3.2: conftest and markers

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/integration/test_fk_constraint_names.py`, `tests/integration/test_counter_cascades.py:21`, `tests/integration/test_pg_trigger_state.py:21`, `tests/api/v1/test_images.py` (two `mariadb_only` tests near lines 3256 and 3471), `tests/api/v1/test_tags.py` (one near line 1184)

- [ ] **Step 1: Delete the MariaDB test bootstrap from `tests/conftest.py`**

- Delete the function `_setup_mariadb_test_database` (line 317 through the line before `@pytest.fixture(scope="function")` / `async def engine()` at line 469).
- Delete `DEFAULT_ROOT_PASSWORD = "root_password"`. Change `DEFAULT_TEST_DB_PORT = "3306"` to `"5432"`.
- In `_get_test_database_url`: build `test_url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"`; delete everything about `test_url_sync` (the backend check, the `TEST_DATABASE_URL_SYNC` derivation, the `_with_worker_suffix` call on it) and return only the async URL. Change the module-level line to `TEST_DATABASE_URL = _get_test_database_url()` and delete `IS_POSTGRES`.
- Delete the two `config.addinivalue_line(...)` calls for `mariadb_only` and `postgres_only`, and in `pytest_collection_modifyitems` delete the whole `if IS_POSTGRES: ... else: ...` block, keeping the `--schema-sync` skip.
- In `setup_test_database`: replace the `if IS_POSTGRES: ... else: ...` dispatch with a bare `_setup_postgres_test_database()` and rewrite the docstring's second paragraph to "Runs once per test session (autouse=True; per worker under xdist): rebuilds the per-worker Postgres database from the migration chain, then runs the perms sync that mirrors application startup."
- In `_truncate_all_tables`: keep only the Postgres branch (drop the `if IS_POSTGRES:` line and dedent; delete the `else:` branch). Replace its comment's "RESTART IDENTITY matches MariaDB TRUNCATE's auto-increment reset. alembic_version survives, like the MariaDB branch below." with "alembic_version survives."
- In `_create_test_users`: drop the `if IS_POSTGRES:` guard so the `setval` runs unconditionally; trim the comment to "Explicit-PK inserts don't advance Postgres sequences, so without this the next id-less user insert gets nextval=1 and collides. setval survives the test's rollback — harmless, the sequence only ever moves forward."
- Delete the now-unused imports `create_engine` (from `sqlalchemy`) and `OperationalError` (from `sqlalchemy.exc`), and the `needs_commit` marker text's "(e.g., FULLTEXT search tests)" clause.

Run: `grep -n 'IS_POSTGRES\|TEST_DATABASE_URL_SYNC\|DEFAULT_ROOT_PASSWORD\|create_engine\|OperationalError\|mariadb\|mysql\|MariaDB\|MySQL' tests/conftest.py`
Expected: no output.

- [ ] **Step 2: Drop the `postgres_only` markers**

- `tests/integration/test_counter_cascades.py` and `tests/integration/test_pg_trigger_state.py`: `pytestmark = [pytest.mark.integration, pytest.mark.postgres_only]` → `pytestmark = [pytest.mark.integration]`.
- `tests/integration/test_fk_constraint_names.py`: delete the whole first test `test_all_fks_use_fk_prefix_convention` together with its two decorators and the comment lines attached to the marker; delete the imports `create_engine, inspect` (keep `text`) and `from tests.conftest import TEST_DATABASE_URL_SYNC`; delete the two remaining `@pytest.mark.postgres_only` decorators with their trailing comment lines; replace the module docstring with `"""Verify FK constraints in the migrated schema: one constraint per column set, and the agreed delete rules on the user-reference columns."""`; in the duplicate-FK failure message change "alembic_pg migration" to "alembic migration".

- [ ] **Step 3: Delete the `mariadb_only` tests**

In `tests/api/v1/test_images.py`, delete the two test methods decorated `@pytest.mark.mariadb_only` (near lines 3256 and 3471; they assert MySQL boolean-mode and natural-language-mode semantics). In `tests/api/v1/test_tags.py`, delete the one near line 1184 (InnoDB stopword-list behavior). Delete each whole method from its decorator through its last assertion.

Run: `grep -rn 'mariadb_only\|postgres_only' tests/`
Expected: no output.

- [ ] **Step 4: Verify**

Run: `./run-tests.sh --pg tests/integration/ tests/api/v1/test_images.py tests/api/v1/test_tags.py tests/unit -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A tests/
git commit -m "test: conftest bootstraps Postgres only; backend markers removed"
```

### Task 3.3: Dependencies, settings, Dockerfile

**Files:**
- Modify: `pyproject.toml:15-18,200-205`, `Dockerfile:12-20`, `app/config.py:1-3,17,46-49`, `scripts/bench_feed_count.py`, `tests/unit/test_email_retry.py:255-315`, `uv.lock` (regenerated)

- [ ] **Step 1: Drop the drivers**

In `pyproject.toml`: change the comment `# Database (MySQL)` to `# Database (Postgres)`; delete the `"aiomysql>=0.2.0",` and `"pymysql>=1.1.0,<1.2", # <1.2: ...` lines; delete `"aiomysql.*",` from the `[[tool.mypy.overrides]]` module list. Confirm `asyncpg` is still listed as a dependency (it is what the app runs on).

Run: `uv lock && uv sync --all-groups && uv run python -c "import aiomysql" 2>&1 | tail -1`
Expected: the lock regenerates; the import fails with `ModuleNotFoundError: No module named 'aiomysql'`.

- [ ] **Step 2: Dockerfile**

Delete the three lines

```dockerfile
# aiomysql calls getpass.getuser() at import time; Python 3.13+ raises OSError
# when running as a numeric UID without a /etc/passwd entry
ENV USER=app
```

and the `    default-libmysqlclient-dev \` line from the apt-get block. Change the comment "(e.g. pymysql 1.1.x → 1.2.0)" to "(e.g. a driver minor bump)".

Run: `docker compose build api`
Expected: builds successfully.

- [ ] **Step 3: Settings**

In `app/config.py`: line 2 `Application Configuration - MariaDB Version` → `Application Configuration`; line 17's comment `# Ignore extra env vars like MARIADB_* used by docker-compose` → `# Ignore extra env vars like POSTGRES_* used by docker-compose`; replace

```python
    # MariaDB Database - UPDATED!
    DATABASE_URL: str = "YOU MUST SET A VALID MARIADB DATABASE URL"
```

(and whatever sits between it and `DATABASE_URL_SYNC`) with

```python
    # Database (Postgres, asyncpg)
    DATABASE_URL: str = "YOU MUST SET A VALID DATABASE URL"
```

and delete the `DATABASE_URL_SYNC` line.

In `tests/unit/test_email_retry.py`, in the four `Settings(...)` constructions, delete every `DATABASE_URL_SYNC=...` line and change `DATABASE_URL="mysql+aiomysql://user:pass@localhost/db"` to `DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db"`.

Run: `grep -rn 'DATABASE_URL_SYNC' app/ tests/ scripts/`
Expected: only `scripts/bench_feed_count.py` (fixed next).

- [ ] **Step 4: Rewrite `scripts/bench_feed_count.py` on the async engine**

```python
"""Benchmark: naive vs fast default-feed pagination COUNT.

Quantifies the list_images count optimization (hidden-complement instead of the
`count(visible OR mine)` full-table scan). Runs against the configured DB
(DATABASE_URL — point it at a production-like dataset).

    uv run python scripts/bench_feed_count.py
"""

import asyncio
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import settings

VISIBLE = "(-1, 1, 2)"  # PUBLIC_IMAGE_STATUSES: REPOST, ACTIVE, SPOILER


async def _time_ms(conn: AsyncConnection, sql: str, runs: int = 3) -> float:
    (await conn.execute(text(sql))).scalar()  # warm
    best = float("inf")
    for _ in range(runs):
        t = time.perf_counter()
        (await conn.execute(text(sql))).scalar()
        best = min(best, (time.perf_counter() - t) * 1000)
    return best


async def _explain_plan(conn: AsyncConnection, sql: str) -> str:
    """The top line of the plan (the outer node and its estimate)."""
    rows = (await conn.execute(text("EXPLAIN " + sql))).scalars().all()
    return str(rows[0]) if rows else "?"


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as c:
            uid = (
                await c.execute(
                    text(
                        "SELECT user_id FROM images GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 1"
                    )
                )
            ).scalar()
            total = (await c.execute(text("SELECT COUNT(*) FROM images"))).scalar()
            print(f"images = {total:,}   sample_user = {uid}\n")

            naive_all = f"SELECT COUNT(*) FROM images WHERE status IN {VISIBLE}"
            naive_mine = f"SELECT COUNT(*) FROM images WHERE status IN {VISIBLE} OR user_id = {uid}"
            all_count = "SELECT COUNT(*) FROM images"
            hidden = f"SELECT COUNT(*) FROM images WHERE status NOT IN {VISIBLE}"
            hidden_mine = (
                f"SELECT COUNT(*) FROM images WHERE status NOT IN {VISIBLE} AND user_id = {uid}"
            )

            print(f"  naive OR plan : {await _explain_plan(c, naive_mine)}")
            print(f"  hidden  plan  : {await _explain_plan(c, hidden)}\n")

            scenarios = {
                "anonymous": (naive_all, [all_count, hidden]),
                "logged-in show_all=0": (naive_mine, [all_count, hidden, hidden_mine]),
                "logged-in show_all=1": (all_count, [all_count]),
            }
            # "after" is the cache-MISS path (the raw DB cost of the hidden-complement
            # counts). In production these globals are TTL-cached (feed_count_cache), so a
            # warm hit is a couple of single-digit-ms Redis reads — faster still.
            print(f"{'scenario':24}  {'before':>10}  {'after':>10}  {'speedup':>8}")
            print("-" * 60)
            for name, (naive, fast_parts) in scenarios.items():
                before = await _time_ms(c, naive)
                after = 0.0
                for part in fast_parts:
                    after += await _time_ms(c, part)
                speedup = before / after if after else 0.0
                print(f"{name:24}  {before:8.1f}ms  {after:8.1f}ms  {speedup:6.1f}x")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

Run: `uv run mypy app/ scripts/bench_feed_count.py && uv run pytest tests/unit/test_email_retry.py -q`
Expected: mypy clean; tests PASS.

Then run the benchmark against the dev database (its queries are read-only COUNTs):

```bash
set -a; . .env; set +a
DATABASE_URL="postgresql+asyncpg://$POSTGRES_USER:$POSTGRES_PASSWORD@localhost:5432/$POSTGRES_DB" \
  uv run python scripts/bench_feed_count.py
```

Expected: a table of three scenarios with millisecond timings.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock Dockerfile app/config.py scripts/bench_feed_count.py tests/unit/test_email_retry.py
git commit -m "chore(deps): drop aiomysql and pymysql; remove DATABASE_URL_SYNC"
```

### Task 3.4: CI and the workflows README

**Files:**
- Modify: `.github/workflows/ci.yml:47-150,151-152`
- Modify: `.github/workflows/README.md`

- [ ] **Step 1: One test job**

Delete the entire `test:` job (from line 47 `  test:` through the `Upload test results` step that ends just before `  test-postgres:`). Then rename `  test-postgres:` to `  test:` and its `name: Tests (Postgres)` to `name: Tests`. Nothing else in the file references the old job id (the `security` job has no `needs`).

Run: `grep -nE '^  [a-z-]+:$' .github/workflows/ci.yml`
Expected: exactly three job ids, `lint:`, `test:`, and `security:`. The pre-commit `check yaml` hook validates the syntax at commit time.

- [ ] **Step 2: README**

In `.github/workflows/README.md`:
- Jobs item 2 becomes: `2. **Tests** - Full test suite against Postgres` with sub-bullets `- Postgres 18 service container (tmpfs data dir)`, `- pytest with xdist, one database per worker`, `- Schema-sync check (models vs the alembic chain)`.
- Replace the `env:` block under "Required Environment Variables" with the PG job's actual env (SECRET_KEY, DATABASE_URL and TEST_DATABASE_URL as `postgresql+asyncpg://shuushuu:shuushuu_ci_password@127.0.0.1:5432/shuushuu_test`, ENVIRONMENT, DEBUG, REDIS_URL).
- Replace the "Database Service" section's YAML with the `postgres:` service block from the job, and its sentence with "Tests run against a real Postgres database using GitHub Actions service containers:".
- Matrix Strategy section: replace the YAML with a one-line note "The test job runs on Python 3.14 only; there is no matrix." and delete the YAML block.
- Under "Test CI workflow locally with act": delete the "Use custom Docker image (for MySQL support)" comment and command.
- Troubleshooting: the "Field required" list loses `DATABASE_URL_SYNC`; the "database connection errors" list becomes "1. Check the Postgres service health in workflow logs 2. Verify `TEST_DATABASE_URL` is set correctly 3. Ensure the health check passes before tests run".
- Branch Protection Rules: status checks become `Lint & Format Check`, `Tests`, `Security Scan`.

Run: `grep -niE 'mysql|mariadb' .github/`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add .github/
git commit -m "ci: drop the MariaDB job; the Postgres job is Tests"
```

### Task 3.5: Compose, env example, Makefile, run-tests, transition scripts

**Files:**
- Modify: `docker-compose.yml`, `docker-compose.override.yml`, `docker-compose.test.yml`, `docker-compose.prod.yml`, `docker/alloy/config.alloy`, `.env.example`, `Makefile`, `run-tests.sh`
- Delete: `docker-compose.pytest.yml`, `scripts/pg_migration/`, `scripts/postgres_poc.py`, `scripts/compare_schemas.py`

- [ ] **Step 1: `docker-compose.yml`**

- Line 1: `# Docker Compose - MariaDB + Redis Version` → `# Docker Compose - Postgres + Redis`.
- Delete the whole `mariadb:` service (from `  # MariaDB Database` through the end of its `deploy:` block, the line before `  # Postgres — the transition target`).
- Replace the postgres service's three comment lines starting `# Postgres — the transition target` with `# Postgres (ADR-0010). In prod the DB tier is native/out-of-stack (docker-compose.prod.yml stubs it), so this service is dev-only in practice.` Replace `# Pin like mariadb above: bump POSTGRES_IMAGE` with `# Pin the image: bump POSTGRES_IMAGE`, `# Mirrors the mariadb tuning rationale: keep` with `# Keep`, and `# Published like mariadb's 3306: dev is reached` with `# Published: dev is reached`.
- Under both `api` and `arq-worker`, replace the four `DATABASE_URL` lines (three comment lines plus the value) with:

```yaml
      # Container-side hostname: .env's DATABASE_URL is written for the host.
      - DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER:-shuushuu}:${POSTGRES_PASSWORD:-pg_dev_password}@postgres:5432/${POSTGRES_DB:-shuushuu}
```

- Under both `api` and `arq-worker` `depends_on`, replace `mariadb:` with `postgres:` (keeping `condition: service_healthy`).
- Adminer: `ADMINER_DEFAULT_SERVER=mariadb` → `ADMINER_DEFAULT_SERVER=postgres`; its `depends_on: - mariadb` → `- postgres`.
- Delete the `mariadb_data:` / `driver: local` entry from `volumes:`.

- [ ] **Step 2: The overlays**

- `docker-compose.override.yml`: delete the `mariadb:` block (two lines) and the `mariadb_data:` block (three lines) under `volumes:`; delete the `depends_on:` block under `api:` (its four lines beginning with the "Dev runs on Postgres" comment), since the base file now waits for postgres.
- `docker-compose.test.yml`: delete the `mariadb:` block (two lines) and the `mariadb_data:` block under `volumes:`. Leave the `postgres` volume naming alone: the test host's data lives in the generic `postgres_data` volume today, and renaming it would orphan that data.
- `docker-compose.prod.yml`: delete the `mariadb:` stub (from `  mariadb:` through its `retries: 1`, including the four comment lines above it that begin `# Disable local services`... keep those four comment lines, they describe the pattern); in the postgres stub's comment replace `# Same treatment for the transition-target Postgres: prod's Postgres (like\n  # MariaDB) lives natively` with `# Prod's Postgres lives natively`; in the redis stub's comments replace `Stub the service the same way\n  # mariadb is stubbed so` with `Stub the service so` and `# Same as mariadb — Redis is native` with `# Redis is native`; the two `# List form replaces base depends_on entirely (removes mariadb dependency)` comments → `(removes postgres dependency)`; delete the `mariadb_data:` block under `volumes:`.
- `docker/alloy/config.alloy`: `regex = "mariadb|redis"` → `regex = "postgres|redis"`; the two comments mentioning `mariadb/redis busybox stubs` → `postgres/redis busybox stubs`.

Run: `docker compose config -q && docker compose -f docker-compose.yml -f docker-compose.test.yml config -q && docker compose -f docker-compose.yml -f docker-compose.prod.yml config -q && grep -rniE 'mariadb|mysql' docker-compose*.yml docker/`
Expected: three silent successes (prod may need `check-env-prod` variables; if it complains about missing env, run it with `--env-file .env.example`); the grep prints nothing.

- [ ] **Step 3: `.env.example`**

Three edits in the database section:

1. Delete lines 16-24, the `# Database (MariaDB)` block through `MARIADB_PASSWORD=...`.
2. Replace the two comment lines above `POSTGRES_IMAGE` (`# Used by docker-compose for the Postgres container (the transition target,` and `# ADR-0010 / docs/postgres-cutover-runbook.md). Pin the image deliberately.`) with:

```bash
# Database (Postgres)
# Used by docker-compose for the Postgres container. Pin the image deliberately;
# prod's native DB tier runs Postgres 18.
```

3. Replace the block from `# Database URLs (used by application)` through the commented `# COMPOSE_DATABASE_URL=...` line with:

```bash
# Database URL (used by the application when run on the host; the compose
# api/arq containers build their own from the POSTGRES_* values above)
DATABASE_URL=postgresql+asyncpg://shuushuu:change_this_password@localhost:5432/shuushuu
```

Delete the `PYTEST_DB_PORT` comment block. Replace the `# Test Database` block with:

```bash
# Test Database (run-tests.sh sets this itself; set it only to point pytest
# at a different Postgres server)
# TEST_DATABASE_URL=postgresql+asyncpg://shuushuu:change_this_password@localhost:5432/shuushuu_pytest
```

- [ ] **Step 4: Makefile and run-tests.sh**

Makefile: in the help text replace the "Python test suite (isolated DB on :3316)" block with

```make
	@echo "Python test suite (dev-stack Postgres, one DB per xdist worker):"
	@echo "  pytest       Run the pytest suite (workers: PYTEST_WORKERS in .env, default auto)"
```

Delete the `PYTEST_DB_PORT` block, `COMPOSE_PYTEST`, the `PYTEST_DB_ENV` block, and the `pytest-db-up` / `pytest-db-down` targets. Replace the `pytest` target and its comment with:

```make
# Python test suite against the dev-stack Postgres (docker compose up -d
# postgres). run-tests.sh loads .env and pins DATABASE_URL and
# TEST_DATABASE_URL at the shuushuu_pytest database, so nothing reaching the
# app-level engine can touch the dev database; each xdist worker gets its own
# shuushuu_pytest_<worker> database. Keep PYTEST_WORKERS below the core count
# on a host that also serves the dev stack.
pytest:
	./run-tests.sh -n $(PYTEST_WORKERS) --dist loadgroup $(ARGS)
```

Replace `run-tests.sh` entirely with:

```bash
#!/bin/bash
# Test runner script for shuushuu-api
# Usage: ./run-tests.sh [pytest args]
# With no args, runs the full suite in parallel (-n 4 --dist loadgroup).
# Pass any args (e.g. a test path) for a plain serial pytest run.
# Runs against the dev-stack Postgres container (docker compose up -d postgres
# first); each xdist worker gets its own shuushuu_pytest_<worker> database.

set -e

# Load environment variables from .env file if it exists
# This ensures test credentials stay in sync with actual database credentials
if [ -f .env ]; then
    echo "Loading database credentials from .env..."
    # Safely load variables from .env using Bash's own parser
    set -a
    . .env
    set +a
fi

# Credentials come from .env, falling back to the compose dev defaults. The
# app-level engine is pointed at the test DB too (mirrors CI) so nothing that
# reaches AsyncSessionLocal outside the get_db override can touch the dev
# database during a run.
PG_TEST_URL="postgresql+asyncpg://${POSTGRES_USER:-shuushuu}:${POSTGRES_PASSWORD:-pg_dev_password}@localhost:5432/shuushuu_pytest"
export TEST_DATABASE_URL="${TEST_DATABASE_URL:-$PG_TEST_URL}"
export DATABASE_URL="$TEST_DATABASE_URL"
echo "Running against Postgres ($TEST_DATABASE_URL)"

# Run pytest with all arguments passed through; default to the parallel
# sweet spot (see tests/README.md) when none are given
if [ $# -eq 0 ]; then
    uv run pytest -n 4 --dist loadgroup
else
    uv run pytest "$@"
fi
```

- [ ] **Step 5: Delete the pytest compose file and the transition scripts**

```bash
git rm -q docker-compose.pytest.yml scripts/postgres_poc.py scripts/compare_schemas.py
git rm -r -q scripts/pg_migration
```

Run: `grep -rn 'postgres_poc\|pg_migration\|compare_schemas\|docker-compose.pytest\|pytest-db-up\|PYTEST_DB_PORT\|COMPOSE_DATABASE_URL' --include='*.py' --include='*.yml' --include='*.sh' --include='Makefile' --include='*.example' . | grep -v '^./docs/\|^./.venv/\|^./.worktrees/'`
Expected: at most `app/core/pg_schema.py` (its docstring names `scripts/postgres_poc.py`; PR 4 rewrites that docstring) and `tests/integration/test_pg_trigger_state.py` (historical rationale in a docstring; leave it).

- [ ] **Step 6: Verify the local test path end to end**

Run: `./run-tests.sh tests/unit -q && make pytest ARGS="tests/unit -q"`
Expected: both PASS against the dev-stack Postgres, printing `Running against Postgres (...shuushuu_pytest)`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore(compose): Postgres only; local tests use the dev-stack container

Removes the mariadb service from every compose file and the prod stub,
the dedicated pytest MariaDB, COMPOSE_DATABASE_URL, and the transition
tooling under scripts/. run-tests.sh and make pytest both target the
dev-stack Postgres."
```

### Task 3.6: Full suite, PR, ruleset gate

- [ ] **Step 1:** `./run-tests.sh` (parallel) and `uv run mypy app/`. Expected: all pass, mypy clean.
- [ ] **Step 2:** `uv run alembic heads` shows one head. Push and open the PR titled `chore: retire MariaDB from CI, compose, tests and dependencies`, body summarizing Tasks 3.1–3.5 and naming the ruleset gate, with the attribution footer.
- [ ] **Step 3: Wait for CI.** The PR's checks must show `Tests` green.
- [ ] **Step 4: Repoint the ruleset (manual, user-run).** The main ruleset requires the check `Tests (Python 3.14)`, which no longer exists after this PR. Right before merging:

```bash
gh api repos/anonymousobject/shuushuu-api/rulesets/10325783 > /tmp/ruleset.json
jq '{name, target, enforcement, conditions, rules: (.rules | map(
      if .type == "required_status_checks"
      then .parameters.required_status_checks = [{"context": "Tests", "integration_id": 15368}]
      else . end))}' /tmp/ruleset.json > /tmp/ruleset-new.json
gh api -X PUT repos/anonymousobject/shuushuu-api/rulesets/10325783 --input /tmp/ruleset-new.json \
  --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks'
```

Expected: the last command prints `[{"context":"Tests","integration_id":15368}]`. Other open PRs now need a rebase onto `main` before they can merge (their runs carry the old check name).

- [ ] **Step 5:** Merge. Then on the dev host: `docker compose up -d --remove-orphans` (the orphaned mariadb container is stopped and removed; the `mariadb_data_dev` volume is untouched until you `docker volume rm` it).

---

# PR 4 — delete the MariaDB arm from application code

Branch: `chore/mariadb-retirement-code`. Spec decisions 1, 10, 11.

### Task 4.1: Core and services

**Files:**
- Modify: `app/core/database.py:47-54,113-137`, `app/core/permission_sync.py:13,41-42`, `app/services/ml_raw_store.py:15-19,192-199`, `app/services/repost.py:11,21-34,71,114,159`, `app/services/tag_type_flags.py:12-62,80`, `scripts/backfill_tag_type_flags.py:17-60`

- [ ] **Step 1: `app/core/database.py`**

Delete the `is_postgres` function. In `statement_timeout`, replace the `if is_postgres(db): ... else: ...` block with the Postgres arm only:

```python
    # int() coerces the value: SET does not take bind parameters, so this is
    # interpolated, and the coercion is what keeps that safe. Postgres takes
    # milliseconds; DEFAULT restores the session's configured value.
    await db.execute(sql_text(f"SET statement_timeout = {int(seconds * 1000)}"))
    try:
        yield
    finally:
        await db.execute(sql_text("SET statement_timeout = DEFAULT"))
```

In its docstring, replace the sentence pair "MariaDB's `max_statement_time` is per *statement*, so a request issuing several still has a total ceiling of the limit times the statement count. Postgres's `statement_timeout` behaves the same way (but is set in milliseconds)." with "`statement_timeout` is per *statement*, so a request issuing several still has a total ceiling of the limit times the statement count." Trim the `pool_recycle` comment to `# Recycle connections every hour`.

- [ ] **Step 2: `app/core/permission_sync.py`**

Delete `from app.core.database import is_postgres`. Replace

```python
    if is_postgres(db):
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SYNC_LOCK_KEY})
```

with the unconditional `await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SYNC_LOCK_KEY})`.

- [ ] **Step 3: `app/services/ml_raw_store.py`**

Delete `from sqlalchemy.dialects.mysql import insert as mysql_insert` and `from app.core.database import is_postgres`. Replace the batch loop body's branch with `stmt = pg_insert(MlRawPredictions).values(batch).on_conflict_do_nothing()` and the comment `# --- 4. Bulk INSERT IGNORE in batches ---` with `# --- 4. Bulk INSERT ... ON CONFLICT DO NOTHING in batches ---`. If `Any` is now unused, drop it from the typing import.

- [ ] **Step 4: `app/services/repost.py`**

Delete `from app.core.database import is_postgres`. Replace `_copy_to_original_sql` with:

```python
def _copy_to_original_sql(table: str, insert_cols: str, select_cols: str) -> TextClause:
    """INSERT-or-skip-duplicates, copying `table` rows from the repost to the original."""
    return text(
        f"INSERT INTO {table} ({insert_cols}) "
        f"SELECT {select_cols} FROM {table} WHERE image_id = :repost_id "
        "ON CONFLICT DO NOTHING"
    )
```

At the three call sites (lines 71, 114, 159) remove the leading `db,` argument.

- [ ] **Step 5: `app/services/tag_type_flags.py` and the backfill script**

In the service: delete `from app.core.database import is_postgres`, delete `_RECOMPUTE_SQL` (the MariaDB multi-table UPDATE) and its comment, rename `_RECOMPUTE_SQL_PG` to `_RECOMPUTE_SQL`, replace its comment with `# Single set-based recompute over a set of image_ids: UPDATE ... FROM with bool_or. The subquery LEFT JOINs from images so every requested id gets an agg row (all-NULL flags when untagged), and COALESCE resets those to false.`, and change the call to `await db.execute(_RECOMPUTE_SQL, {"ids": ids})`.

In `scripts/backfill_tag_type_flags.py`: delete the `is_postgres` import, delete `_BATCH_SQL` (the MariaDB one), rename `_BATCH_SQL_PG` to `_BATCH_SQL`, replace its comment with `# UPDATE ... FROM + bool_or, same shape as app/services/tag_type_flags.py.`, and make `backfill_range` execute `_BATCH_SQL` unconditionally.

- [ ] **Step 6: Verify**

Run: `grep -rn 'is_postgres' app/ scripts/ | grep -v 'app/api/'; uv run mypy app/ scripts/backfill_tag_type_flags.py && ./run-tests.sh tests/services/ tests/api/v1/test_image_reposts_endpoint.py tests/api/v1/test_ml_tag_suggestions.py tests/integration/ -q`
Expected: the grep prints nothing; mypy clean; all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/core/database.py app/core/permission_sync.py app/services/ scripts/backfill_tag_type_flags.py
git commit -m "refactor: drop the MariaDB arm from core and services"
```

### Task 4.2: Routers

**Files:**
- Modify: `app/api/v1/users.py:150-159`, `app/api/v1/images.py:51,538-552,822-827,1972-1978`, `app/api/v1/comments.py:17,79-95,130-135`, `app/api/v1/tags.py:155-245,676-762,824-850`

- [ ] **Step 1: `users.py`**

Remove `is_postgres` from the `app.core.database` import. Replace the block

```python
    if is_postgres(db) and sorting.sort_by in ("last_login", "last_active"):
        # Nullable sort columns: MariaDB places NULLs first on ASC / last on
        # DESC and that ordering is the API contract; Postgres defaults to the
        # opposite. MariaDB has no NULLS FIRST/LAST syntax, so this is
        # Postgres-only by construction.
```

with

```python
    if sorting.sort_by in ("last_login", "last_active"):
        # Nullable sort columns: NULLs first on ASC / last on DESC is the API
        # contract (inherited from the legacy site); Postgres defaults to the
        # opposite, so say so explicitly.
```

- [ ] **Step 2: `images.py`**

Remove `is_postgres` from the import. In `list_images`, change the `apply_comment_text_search(...)` call to `apply_comment_text_search(query, commentsearch, commentsearch_mode)`. In the repost listing, replace

```python
    # Nullable sort column: MariaDB places NULLs last on a DESC sort, Postgres
    # places them first. A legacy repost with no status_updated belongs at the
    # bottom on both. MariaDB has no NULLS LAST syntax, so this is Postgres-only
    # by construction (same pattern as the user list in app/api/v1/users.py).
    status_updated_desc: Any = desc(Images.status_updated)  # type: ignore[arg-type]
    if is_postgres(db):
        status_updated_desc = status_updated_desc.nullslast()
```

with

```python
    # Nullable sort column: Postgres puts NULLs first on DESC, but a legacy
    # repost with no status_updated belongs at the bottom (same pattern as the
    # user list in app/api/v1/users.py).
    status_updated_desc: Any = desc(Images.status_updated).nullslast()  # type: ignore[arg-type]
```

Replace the "Comment Search Modes" and "Boolean Mode Examples" docstring blocks with:

```
    **Comment Search Modes:**
    - `all_words` (default): every term must appear, as a case-insensitive
      substring match. Supports `"exact phrase"` and `-excluded`. A blank or
      whitespace-only `commentsearch` applies no filter at all; a non-blank
      value with nothing searchable in it (e.g. `!!!`) matches zero comments.
    - `natural`, `boolean`: accepted for compatibility and behave as
      `all_words`; operators such as `+` and `*` are ignored.
    - `like`: the whole string as one substring match. `%` and `_` in the query
      are escaped to literals, not treated as wildcards.

    **Search Examples:**
    - `happy -terrible`: must contain "happy", must not contain "terrible"
    - `"exact phrase"`: the words in that order
```

- [ ] **Step 3: `comments.py`**

Same import removal; `apply_comment_text_search(query, search_text, search_mode)` (keep the `# type: ignore[arg-type]` on the `search_text` argument line); the "Supports" bullet `Multiple search modes (all_words, natural fulltext, boolean fulltext, LIKE)` → `Search modes (all_words, like; natural and boolean accepted as all_words)`. Replace the "Search Modes" and "Boolean Mode Examples" blocks with:

```
    **Search Modes:**
    - `all_words` (default): every term must appear, as a case-insensitive
      substring match. Supports `"exact phrase"` and `-excluded`. A blank or
      whitespace-only `search_text` applies no filter at all; a non-blank
      value with nothing searchable in it (e.g. `!!!`) matches zero comments.
    - `natural`, `boolean`: accepted for compatibility and behave as
      `all_words`; operators such as `+` and `*` are ignored.
    - `like`: the whole string as one substring match. Example: `?search_text=awesome`.
      `%` and `_` in the query are escaped to literals, not treated as wildcards.

    **Search Examples:**
    - `happy -terrible`: must contain "happy", must not contain "terrible"
    - `"exact phrase"`: the words in that order
```

- [ ] **Step 4: `tags.py` — delete the FULLTEXT machinery**

Delete everything from the comment `# MySQL/MariaDB default fulltext stopwords that cause search failures` (line 155) through the end of `_has_valid_fulltext_tokens` (line 245): `FULLTEXT_STOPWORDS`, `FULLTEXT_MIN_TOKEN_SIZE`, `FULLTEXT_SPECIAL_CHARS`, `FULLTEXT_WORD_DELIMITERS`, `_DELIMITER_TRANS_TABLE`, `_sanitize_fulltext_term`, `_get_fulltext_tokens`, `_has_valid_fulltext_tokens`. Keep the `# TODO: Create tag proposal/review system` line that follows. Remove `is_postgres` from the import.

- [ ] **Step 5: `tags.py` — the search predicate**

Replace the block from `    # Track whether we're using fulltext search and what query string` (line 676) through the end of the `if search:` block (the line `query = query.where(Tags.title.like(f"{escaped_search}%"))  # type: ignore[union-attr]`, just before `    if type_id is not None:`) with:

```python
    if search:
        # Hybrid: queries under 3 chars are a prefix match (autocomplete);
        # longer ones AND a case-insensitive contains-match per word, which
        # keeps word order independent ("sakura kinomoto" finds "kinomoto
        # sakura") and matches partial words ("thig" finds "thighs").
        if len(search) < 3:
            # Escape LIKE special characters to prevent unintended wildcard matching
            escaped_search = escape_like_pattern(search)
            query = query.where(Tags.title.ilike(f"{escaped_search}%"))  # type: ignore[union-attr]
        else:
            for word in search.split():
                query = query.where(
                    Tags.title.ilike(f"%{escape_like_pattern(word)}%")  # type: ignore[union-attr]
                )
```

- [ ] **Step 6: `tags.py` — relevance ordering**

Replace the `elif search:` branch of the sort logic (from `    elif search:` through `.params(search=fulltext_query_str)`) with:

```python
    elif search:
        # No explicit sort_by + search: relevance ranking — exact match first,
        # then prefix matches, then the rest; alphabetical within each group.
        query = query.order_by(
            case(
                (func.lower(Tags.title) == search.lower(), 0),  # Exact match (case-insensitive)
                (
                    func.lower(Tags.title).like(f"{search.lower()}%"),
                    1,
                ),  # Starts with (case-insensitive)
                else_=2,  # Contains (middle/end)
            ),
            func.lower(Tags.title),  # Alphabetical within each priority group
        )
```

- [ ] **Step 7: Verify**

Run: `uv run ruff check app/ && uv run mypy app/ && grep -rn 'is_postgres\|fulltext_query_str\|use_fulltext' app/`
Expected: ruff clean (it reports any import left unused, such as `text` in tags.py — delete what it names); mypy clean; grep prints nothing except `app/utils/comment_search.py` (next task).

Run: `./run-tests.sh tests/api/v1/test_tags.py tests/api/v1/test_images.py tests/api/v1/test_comments.py tests/api/v1/test_users.py -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add app/api/
git commit -m "refactor(api): drop the MariaDB arm from the routers

Deletes the FULLTEXT stopword list, tokenizer and operator sanitizer;
tag, image and comment search keep the ILIKE path prod has run since
cutover. The natural and boolean mode values stay accepted."
```

### Task 4.3: Comment search

**Files:**
- Modify: `app/utils/comment_search.py`
- Modify: `tests/unit/test_comment_search.py`

- [ ] **Step 1: Rewrite the tests first**

Replace the module docstring, imports, `TestParseCommentSearch`, `TestParseWithoutIndex`, and `TestAppliedPredicateDialect` with the following (keep `TestLikePattern`, `TestIsEmpty`, and `TestIsTooShortToIndex` as they are, except: in `TestIsEmpty` delete the `assert not CommentSearchQuery(boolean_query="+cat").is_empty` line, and in `TestIsTooShortToIndex`'s docstring replace "MariaDB has no ngram parser, so CJK can only ever be served by the LIKE fallback" with "CJK is only ever served by the substring match"):

```python
"""Unit tests for the comment-search query parser.

Every case here maps to a behaviour measured against the live corpus and
recorded in
<shuushuu-frontend-repo>/docs/plans/2026-Q3/2026-08-10-comment-search-and-semantics-impl.md.
"""

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import Comments
from app.utils.comment_search import (
    CommentSearchQuery,
    apply_comment_text_search,
    like_pattern,
    parse_comment_search,
)


class TestParseCommentSearch:
    def test_multiple_words_are_anded(self):
        parsed = parse_comment_search("happy birthday")
        assert parsed.like_terms == ["happy", "birthday"]
        assert parsed.not_like_terms == []

    def test_single_word(self):
        assert parse_comment_search("birthday").like_terms == ["birthday"]

    def test_short_token_is_kept(self):
        assert parse_comment_search("happy bd").like_terms == ["happy", "bd"]

    def test_non_ascii_is_kept_whole(self):
        assert parse_comment_search("かわいい").like_terms == ["かわいい"]

    def test_mixed_ascii_and_cjk(self):
        assert parse_comment_search("cute かわいい").like_terms == ["cute", "かわいい"]

    def test_quoted_phrase_is_one_term(self):
        parsed = parse_comment_search('"happy birthday"')
        assert parsed.like_terms == ["happy birthday"]

    def test_phrase_and_bare_word_combine(self):
        parsed = parse_comment_search('"happy birthday" yui')
        assert parsed.like_terms == ["happy birthday", "yui"]

    def test_negated_term_excludes(self):
        parsed = parse_comment_search("happy -sad")
        assert parsed.like_terms == ["happy"]
        assert parsed.not_like_terms == ["sad"]

    def test_only_negative_terms(self):
        parsed = parse_comment_search("-sad")
        assert parsed.like_terms == []
        assert parsed.not_like_terms == ["sad"]

    def test_negated_quoted_phrase(self):
        parsed = parse_comment_search('-"happy birthday"')
        assert parsed.not_like_terms == ["happy birthday"]

    def test_punctuation_splits_words(self):
        # `@` and `)` are not word characters; neither reaches a pattern.
        assert parse_comment_search("happy@birthday)").like_terms == ["happy", "birthday"]

    def test_hyphenated_word_splits_into_tokens(self):
        assert parse_comment_search("well-known").like_terms == ["well", "known"]

    def test_legacy_boolean_operators_are_dropped(self):
        # The `boolean` mode value is still accepted; its operators are not.
        parsed = parse_comment_search("+awesome -terrible word*")
        assert parsed.like_terms == ["awesome", "word"]
        assert parsed.not_like_terms == ["terrible"]

    def test_unbalanced_quote_does_not_crash(self):
        assert parse_comment_search('happy "birthday').like_terms == ["happy", "birthday"]

    def test_empty_input_is_empty(self):
        assert parse_comment_search("").is_empty
        assert parse_comment_search("   ").is_empty
        assert parse_comment_search("!!!").is_empty

    def test_case_is_preserved(self):
        # ILIKE handles case at query time; the parser leaves it alone.
        assert parse_comment_search("The Cat").like_terms == ["The", "Cat"]


class TestAppliedPredicates:
    """LIKE is case-sensitive on Postgres, so every predicate must be ILIKE
    (measured: 'birthday' matched 2790 comments case-insensitively but only
    1936 with plain LIKE)."""

    def test_default_mode_uses_ilike(self):
        query = apply_comment_text_search(select(Comments), "birthday", None)
        sql = str(query.compile(dialect=postgresql.dialect()))
        assert "ILIKE" in sql

    def test_like_mode_uses_ilike(self):
        query = apply_comment_text_search(select(Comments), "birthday", "like")
        sql = str(query.compile(dialect=postgresql.dialect()))
        assert "ILIKE" in sql

    def test_negated_term_uses_not_ilike(self):
        query = apply_comment_text_search(select(Comments), "-birthday cake", None)
        sql = str(query.compile(dialect=postgresql.dialect()))
        assert "NOT ILIKE" in sql

    def test_boolean_mode_behaves_as_all_words(self):
        a = apply_comment_text_search(select(Comments), "happy -sad", "boolean")
        b = apply_comment_text_search(select(Comments), "happy -sad", "all_words")
        assert str(a.compile(dialect=postgresql.dialect())) == str(
            b.compile(dialect=postgresql.dialect())
        )

    def test_nothing_searchable_matches_nothing(self):
        query = apply_comment_text_search(select(Comments), "!!!", None)
        sql = str(query.compile(dialect=postgresql.dialect()))
        assert "WHERE false" in sql
```

Run: `uv run pytest tests/unit/test_comment_search.py -q`
Expected: FAIL (the parser still routes indexable tokens to `boolean_query`, and `apply_comment_text_search` still requires `use_fulltext`).

- [ ] **Step 2: Rewrite `app/utils/comment_search.py`**

Replace the module docstring, delete `STOPWORDS`, `_is_indexable`, and the `boolean_query` field, and rewrite `parse_comment_search`, `reject_unindexable_comment_search`'s docstring, and `apply_comment_text_search`. The result:

```python
"""Translate a user's comment-search string into SQL predicates.

Every term is a case-insensitive substring match (ILIKE) and every term is
ANDed. `-term` excludes; a quoted phrase matches as one substring. The legacy
``boolean`` and ``natural`` mode values are still accepted (the frontend sends
them) and behave as the default: their operators are just punctuation here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Select, false

from app.models import Comments
from app.utils.like_escape import escape_like_pattern

# Below this many characters an ASCII-only search is refused (see
# CommentSearchQuery.is_too_short_to_index).
MIN_TOKEN_SIZE = 3

# Ceiling on a single comment-search statement, in seconds.
#
# A circuit breaker, not a performance policy. Comment search is an unindexed
# ILIKE scan. Measured warm on a 536k-comment corpus: ~0.5s for the count and
# ~0.8s for the page query, and — importantly — flat regardless of how many
# rows match, because the cost is the scan rather than the result set.
#
# 5s leaves roughly 6x headroom for a cold cache, concurrency and corpus
# growth. That margin is the point: if this ever fires on a real search it
# becomes a hard failure for comment search. It exists to turn a plan
# regression from minutes into an error, nothing more.
COMMENT_SEARCH_TIMEOUT_SECONDS = 5.0

# A quoted phrase (optionally negated), or a bare run of non-space characters.
_TERM_RE = re.compile(r'-?"[^"]*"|\S+')

# Word characters only: "well-known" is two terms, and stray punctuation such
# as `@` or `)` never reaches a pattern.
_WORD_RE = re.compile(r"\w+", re.UNICODE)


@dataclass
class CommentSearchQuery:
    """The predicates a parsed search string maps onto."""

    like_terms: list[str] = field(default_factory=list)
    not_like_terms: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.like_terms or self.not_like_terms)

    @property
    def is_too_short_to_index(self) -> bool:
        """Whether this query is short enough that refusing it costs the user nothing.

        The scan is a flat ~0.5s on the count and ~0.8s on the page query,
        independent of how many rows match, so a search of nothing but one- and
        two-character words buys a second of database time for a result nobody
        wants. Callers refuse these with a 400.

        Length-based and ASCII-only, deliberately: non-ASCII is always allowed
        (a two-character Japanese word is a real search), and long words are
        allowed whatever they are.
        """
        if self.is_empty:
            return False
        # Split back into words rather than measuring the stored entry: a quoted
        # phrase is kept as one joined string, so `"ab cd"` would otherwise
        # measure five characters and slip past the guard that refuses the very
        # same two words unquoted.
        words = [
            word
            for term in self.like_terms + self.not_like_terms
            for word in _WORD_RE.findall(term)
        ]
        return bool(words) and all(w.isascii() and len(w) < MIN_TOKEN_SIZE for w in words)


def like_pattern(term: str) -> str:
    """Build a contains-pattern, escaping LIKE metacharacters.

    Without this a search for `100%` degrades into "match anything". Backslash
    is escaped first so it cannot double-escape the wildcards added after it.
    """
    return f"%{escape_like_pattern(term)}%"


def parse_comment_search(raw: str) -> CommentSearchQuery:
    """Split a user's search string into positive and negated substring terms.

    A quoted phrase stays one term (punctuation and repeated spaces inside it
    are not significant: it is re-joined from its words); a bare term
    contributes one entry per word.
    """
    parsed = CommentSearchQuery()

    for match in _TERM_RE.finditer(raw or ""):
        term = match.group(0)
        negated = term.startswith("-")
        if negated:
            term = term[1:]

        target = parsed.not_like_terms if negated else parsed.like_terms
        if term.startswith('"') and term.endswith('"') and len(term) >= 2:
            phrase = " ".join(_WORD_RE.findall(term[1:-1]))
            if phrase:
                target.append(phrase)
            continue

        target.extend(_WORD_RE.findall(term))

    return parsed


def reject_unindexable_comment_search(raw: str, mode: str | None) -> None:
    """Raise 400 for a search of nothing but very short ASCII words.

    Only applies to the default `all_words` mode; the explicit legacy modes
    are left alone. See `CommentSearchQuery.is_too_short_to_index` for why the
    rule is length-based and never touches non-ASCII.
    """
    if (mode or "all_words") != "all_words":
        return
    if parse_comment_search(raw).is_too_short_to_index:
        raise HTTPException(
            status_code=400,
            detail=f"Comment search terms must be at least {MIN_TOKEN_SIZE} characters.",
        )


def apply_comment_text_search(query: Select[Any], raw: str, mode: str | None) -> Select[Any]:
    """Add comment-text predicates to `query`.

    `query` must already select from / join the Comments table. ``like`` mode
    matches the whole string as one substring; every other mode value goes
    through the parser (terms ANDed, `-term` excluded, quoted phrases kept
    together).

    Callers are expected to skip calling this at all for a blank/whitespace-only
    `raw` -- that means "not searching," not "search for nothing" (see the
    `commentsearch`/`search_text` guards in images.py/comments.py). A non-blank
    `raw` that still has nothing searchable (e.g. "!!!") is a query the user typed
    that can never match a comment, so it returns zero rows rather than silently
    falling back to "no filter."
    """

    def contains(pattern: str) -> Any:
        # Postgres LIKE is case-sensitive; comment search is not.
        return Comments.post_text.ilike(pattern, escape="\\")  # type: ignore[attr-defined]

    if (mode or "all_words") == "like":
        return query.where(contains(like_pattern(raw)))

    parsed = parse_comment_search(raw)
    if parsed.is_empty:
        return query.where(false())

    for term in parsed.like_terms:
        query = query.where(contains(like_pattern(term)))
    for term in parsed.not_like_terms:
        # post_text is NOT NULL (verified), so NOT LIKE needs no NULL guard.
        query = query.where(~contains(like_pattern(term)))
    return query
```

- [ ] **Step 3: Verify**

Run: `uv run pytest tests/unit/test_comment_search.py -v && uv run ruff check app/ && uv run mypy app/ && ./run-tests.sh tests/api/v1/test_comments.py tests/api/v1/test_images.py -q`
Expected: all PASS; ruff and mypy clean.

- [ ] **Step 4: Commit**

```bash
git add app/utils/comment_search.py tests/unit/test_comment_search.py
git commit -m "refactor(comment_search): substring predicates only

Drops the InnoDB boolean-query half of the parser; every term is an
ILIKE contains-match, as it has been on Postgres since cutover."
```

### Task 4.4: Model types, CHECK constraints, index names, bootstrap shims

**Files:**
- Modify: `app/models/types.py`
- Modify: `app/models/admin_action.py`, `image_report.py`, `image_report_tag_suggestion.py`, `image_review.py`, `image_status_history.py`, `ml_raw_prediction.py`, `review_vote.py`, `user_tag_affinity.py` (unsigned types)
- Modify: `app/models/user.py`, `app/models/tag.py`, `app/models/tag_external_link.py` (CITEXT + CHECKs), `app/models/comment.py:110`, `app/models/misc.py:244`, `app/models/image_report_tag_suggestion.py:40`, `app/models/tag_external_link.py:62` (index names)
- Modify: `app/core/pg_schema.py`, `app/services/artist_identity_backfill.py:53`
- Delete: `scripts/gen_pg_baseline.py`, `app/models/generated.py.backup`

- [ ] **Step 1: `app/models/types.py`**

Delete `UnsignedInt`, `UnsignedSmallInt`, `ci_string`, and the imports `Integer, SmallInteger, String`, `INTEGER, SMALLINT` (mysql dialect), `CITEXT`, and `TypeEngine`. Replace the module docstring with:

```python
"""SQLAlchemy column types for the shuushuu-api models.

UtcDateTime: a DateTime variant that round-trips tz-aware datetimes through a
tz-naive TIMESTAMP column. Stores values as UTC (tzinfo stripped); attaches
tzinfo=UTC on read. Naive datetimes are rejected on bind to avoid ambiguous
"is this UTC or local?" assumptions.
"""
```

and in `UtcDateTime`'s docstring change "MariaDB's DATETIME stores no tz info." to "The column stores no tz info."

- [ ] **Step 2: Unsigned types become plain integers**

```bash
sed -i 's/\bUnsignedSmallInt\b/SmallInteger/g; s/\bUnsignedInt\b/Integer/g' \
  app/models/admin_action.py app/models/image_report.py app/models/image_report_tag_suggestion.py \
  app/models/image_review.py app/models/image_status_history.py app/models/ml_raw_prediction.py \
  app/models/review_vote.py app/models/user_tag_affinity.py
```

Then fix each file's imports by hand: the `from app.models.types import ...` line loses `Integer`/`SmallInteger` (which the sed put there) and keeps `UtcDateTime` (drop the line entirely in `ml_raw_prediction.py`, which imported only the two unsigned types); `Integer` is added to the `from sqlalchemy import ...` line in `admin_action.py` and `image_status_history.py` (the other six already import it, and `ml_raw_prediction.py` already imports `SmallInteger`). Delete the two "INT UNSIGNED to match the migration" / "SMALLINT UNSIGNED to match the migration" comments in `ml_raw_prediction.py`.

- [ ] **Step 3: CITEXT and the length CHECKs**

In `app/models/user.py`, `tag.py`, `tag_external_link.py`: replace `from app.models.types import UtcDateTime, ci_string` with `from app.models.types import UtcDateTime` plus `from sqlalchemy.dialects.postgresql import CITEXT`; add `CheckConstraint` to each file's `from sqlalchemy import ...`; replace every `sa_type=ci_string(N)` with `sa_type=CITEXT`; change the `# ci_string:` comments to `# CITEXT (ADR-0008):`. Then append to each `__table_args__` tuple:

- `user.py`: `CheckConstraint("char_length(username) <= 30", name="ck_users_username_len"),` and `CheckConstraint("char_length(email) <= 120", name="ck_users_email_len"),`
- `tag.py`: `CheckConstraint("char_length(title) <= 255", name="ck_tags_title_len"),`
- `tag_external_link.py`: `CheckConstraint("char_length(site) <= 32", name="ck_tag_external_links_site_len"),` and `CheckConstraint("char_length(external_id) <= 128", name="ck_tag_external_links_external_id_len"),`

In `app/services/artist_identity_backfill.py:53` change "are ci_string columns (ADR-0008)" to "are citext columns (ADR-0008)".

- [ ] **Step 4: Index names match the baseline**

- `app/models/comment.py:110`: `Index("idx_date", "date")` → `Index("posts_idx_date", "date")`
- `app/models/misc.py:244`: `Index("idx_date", "date")` → `Index("donations_idx_date", "date")`
- `app/models/image_report_tag_suggestion.py:40`: `Index("idx_tag_id", "tag_id")` → `Index("image_report_tag_suggestions_idx_tag_id", "tag_id")`
- `app/models/tag_external_link.py:62`: `Index("idx_tag_id", "tag_id")` → `Index("tag_external_links_idx_tag_id", "tag_id")`

- [ ] **Step 5: Shrink `app/core/pg_schema.py`**

Replace the module with:

```python
"""Postgres schema bootstrap from the models.

The models' view of the schema, which tests/integration/test_schema_sync.py
compares against the migration chain. Everything the chain creates that
create_all cannot express lives here: the citext extension and the counter
triggers (ADR-0009).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.pg_triggers import install_counter_triggers


async def build_pg_schema(conn: AsyncConnection) -> None:
    """Drop and rebuild the public schema from the models. Destroys all data.

    DROP SCHEMA CASCADE instead of drop_all: the FK graph has cycles that
    drop_all cannot untangle. citext must be recreated after the drop — it
    lives in public.
    """
    # app.main, not app.models: the models package __init__ does not import
    # every model module (e.g. user_suspension), but the app wiring does.
    from sqlmodel import SQLModel

    import app.main  # noqa: F401  (registers all tables on SQLModel.metadata)

    await conn.execute(text("DROP SCHEMA public CASCADE"))
    await conn.execute(text("CREATE SCHEMA public"))
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
    await conn.run_sync(SQLModel.metadata.create_all)
    await install_counter_triggers(conn)
```

Delete the generator and the stale backup:

```bash
git rm -q scripts/gen_pg_baseline.py app/models/generated.py.backup
```

- [ ] **Step 6: Verify**

Run: `grep -rn 'UnsignedInt\|UnsignedSmallInt\|ci_string\|dedupe_index_names\|_LENGTH_CHECKS\|dialects.mysql' app/ scripts/ tests/; uv run ruff check app/ && uv run mypy app/ && ./run-tests.sh tests/integration/test_schema_sync.py --schema-sync -v`
Expected: the grep prints nothing; ruff and mypy clean; the schema-sync test PASSES (the models-built and chain-built catalogs are identical, which proves the CHECKs and index names moved without changing DDL).

- [ ] **Step 7: Commit**

```bash
git add -A app/ scripts/
git commit -m "refactor(models): plain integers, CITEXT, CHECKs and index names in the models

Deletes the unsigned variants, ci_string, and the two schema bootstrap
shims; schema-sync proves the models still match the chain."
```

### Task 4.5: Full suite and PR

- [ ] **Step 1:** `grep -rniE 'mariadb|mysql|aiomysql|pymysql' app/ scripts/ tests/ --include='*.py' | grep -v 'test_pg_trigger_state.py'`. Expected: only comment-level mentions that describe history (e.g. "inherited from the legacy site"). Read each hit; delete any that now states something false.
- [ ] **Step 2:** `./run-tests.sh` (parallel), `uv run mypy app/`, `uv run ruff check app/`, `uv run ruff format --check app/`. Expected: all clean.
- [ ] **Step 3:** Push and open the PR titled `refactor: delete the MariaDB arm from application code`, body listing Tasks 4.1–4.4, with the attribution footer. Merge after CI.

---

# Docs — straight to `main` after PR 4

Documentation-only commits go directly to `main` (repo rule; `docs/agents/` is not touched here).

### Task 5.1: ADR-0014 and the ADR-0010 note

**Files:**
- Create: `docs/adr/0014-postgres-is-the-only-supported-database.md`
- Modify: `docs/adr/0010-postgres-migrations-are-a-parallel-chain-with-a-frozen-baseline.md`

- [ ] **Step 1: Write ADR-0014**

```markdown
# Postgres is the only supported database

Production cut over from MariaDB to Postgres 18 on 2026-08-22 (PR #358) and
MariaDB was retired from the codebase in September 2026 (design record:
`docs/plans/2026-Q3/2026-09-10-mariadb-retirement-design.md`). There is one
backend. Code is written for Postgres directly: no `is_postgres` switch, no
dialect-neutral SQL for its own sake, no second migration chain, no backend
markers in the test suite.

## Considered Options

- **Keeping the dialect branches** as insurance against returning to MariaDB
  was rejected. There is no plan to return, every branch was dead code in
  prod, and the pair rule for migrations taxed every schema change. Two of the
  branches were live defects on Postgres: the conflict-retry helper matched
  MariaDB errnos and never fired, and the affinity rebuild raised
  `NotImplementedError` nightly.
- **Porting the MariaDB full-text search to Postgres** in the same effort was
  rejected as scope creep. Text search keeps the ILIKE path prod has run
  since cutover; a tsvector or Meilisearch upgrade is its own plan.

## Consequences

- The migration chain lives at `alembic/` and a bare `alembic upgrade head`
  targets it. The frozen-baseline rule (ADR-0010) stays in force.
- Transient write conflicts are recognized by SQLSTATE (`40P01`, `40001`),
  per ADR-0004 as amended.
- Postgres-specific constructs are used where they are the right tool:
  advisory locks, `ON CONFLICT DO NOTHING`, `UPDATE ... FROM`, `NULLS LAST`,
  temp tables, `citext` (ADR-0008), PL/pgSQL counter triggers (ADR-0009).
- The comment-search `natural` and `boolean` mode values are still accepted
  and behave as `all_words`; collapsing the enum is a frontend-visible API
  change and a separate decision.
- Adding a dialect branch, a `sqlalchemy.dialects.mysql` import, or a second
  driver is a regression against this decision.
```

- [ ] **Step 2: Close out ADR-0010**

Append to the end of ADR-0010:

```markdown

## Status (2026-09)

The parallel-chain period ended with the MariaDB retirement (ADR-0014).
`alembic_pg/` was moved to `alembic/`, the MariaDB chain and `alembic.pg.ini`
were deleted, and the pair rule dissolved. The frozen baseline, the
`!alembic/versions/*.sql` gitignore rule, and the never-regenerate rule stay
in force. `scripts/gen_pg_baseline.py` was deleted with the bootstrap shims it
depended on; it is in git history at the baseline commit if the generation
method is ever needed again.
```

### Task 5.2: Live docs

**Files:**
- Modify: `docs/postgres-cutover-runbook.md:1-2`, `docs/creating_alembic_migrations.md:70-74,117-122,364-372`, `tests/README.md:46-125`, `docs/log-operations.md:189-196`, `docs/ml-tag-suggestions-prod-seeding.md:55`

- [ ] **Step 1: Runbook note** — insert after the title line:

```markdown
> **Completed.** Prod cut over on 2026-08-22 (PR #358); MariaDB was retired
> from the codebase in September 2026 (ADR-0014). Kept as a record; the
> scripts it names were deleted and live in git history.
```

- [ ] **Step 2: Alembic guide** — replace the example `upgrade`/`downgrade` bodies with `op.execute("CREATE INDEX idx_posts_date ON posts (date)")` / `op.execute("DROP INDEX idx_posts_date")` and the docstrings with "Add an index on posts.date." / "Remove the index on posts.date."; replace the "Connect to MySQL" block with

```bash
# Connect to Postgres (dev stack)
docker compose exec postgres psql -U shuushuu -d shuushuu

# Verify the index
\di idx_posts_date
```

and the two `mysql -u user -p database -e "SHOW INDEX ..."` lines with `docker compose exec postgres psql -U shuushuu -d shuushuu -c '\di idx_posts_date'`.

- [ ] **Step 3: tests/README.md** — replace the "Run in parallel" section body with:

```markdown
```bash
make pytest                 # full suite, -n auto --dist loadgroup
make pytest ARGS="-m unit"  # forward extra args to pytest
./run-tests.sh              # same, -n 4
./run-tests.sh tests/unit   # serial run of one path
```
Both run against the dev-stack Postgres container (`docker compose up -d
postgres`). Each worker gets its own database (`shuushuu_pytest_gw0`, ...) and
Redis DB, created automatically. `--dist loadgroup` keeps the schema-sync test
(which rebuilds fixed-name databases) on a single worker.

**Note on Redis fixtures:** Tests using real-Redis fixtures (not mocked) still
target the local Redis at `localhost:6379`. They skip gracefully if Redis is not
available (pre-existing behavior). Only the database is fully isolated; Redis
fixtures are shared across workers.

Redis DB numbering caps runs at 13 workers (see `_test_redis_db` in conftest).
```

and the "Test Database Configuration" section's env block with `TEST_DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/shuushuu_pytest` (one line, no sync URL), noting that `run-tests.sh` sets it from the `POSTGRES_*` values in `.env` so it rarely needs setting by hand; in "Local Development" replace "the same MySQL server" with "the same Postgres server"; in "Test Database Lifecycle" replace "Creates all tables from SQLModel metadata" with "Runs the alembic chain to head (truncate-only when already there)".

- [ ] **Step 4:** In `docs/log-operations.md` delete the whole "DB connection-pool ping errors (sqlalchemy ↔ pymysql version drift)" subsection (heading, query block, and paragraph). In `docs/ml-tag-suggestions-prod-seeding.md:55` replace `` `mysqldump` dev's `` with `` `pg_dump` dev's ``.

- [ ] **Step 5: Verify and commit**

Run: `uv run python scripts/gen_plans_index.py && grep -rniE 'mariadb|mysql' docs/ tests/README.md --include='*.md' -l | grep -v 'docs/plans/\|docs/adr/000[4-9]\|docs/adr/001[0-4]\|docs/postgres-cutover-runbook.md'`
Expected: the plans index regenerates; the grep prints nothing (only plans, the historical ADRs, and the runbook still mention MariaDB).

```bash
git add docs/ tests/README.md
git commit -m "docs: ADR-0014 Postgres is the only supported database; retire MariaDB from live docs"
```

### Task 5.3: Hand-run operational steps

Not code. Listed for the user; none are blockers.

- [ ] Remove `MARIADB_IMAGE`, `MARIADB_ROOT_PASSWORD`, `MARIADB_DATABASE`, `MARIADB_USER`, `MARIADB_PASSWORD`, `DATABASE_URL_SYNC`, `COMPOSE_DATABASE_URL`, and any `TEST_DATABASE_URL`/`TEST_DATABASE_URL_SYNC` from the dev `.env` (and the test host's). They are inert after PR 3. If `DATABASE_URL` there still names MariaDB, point it at the Postgres container (`postgresql+asyncpg://...@localhost:5432/shuushuu`) so host-side scripts work.
- [ ] `docker compose up -d --remove-orphans` on the dev host to drop the orphaned mariadb container, then `docker volume rm mariadb_data_dev` when satisfied. Same for `mariadb_data_test` on the test host.
- [ ] The native MariaDB server on the prod DB tier is decommissioned outside this repo.
- [ ] Optional follow-up in the frontend repo: reword the three comments (`playwright.config.ts:54`, `tests/e2e/profile-favorite-tags.spec.ts:401`, `tests/e2e/link-pictures.spec.ts:13,92`) that describe the retry in terms of MariaDB error 1020.
