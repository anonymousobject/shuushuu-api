# UTC Datetime TypeDecorator Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the structural mismatch between MariaDB DATETIME columns (returned as naive Python datetimes) and tz-aware application code (`datetime.now(UTC)`) by introducing a SQLAlchemy `TypeDecorator` that makes all datetime columns return timezone-aware values, then deleting the 18 scattered `.replace(tzinfo=None)` workarounds.

**Architecture:** Add `app/models/types.py::UtcDateTime` (a `TypeDecorator(impl=DateTime)`). On bind, accept only tz-aware datetimes, convert to UTC, strip tzinfo for storage. On result, attach `tzinfo=UTC` to naive datetimes coming from the DB. Apply to every datetime column across `app/models/*.py`. Then delete the now-unnecessary `.replace(tzinfo=None)` calls in `app/api/v1/*` and `app/services/*`.

**Tech Stack:** Python 3.12+, SQLAlchemy 2.0 async, SQLModel, MariaDB (DATETIME columns).

---

## Design decisions

These were settled in the brainstorm; capturing here so future readers can see the reasoning.

1. **Why a TypeDecorator and not a column-type migration to `DateTime(timezone=True)`?** MariaDB has no `TIMESTAMP WITH TIME ZONE` type. SQLAlchemy's `timezone=True` flag is a no-op on MariaDB; it only takes effect on dialects like PostgreSQL. The TypeDecorator is the standard SQLAlchemy pattern for cross-dialect tz handling and is what production Python projects on MySQL/MariaDB use.

2. **Why not migrate to PostgreSQL?** PostgreSQL's `timestamptz` would solve this natively, but the migration cost (schema rewrite, data dump/reload, SQL dialect differences, test infrastructure changes) is much larger than the bug class warrants. The TypeDecorator unblocks the immediate pain and is forward-compatible — if PostgreSQL migration ever happens, the decorator becomes a passthrough (PostgreSQL returns aware datetimes natively).

3. **Strict-on-bind policy:** Naive datetimes passed to the column raise `TypeError("naive datetime not allowed; pass datetime.now(UTC) or attach tzinfo=UTC")`. Reasoning: every code path in the codebase already uses `datetime.now(UTC)`. Strict bind catches any future regression at write time. Server-side defaults (`server_default=text("current_timestamp()")`) are unaffected because they bypass `process_bind_param` — MariaDB writes its own UTC value, and our `process_result_value` attaches `tzinfo=UTC` on read.

4. **Out of scope:** the 3 naive `datetime.now()` calls in `upload.py:112`, `image_processing.py:83`, `images.py:2147` produce filename date prefixes (immediately stringified via `.strftime`). They don't go through the ORM and aren't a tz-comparison risk. Fix in a follow-up cleanup PR. The `UTCDatetime` Pydantic serializer at `app/schemas/base.py` will keep working unchanged because all values it sees will now be tz-aware.

---

## File map

**Created:**
- `app/models/types.py` — `UtcDateTime` TypeDecorator (~50 lines incl. docstring)
- `tests/unit/test_models_types.py` — round-trip + edge cases (in-memory SQLite for speed)
- `tests/integration/test_datetime_round_trip.py` — round-trip against real MariaDB to confirm dialect behavior

**Modified — model field types (one task per file in Chunk 2):**
- `app/models/user.py` (8 fields)
- `app/models/refresh_token.py` (3 fields)
- `app/models/user_suspension.py` (3 fields)
- `app/models/image_review.py` (3 fields)
- `app/models/ban.py` (3 fields)
- `app/models/image.py` (3 fields)
- `app/models/comment.py` (2)
- `app/models/comment_report.py` (2)
- `app/models/image_report.py` (2)
- `app/models/misc.py` (2)
- `app/models/privmsg.py` (2)
- `app/models/tag_external_link.py` (2)
- `app/models/tag_history.py` (2)
- `app/models/admin_action.py` (1)
- `app/models/character_source_link.py` (1)
- `app/models/favorite.py` (1)
- `app/models/image_rating.py` (1)
- `app/models/image_report_tag_suggestion.py` (1)
- `app/models/image_status_history.py` (1)
- `app/models/review_vote.py` (1)
- `app/models/tag.py` (1)
- `app/models/tag_audit_log.py` (1)
- `app/models/tag_link.py` (1)

**Modified — workaround removal (Chunk 3):**
- `app/api/v1/auth.py` (8 sites: lines 91-93, 109, 291, 409, 430-436, 689, 720, 779-780, 845-846)
- `app/api/v1/admin.py` (2 sites: lines 2424, 2534)
- `app/api/v1/users.py` (1 site: line 393)
- `app/api/v1/feeds.py` (1 site: line 41)
- `app/services/upload.py` (1 site: line 62)
- `app/services/review_jobs.py` (1 site: line 339)
- `app/services/user_cleanup.py` (1 site: line 31)

**Tests added (Chunk 4):**
- `tests/unit/test_models_types.py` — bind-strictness, naive→aware result, None passthrough, non-UTC bind conversion
- `tests/integration/test_datetime_round_trip.py` — write `datetime.now(UTC)`, reload, assert `.tzinfo == UTC`
- `tests/api/v1/test_auth_lockout_regression.py` — direct DB-write of lockout_until, then login attempt; the original PR #198 bug class

---

## Chunk 1: TypeDecorator + unit tests

### Task 1.1: Write the failing unit test for naive-bind rejection

**Files:**
- Create: `tests/unit/test_models_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_models_types.py
"""Unit tests for app.models.types.UtcDateTime."""

from datetime import UTC, datetime, timezone

import pytest

from app.models.types import UtcDateTime


class TestUtcDateTimeBind:
    """process_bind_param converts Python -> DB value."""

    def test_naive_datetime_raises(self):
        """Naive datetimes must be rejected on bind to prevent ambiguous UTC assumptions."""
        col = UtcDateTime()
        with pytest.raises(TypeError, match="naive datetime"):
            col.process_bind_param(datetime(2026, 5, 1, 12, 0, 0), dialect=None)

    def test_utc_aware_datetime_strips_tzinfo(self):
        col = UtcDateTime()
        result = col.process_bind_param(datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC), dialect=None)
        assert result == datetime(2026, 5, 1, 12, 0, 0)
        assert result.tzinfo is None

    def test_non_utc_aware_datetime_converts_to_utc(self):
        from datetime import timedelta

        est = timezone(timedelta(hours=-5))
        col = UtcDateTime()
        # 12:00 EST == 17:00 UTC
        result = col.process_bind_param(datetime(2026, 5, 1, 12, 0, 0, tzinfo=est), dialect=None)
        assert result == datetime(2026, 5, 1, 17, 0, 0)
        assert result.tzinfo is None

    def test_none_passthrough(self):
        col = UtcDateTime()
        assert col.process_bind_param(None, dialect=None) is None


class TestUtcDateTimeResult:
    """process_result_value converts DB value -> Python."""

    def test_naive_datetime_becomes_utc_aware(self):
        col = UtcDateTime()
        result = col.process_result_value(datetime(2026, 5, 1, 12, 0, 0), dialect=None)
        assert result == datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_already_aware_passthrough(self):
        col = UtcDateTime()
        aware = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        result = col.process_result_value(aware, dialect=None)
        assert result == aware
        assert result.tzinfo == UTC

    def test_none_passthrough(self):
        col = UtcDateTime()
        assert col.process_result_value(None, dialect=None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_models_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.types'`

- [ ] **Step 3: Write minimal implementation**

Create `app/models/types.py`:

```python
"""SQLAlchemy column types for the shuushuu-api models.

UtcDateTime: a DateTime variant that round-trips tz-aware datetimes through
MariaDB's tz-naive DATETIME. Stores values as UTC (tzinfo stripped); attaches
tzinfo=UTC on read. Naive datetimes are rejected on bind to avoid ambiguous
"is this UTC or local?" assumptions.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """DATETIME column that always exposes tz-aware UTC datetimes in Python.

    MariaDB's DATETIME stores no tz info. Without this decorator, reads return
    naive datetimes that cannot be compared with `datetime.now(UTC)` without
    raising TypeError. This decorator centralizes the convention "DB stores
    UTC, Python sees aware" so call sites never need to strip or attach
    tzinfo manually.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise TypeError(
                "naive datetime not allowed; pass datetime.now(UTC) or attach tzinfo=UTC"
            )
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_models_types.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add app/models/types.py tests/unit/test_models_types.py
git commit -m "feat(models): add UtcDateTime TypeDecorator for tz-aware columns"
```

### Task 1.2: Add integration round-trip test against real MariaDB

**Files:**
- Create: `tests/integration/test_datetime_round_trip.py`

This task confirms that the decorator behaves correctly when wired into a real model, against the actual MariaDB driver — the unit tests use synthetic dialect=None calls and don't exercise SQLAlchemy's full bind/result path.

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_datetime_round_trip.py
"""Round-trip a tz-aware datetime through a real MariaDB column."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, Integer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class _RoundTripFixture(SQLModel, table=True):
    __tablename__ = "_test_utc_datetime_round_trip"
    id: int = Field(default=None, primary_key=True)
    ts: datetime = Field(sa_column=Column(UtcDateTime, nullable=False))


@pytest.mark.integration
async def test_round_trip_utc(db_session: AsyncSession):
    """A tz-aware UTC datetime survives write+read with tzinfo intact."""
    await db_session.run_sync(lambda sync_conn: SQLModel.metadata.create_all(sync_conn, tables=[_RoundTripFixture.__table__]))
    written = datetime(2026, 5, 1, 12, 30, 45, tzinfo=UTC)
    db_session.add(_RoundTripFixture(ts=written))
    await db_session.commit()

    from sqlalchemy import select
    result = await db_session.execute(select(_RoundTripFixture))
    row = result.scalar_one()
    assert row.ts == written
    assert row.ts.tzinfo == UTC


@pytest.mark.integration
async def test_round_trip_non_utc_converts(db_session: AsyncSession):
    """A non-UTC aware datetime is converted to UTC for storage."""
    await db_session.run_sync(lambda sync_conn: SQLModel.metadata.create_all(sync_conn, tables=[_RoundTripFixture.__table__]))
    est = timezone(timedelta(hours=-5))
    written = datetime(2026, 5, 1, 12, 0, 0, tzinfo=est)  # 17:00 UTC
    db_session.add(_RoundTripFixture(ts=written))
    await db_session.commit()

    from sqlalchemy import select
    result = await db_session.execute(select(_RoundTripFixture))
    row = result.scalar_one()
    assert row.ts == datetime(2026, 5, 1, 17, 0, 0, tzinfo=UTC)
    assert row.ts.tzinfo == UTC


@pytest.mark.integration
async def test_naive_bind_raises(db_session: AsyncSession):
    """Writing a naive datetime is rejected at bind time."""
    await db_session.run_sync(lambda sync_conn: SQLModel.metadata.create_all(sync_conn, tables=[_RoundTripFixture.__table__]))
    db_session.add(_RoundTripFixture(ts=datetime(2026, 5, 1, 12, 0, 0)))
    with pytest.raises(TypeError, match="naive datetime"):
        await db_session.commit()
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/integration/test_datetime_round_trip.py -v`
Expected: PASS (3 tests). If the fixture-table create needs more setup, adjust to drop the table after the test (or use a temp alembic revision) — the goal is verification, not a permanent table.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_datetime_round_trip.py
git commit -m "test(models): integration round-trip for UtcDateTime against MariaDB"
```

---

## Chunk 2: Apply UtcDateTime to every model datetime field

This chunk is mechanical but high-volume. The procedure is identical for every file. Do **one model at a time**, run the existing tests for that model after each change, and commit after each file (or small group of related files). This way any regression is bisectable.

### General procedure (apply to each model file below)

For each datetime field in the model:

**Before:**
```python
created_at: datetime | None = Field(
    sa_column_kwargs={"server_default": text("current_timestamp()")}
)
```

**After:**
```python
created_at: datetime | None = Field(
    sa_column=Column(UtcDateTime, server_default=text("current_timestamp()"))
)
```

**Or, when there's a default_factory:**

**Before:**
```python
date_added: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

**After:**
```python
date_added: datetime = Field(
    default_factory=lambda: datetime.now(UTC),
    sa_column=Column(UtcDateTime, nullable=False),
)
```

**Or, simple plain field with no default:**

**Before:**
```python
expires_at: datetime
```

**After:**
```python
expires_at: datetime = Field(sa_column=Column(UtcDateTime, nullable=False))
```

(Adjust `nullable=` to match the original optionality — `datetime | None` → `nullable=True`.)

**Imports to add at the top of each model file:**
```python
from sqlalchemy import Column

from app.models.types import UtcDateTime
```

**Critical:** preserve all existing `server_default=`, `nullable=`, `default=`, `default_factory=`, and any other column kwargs. The only change is wrapping the column type with `UtcDateTime`.

### Task 2.1: app/models/user.py (8 fields)

**Files:**
- Modify: `app/models/user.py:114-148`

- [ ] **Step 1: Apply procedure to all 8 datetime fields** (`date_joined`, `last_login`, `last_active`, `lockout_until`, `email_verification_sent_at`, `email_verification_expires_at`, `password_reset_sent_at`, `password_reset_expires_at`)

- [ ] **Step 2: Run user-related tests**

Run: `uv run pytest tests/api/v1/test_auth.py tests/api/v1/test_users.py -v`
Expected: any test that previously read a user's datetime and compared it against `datetime.now(UTC).replace(tzinfo=None)` will now FAIL — that is intentional; it surfaces the call sites we're about to fix in Chunk 3. Tests that just write/read datetimes should still PASS.

- [ ] **Step 3: Commit**

```bash
git add app/models/user.py
git commit -m "refactor(models): user datetime fields use UtcDateTime"
```

### Task 2.2: app/models/refresh_token.py (3 fields)

Same procedure for `created_at`, `expires_at`, `revoked_at`.

- [ ] **Modify, run `tests/api/v1/test_auth.py::TestRefreshToken*`, commit.**

```bash
git commit -m "refactor(models): refresh_token datetime fields use UtcDateTime"
```

### Task 2.3: app/models/user_suspension.py (3 fields)

Same procedure for `actioned_at`, `suspended_until`, `acknowledged_at`.

- [ ] **Modify, run `tests/api/v1/test_admin_suspensions.py` (or equivalent), commit.**

### Task 2.4: app/models/image_review.py (3 fields)

`deadline`, `created_at`, `closed_at`.

- [ ] **Modify, run `tests/api/v1/test_reviews.py`, commit.**

### Task 2.5: app/models/ban.py (3 fields)

`Bans.date`, `Bans.expires`, `BannedIPs.date`.

- [ ] **Modify, run `tests/api/v1/test_admin*.py` ban-related tests, commit.**

### Task 2.6: app/models/image.py (3 fields)

`date_added`, `status_updated`, `last_post`.

- [ ] **Modify, run `tests/api/v1/test_images.py` plus `tests/api/v1/test_image_status_history.py`, commit.**

### Task 2.7: Remaining model files (one commit per file)

For each file below, apply the same procedure and commit individually. Each is a small, mechanical change.

- [ ] `app/models/comment.py` (date, last_updated)
- [ ] `app/models/comment_report.py` (created_at, reviewed_at)
- [ ] `app/models/image_report.py` (created_at, reviewed_at)
- [ ] `app/models/misc.py` (created_at on AuditLog?, date on Inbox)
- [ ] `app/models/privmsg.py` (Inbox.date, Outbox.date)
- [ ] `app/models/tag_external_link.py` (date_added, dead_at)
- [ ] `app/models/tag_history.py` (date in TagHistoryBase, date in TagHistory)
- [ ] `app/models/admin_action.py` (created_at)
- [ ] `app/models/character_source_link.py` (created_at)
- [ ] `app/models/favorite.py` (fav_date)
- [ ] `app/models/image_rating.py` (date)
- [ ] `app/models/image_report_tag_suggestion.py` (created_at)
- [ ] `app/models/image_status_history.py` (created_at)
- [ ] `app/models/review_vote.py` (created_at)
- [ ] `app/models/tag.py` (date_added)
- [ ] `app/models/tag_audit_log.py` (created_at)
- [ ] `app/models/tag_link.py` (date_linked)

After each: run any test file mentioning the model, then commit. If a test fails because it does `datetime.now(UTC).replace(tzinfo=None)` against the model, leave the failure — Chunk 3 deletes those workarounds and fixes the test.

### Task 2.8: Final sweep — run full suite

- [ ] **Run full test suite to enumerate the breakage.**

Run: `uv run pytest --tb=no -q 2>&1 | tail -40`
Expected: many FAILs — all of them comparison errors of the form `TypeError: can't compare offset-naive and offset-aware datetimes` or `unsupported operand type(s) for -`. **Document the failing test list** — this becomes the regression checklist for Chunk 3.

- [ ] **Commit a checkpoint** if needed, but don't push to remote yet.

---

## Chunk 3: Delete the .replace(tzinfo=None) workarounds

Now that DB reads return tz-aware datetimes, the strip-tz workarounds are not just unnecessary — they're harmful. `aware.replace(tzinfo=None) - aware_from_db` raises TypeError. So every workaround must go.

### Task 3.1: app/api/v1/auth.py — 8 sites

**Files:**
- Modify: `app/api/v1/auth.py`

Each site below is a search-and-delete:

**Replace** `datetime.now(UTC).replace(tzinfo=None)` → `datetime.now(UTC)`

Specifically these lines (per-line diffs):

```
91-93:   suspended_until < datetime.now(UTC).replace(tzinfo=None)
         → suspended_until < datetime.now(UTC)

109:     remaining = suspension.suspended_until - datetime.now(UTC).replace(tzinfo=None)
         → remaining = suspension.suspended_until - datetime.now(UTC)

291:     now_naive = datetime.now(UTC).replace(tzinfo=None)
         → Rename: now = datetime.now(UTC) (and update downstream uses)

409:     if db_token.expires_at < datetime.now(UTC).replace(tzinfo=None):
         → if db_token.expires_at < datetime.now(UTC):

430-436: revoked_at = db_token.revoked_at
         if revoked_at.tzinfo is None:
             revoked_at_utc = revoked_at.replace(tzinfo=UTC)
         else:
             revoked_at_utc = revoked_at
         time_since_revoked = datetime.now(UTC) - revoked_at_utc
         → time_since_revoked = datetime.now(UTC) - db_token.revoked_at
         (the if/else block is no longer needed — revoked_at is now always aware)

689:     if user.email_verification_expires_at < datetime.now(UTC).replace(tzinfo=None):
         → ... < datetime.now(UTC):

720:     datetime.now(UTC).replace(tzinfo=None) - current_user.email_verification_sent_at
         → datetime.now(UTC) - current_user.email_verification_sent_at

779-780: sent_at = sent_at.replace(tzinfo=None)
         time_since_last = datetime.now(UTC).replace(tzinfo=None) - sent_at
         → time_since_last = datetime.now(UTC) - sent_at
         (delete the sent_at reassignment)

845-846: expires_at = expires_at.replace(tzinfo=None)
         if expires_at < datetime.now(UTC).replace(tzinfo=None):
         → if expires_at < datetime.now(UTC):
         (delete the expires_at reassignment)
```

- [ ] **Step 1: Apply all 8 edits to auth.py**

- [ ] **Step 2: Run auth tests**

Run: `uv run pytest tests/api/v1/test_auth.py -v`
Expected: PASS — including any tests that broke after Chunk 2. If new failures appear, the most likely cause is a test that was constructing a naive datetime literal (`datetime(2026, 1, 1)`) and feeding it to a model. Those tests need updating to use `datetime(2026, 1, 1, tzinfo=UTC)`.

- [ ] **Step 3: Commit**

```bash
git add app/api/v1/auth.py
git commit -m "refactor(auth): drop tz-strip workarounds now that DB returns aware datetimes"
```

### Task 3.2: app/api/v1/admin.py — 2 sites

```
2424:    now_naive = now.replace(tzinfo=None)
         → delete this line; replace downstream uses of now_naive with now

2534:    or latest_suspended.suspended_until > datetime.now(UTC).replace(tzinfo=None)
         → ... > datetime.now(UTC)
```

- [ ] **Step 1: Apply edits**
- [ ] **Step 2: Run admin tests**
- [ ] **Step 3: Commit**

```bash
git commit -m "refactor(admin): drop tz-strip workarounds"
```

### Task 3.3: app/api/v1/users.py (1 site)

```
393:     now = datetime.now(UTC).replace(tzinfo=None)
         → now = datetime.now(UTC)
```

- [ ] **Apply, test, commit.**

### Task 3.4: app/api/v1/feeds.py (1 site)

```
41:      return dt.replace(tzinfo=UTC)
```

This site does the *opposite* (attaches UTC to a naive value). It's no longer needed because `dt` is already aware coming from the model. Replace the function body with an identity for already-aware values, or delete the helper entirely if it has no other purpose. Inspect the surrounding code to decide.

- [ ] **Inspect, apply, test, commit.**

### Task 3.5: app/services/upload.py (1 site)

```
62:      elapsed = (datetime.now(UTC).replace(tzinfo=None) - last_upload).total_seconds()
         → elapsed = (datetime.now(UTC) - last_upload).total_seconds()
```

- [ ] **Apply, run upload tests, commit.**

### Task 3.6: app/services/review_jobs.py (1 site)

```
339:     cutoff_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=...)
         → cutoff_date = datetime.now(UTC) - timedelta(days=...)
```

- [ ] **Apply, test, commit.**

### Task 3.7: app/services/user_cleanup.py (1 site)

Same pattern.

- [ ] **Apply, test, commit.**

### Task 3.8: Full suite green

- [ ] **Run full suite.**

Run: `uv run pytest 2>&1 | tail -10`
Expected: full suite green.

If any test still fails, it's likely a test fixture or assertion that constructs a naive datetime literal. Update the literal to be tz-aware (`datetime(..., tzinfo=UTC)`) and commit the test fix.

- [ ] **Run mypy.**

Run: `uv run mypy app/ 2>&1 | tail -10`
Expected: no new errors.

---

## Chunk 4: Regression test for the lockout class of bug

Before this work, the lockout-login bug (PR #198) happened because someone forgot the `.replace(tzinfo=None)` workaround. Now that the workaround is gone entirely, an analogous "compare a DB datetime against `datetime.now(UTC)`" can never raise TypeError. Add an explicit regression test pinning that contract.

### Task 4.1: Lockout regression test

**Files:**
- Create: `tests/api/v1/test_auth_lockout_regression.py` (or extend the existing test_auth.py file with a new class)

- [ ] **Step 1: Write the test**

```python
# tests/api/v1/test_auth_lockout_regression.py
"""Regression: PR #198 — login broke when lockout_until existed because
the DB returned a naive datetime that couldn't be compared with
datetime.now(UTC). With UtcDateTime in place, this can never recur.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.user import Users


@pytest.mark.api
class TestLockoutRegression:
    async def test_login_works_when_lockout_until_set_in_past(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """User with an *expired* lockout_until can still log in.
        Pre-#198 this raised TypeError → 500 → masked as Login Failed."""
        password = "TestPassword123!"
        user = Users(
            username="lockout_user",
            password=get_password_hash(password),
            password_type="bcrypt",
            salt="",
            email="lockout@example.com",
            active=1,
            lockout_until=datetime.now(UTC) - timedelta(hours=1),  # already expired
        )
        db_session.add(user)
        await db_session.commit()

        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "lockout_user", "password": password},
        )
        assert response.status_code == 200, response.text
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/api/v1/test_auth_lockout_regression.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/api/v1/test_auth_lockout_regression.py
git commit -m "test(auth): regression test for #198 lockout login class"
```

---

## Chunk 5: PR + post-merge cleanup

### Task 5.1: Open PR

- [ ] Create PR with summary covering: design decision (TypeDecorator over Postgres migration / over `DateTime(timezone=True)`), the strict-on-bind contract, model coverage, and the deleted-workarounds list.
- [ ] Run `/ultrareview` if appropriate (the user can decide).
- [ ] Address review feedback.

### Task 5.2: Follow-up issues (do NOT include in this PR)

Open separate issues / scheduled tasks for:
- [ ] **Filename date-prefix UTC-ization** — three `datetime.now()` calls in `upload.py:112`, `image_processing.py:83`, `images.py:2147` produce filenames using server-local time. Trivial fix (`datetime.now(UTC)`), but separate concern.
- [ ] **Schema serializer hardening** — `app/schemas/base.py::UTCDatetime` could `astimezone(UTC)` before formatting instead of trusting the `Z` label. Defensive only; current behavior is correct because all values are now aware UTC.
- [ ] **Moderator-visibility filter on REVIEW transitions** — the deferred privacy concern from PR #203.

---

## Done definition

- All 22+ model files use `UtcDateTime` for datetime columns.
- Zero `.replace(tzinfo=None)` calls remain in `app/` (verify with `grep -rn 'replace(tzinfo=None)' app/`).
- `datetime.utcnow()` count: still zero (verify with grep).
- Full test suite green; mypy clean.
- Regression test for the lockout class committed.
- PR opened with the design rationale documented.
