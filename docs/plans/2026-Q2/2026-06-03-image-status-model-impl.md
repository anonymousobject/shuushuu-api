# Image Status Model Redesign — Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the overloaded image-`status` int into separate axes (state + deactivation reason category + free-text reason), introduce a single `DEACTIVATED` status, and route the direct mod status-change endpoint through one unified service that always logs consistently — without breaking any existing history.

**Architecture:** Today, status-change logic is copy-pasted inline across five route handlers, `status` encodes both moderation state and reason with no field for an explanation, and two of the five paths log inconsistently. This plan reuses status `0` — historically the generic "disable" target — as a first-class `DEACTIVATED` status (retiring the vague `OTHER` name, kept only as a deprecated alias), so the `INAPPROPRIATE` / `LOW_QUALITY` / `OTHER` *statuses* collapse into deactivation *reason categories*. It adds nullable `reason_category` + `reason` columns to both `images` and `image_status_history`, extracts a `change_image_status()` service into `app/services/image_status.py`, and wires the direct `PATCH /admin/images/{id}` handler through it. Existing int values are **kept** (no renumbering) so historical `image_status_history` rows still resolve to their labels — the one change is that `0` now renders "deactivated" instead of "other", which is *more* accurate (0 always was the disable bucket). A data migration backfills `reason_category` for existing `0` images, converts `-2/-3` images to `DEACTIVATED(0) + category`, and **backfills the triage-gap history rows** that the current `action_report` path never wrote.

**Tech Stack:** FastAPI, SQLModel, Pydantic v2, Alembic, MariaDB (aiomysql async / pymysql sync), pytest + pytest-asyncio (auto mode), `uv` for tooling.

**Scope boundaries (read before starting):**
- **In scope (Plan 1):** config enums, `Images` + `ImageStatusHistory` models, `ImageStatusUpdate`/`ImageStatusResponse` schemas, the unified service, the direct `PATCH /admin/images/{image_id}` handler, the `GET /images/{id}/status-history` reason-visibility, and the data migration.
- **Out of scope — Plan 2 (reports & reviews):** routing `action_report`, `escalate_report`, `create_review` through the service; report-resolution storage (resulting status + mod reason); requiring a reason on review creation; removing `review_type`. **Do not touch these handlers in Plan 1** beyond leaving them working as-is.
- **Out of scope — Plan 3 (frontend):** the Deactivate dialog, triage UI, report dialog, reviews UI, regenerated API types.
- **Owner path** (`PATCH /images/{id}` in `images.py`) is intentionally left on its inline logic in Plan 1; the service is designed to absorb it later.

**Design decisions locked with the maintainer (do not relitigate):**
- Reuse `0` (historically the generic "disable" bucket) as `DEACTIVATED`, retiring `OTHER` as a status name. No renumbering, no new int value. `OTHER = 0` is kept as a deprecated alias so existing test references keep resolving (removing it + migrating those refs is deferred cleanup).
- `DEACTIVATED = 0` replaces the `OTHER` status outright — live `status=0` rows are already disabled images and stay at `0` (the migration only backfills their `reason_category`). `INAPPROPRIATE`(-2)/`LOW_QUALITY`(-3) become legacy: still valid for loading old rows and rendering old history, but **no longer settable**, and converted to `DEACTIVATED(0) + category` by the migration.
- New `DeactivationReason` categories: `INAPPROPRIATE=1, LOW_QUALITY=2, SPAM=3, OTHER=4`. Shown to **everyone**.
- Free-text `reason`: visible to **everyone** for spoiler/repost transitions; visible to **owner + mods only** for deactivated/review transitions.
- Actor identity visibility is unchanged (`VISIBLE_USER_STATUSES = {REPOST, SPOILER, ACTIVE}`).
- Keep `admin_actions` and `image_status_history` as separate tables; do **not** dedupe the status-in-JSON overlap.
- Lock/unlock stays status-only-absent from `image_status_history` (internal `admin_actions` only) — unchanged in Plan 1.
- A deactivation requires **both** `reason_category` and a non-empty `reason`.

---

## Pre-flight (one-time, before Task 1)

- [ ] **Create a working branch off `main` in the API repo:**

```bash
git -C /home/dtaylor/shuu/shuushuu-api checkout main
git -C /home/dtaylor/shuu/shuushuu-api pull --ff-only
git -C /home/dtaylor/shuu/shuushuu-api checkout -b image-status-model
```

- [ ] **Confirm the test DB is reachable and the suite is green before changes:**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_admin_images.py -q
```
Expected: PASS (establishes a clean baseline).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `app/config.py` | Modify | Add `ImageStatus.DEACTIVATED`, label, keep legacy labels; add `class DeactivationReason`. |
| `app/models/image.py` | Modify | `validate_status` accepts `DEACTIVATED`; add `reason_category` + `status_reason` columns to `Images`. |
| `app/models/image_status_history.py` | Modify | Add `reason_category` + `reason` columns. |
| `app/schemas/admin.py` | Modify | `ImageStatusUpdate`: add `reason`/`reason_category`, restrict settable statuses, require category+reason for `DEACTIVATED`. `ImageStatusResponse`: expose `reason_category`/`status_reason`. |
| `app/schemas/audit.py` | Modify | `ImageStatusHistoryResponse`: add `reason_category`/`reason`. |
| `app/services/image_status.py` | Modify | Add `change_image_status()` service (the unified mutation+audit path). |
| `app/api/v1/admin.py` | Modify | `change_image_status` route → call the service. |
| `app/api/v1/images.py` | Modify | `get_image_status_history` → reason-visibility scoping + optional auth. |
| `alembic/versions/<hash>_image_status_deactivated.py` | Create | Add 4 columns; convert `-2/-3/0` images; backfill triage-gap history. |
| `tests/api/v1/test_admin_images.py` | Modify | New tests for DEACTIVATED + reason plumbing. |
| `tests/api/v1/test_image_status_history_endpoint.py` | Modify | New tests for reason visibility. |
| `tests/services/test_image_status_service.py` | Create | Unit tests for the service. |

---

## Chunk 1: Foundation (constants, models, migration)

### Task 1: De-risk the new int value

**Files:** none (investigation only).

- [ ] **Step 1: (Pre-investigated — re-confirm) magnitude checks on image status**

Pre-investigation found exactly one magnitude-based check on image status: `Images.status >= 1` in `app/api/v1/images.py:788` and `:797` (the "publicly listed" cutoff = ACTIVE/SPOILER). `DEACTIVATED = 0` sits below it (`0 < 1`), so deactivated images stay correctly excluded — identical to the old `OTHER = 0`. Re-confirm nothing else was added since:

```bash
cd /home/dtaylor/shuu/shuushuu-api && rg -n '\.status\s*[<>]=?\s*[0-9]' app/ | rg -iv 'http_?status|status_code'
```
Expected: only the two `Images.status >= 1` hits. If new magnitude checks appear, evaluate each against `0`.

- [ ] **Step 2: (Pre-investigated) `status = 0` is only ever the disable bucket**

Confirmed during planning: there are no `image.status == 0` comparisons in `app/`, and the model default is `ACTIVE = 1` — so every live `status=0` row is a deliberately-disabled image, safe to treat as `DEACTIVATED`. `ImageStatus.OTHER` is referenced in `app/models/image.py:140` and `app/schemas/admin.py:278` (both rewritten in Tasks 3 and 7) plus ~15 test sites. Because `OTHER` is kept as a deprecated alias of `DEACTIVATED` (both `= 0`), those test references keep resolving — only label-string expectations for `0` change (Task 2, Step 4b).

### Task 2: Add `DEACTIVATED` status and `DeactivationReason` enum

**Files:**
- Modify: `app/config.py:264-293` (class `ImageStatus`), and add a new class after it.

- [ ] **Step 1: Add the failing test**

Add to `tests/unit/test_config_status.py` (create if absent; place under `tests/unit/`):

```python
from app.config import DeactivationReason, ImageStatus


def test_deactivated_status_exists_and_labels():
    assert ImageStatus.DEACTIVATED == 0  # reuses the historical "disable" bucket
    assert ImageStatus.OTHER == 0  # deprecated alias kept for backward-compat
    assert ImageStatus.get_label(ImageStatus.DEACTIVATED) == "deactivated"
    assert ImageStatus.get_label(0) == "deactivated"  # 0 now renders "deactivated"
    # Legacy values must still resolve for historical rows
    assert ImageStatus.get_label(ImageStatus.INAPPROPRIATE) == "inappropriate"
    assert ImageStatus.get_label(ImageStatus.LOW_QUALITY) == "low_quality"


def test_deactivation_reason_labels():
    assert DeactivationReason.INAPPROPRIATE == 1
    assert DeactivationReason.LOW_QUALITY == 2
    assert DeactivationReason.SPAM == 3
    assert DeactivationReason.OTHER == 4
    assert DeactivationReason.get_label(DeactivationReason.SPAM) == "Spam"
    assert DeactivationReason.get_label(999) == "unknown"
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/unit/test_config_status.py -q
```
Expected: FAIL (`AttributeError: ... DEACTIVATED` / `cannot import name 'DeactivationReason'`).

- [ ] **Step 3: Implement**

In `app/config.py`, modify `class ImageStatus` to add the new value + label (keep all existing legacy labels):

```python
class ImageStatus:
    """Image status constants"""

    REVIEW = -4
    LOW_QUALITY = -3  # legacy: no longer settable; kept for historical rows
    INAPPROPRIATE = -2  # legacy: no longer settable; kept for historical rows
    REPOST = -1
    DEACTIVATED = 0  # reuses the historical generic "disable" bucket (was OTHER)
    OTHER = 0  # DEPRECATED alias of DEACTIVATED; remove once test refs migrate
    ACTIVE = 1
    SPOILER = 2

    # Status values where we show the user who made the change in public audit
    VISIBLE_USER_STATUSES: set[int] = {REPOST, SPOILER, ACTIVE}

    LABELS: dict[int, str] = {
        REVIEW: "review",
        LOW_QUALITY: "low_quality",  # legacy label for historical rows
        INAPPROPRIATE: "inappropriate",  # legacy label for historical rows
        REPOST: "repost",
        DEACTIVATED: "deactivated",  # key 0 — replaces the old "other" label
        ACTIVE: "active",
        SPOILER: "spoiler",
    }

    @classmethod
    def get_label(cls, status: int) -> str:
        """Get human-readable label for image status."""
        return cls.LABELS.get(status, "unknown")
```

Add a new class immediately after `ImageStatus`:

```python
class DeactivationReason:
    """Reason categories for a DEACTIVATED image. Shown publicly."""

    INAPPROPRIATE = 1
    LOW_QUALITY = 2
    SPAM = 3
    OTHER = 4

    LABELS: dict[int, str] = {
        INAPPROPRIATE: "Inappropriate",
        LOW_QUALITY: "Low Quality",
        SPAM: "Spam",
        OTHER: "Other",
    }

    VALID: set[int] = {INAPPROPRIATE, LOW_QUALITY, SPAM, OTHER}

    @classmethod
    def get_label(cls, value: int | None) -> str:
        if value is None:
            return ""
        return cls.LABELS.get(value, "unknown")
```

- [ ] **Step 4: Run it to confirm it passes**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/unit/test_config_status.py -q
```
Expected: PASS.

- [ ] **Step 4b: Fix existing label-string expectations for status `0`**

Relabeling `0` from "other" → "deactivated" breaks the few tests that assert the old string. Find and update them:

```bash
cd /home/dtaylor/shuu/shuushuu-api && rg -n '"other"' tests/api/v1/test_image_status_history_endpoint.py tests/api/v1/test_user_history_endpoint.py
```
Update each `..., "other")` label expectation for a `status=0` / `ImageStatus.OTHER` transition to `"deactivated"` (e.g. `test_image_status_history_endpoint.py:532-533`). Then:

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_image_status_history_endpoint.py tests/api/v1/test_user_history_endpoint.py -q
```
Expected: PASS. (Test references to `ImageStatus.OTHER` themselves still resolve via the alias — only the asserted label strings change.)

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/unit/test_config_status.py tests/api/v1/test_image_status_history_endpoint.py tests/api/v1/test_user_history_endpoint.py
git commit -m "feat(status): reuse 0 as DEACTIVATED (alias OTHER), add DeactivationReason enum"
```

### Task 3: Extend the `Images` model

**Files:**
- Modify: `app/models/image.py` — `validate_status` (lines 131-149), add columns near the moderation fields (after line 259).

- [ ] **Step 1: Add the failing test**

Add to `tests/unit/test_config_status.py`:

```python
import pytest
from pydantic import ValidationError

from app.models.image import Images


def test_images_model_accepts_deactivated():
    img = Images(user_id=1, filename="x", ext="jpg", md5_hash="a" * 32, status=ImageStatus.DEACTIVATED)
    assert img.status == ImageStatus.DEACTIVATED
    assert img.reason_category is None
    assert img.status_reason is None


def test_images_model_still_loads_legacy_statuses():
    # Old rows may still construct with legacy values during/after migration.
    # (0/DEACTIVATED is current, not legacy — covered by test_images_model_accepts_deactivated.)
    for legacy in (ImageStatus.INAPPROPRIATE, ImageStatus.LOW_QUALITY):
        assert Images(user_id=1, filename="x", ext="jpg", md5_hash="a" * 32, status=legacy).status == legacy


def test_images_model_rejects_unknown_status():
    with pytest.raises(ValidationError):
        Images(user_id=1, filename="x", ext="jpg", md5_hash="a" * 32, status=99)
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/unit/test_config_status.py -q
```
Expected: FAIL (`reason_category`/`status_reason` not attributes; `DEACTIVATED` not in valid set).

- [ ] **Step 3: Implement**

In `app/models/image.py`, update the `validate_status` valid set to include `DEACTIVATED` (keep all legacy values for backward-load safety):

```python
        valid_statuses = {
            ImageStatus.REVIEW,
            ImageStatus.LOW_QUALITY,  # legacy value, still loadable from old rows
            ImageStatus.INAPPROPRIATE,  # legacy value, still loadable from old rows
            ImageStatus.REPOST,
            ImageStatus.DEACTIVATED,  # == 0 (formerly OTHER)
            ImageStatus.ACTIVE,
            ImageStatus.SPOILER,
        }
```

Add two columns to the `Images` table subclass, next to `replacement_id` (line 259):

```python
    # Deactivation detail (set when status == DEACTIVATED)
    reason_category: int | None = Field(default=None)
    # Free-text reason for the current status (visibility scoped at the API layer)
    status_reason: str | None = Field(default=None, max_length=1000)
```

- [ ] **Step 4: Run it to confirm it passes**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/unit/test_config_status.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models/image.py tests/unit/test_config_status.py
git commit -m "feat(status): add reason_category/status_reason to Images, allow DEACTIVATED"
```

### Task 4: Extend the `ImageStatusHistory` model

**Files:**
- Modify: `app/models/image_status_history.py` (add two fields to the table subclass).

- [ ] **Step 1: Add the failing test**

Add to `tests/unit/test_config_status.py`:

```python
from app.models.image_status_history import ImageStatusHistory


def test_status_history_has_reason_fields():
    h = ImageStatusHistory(
        image_id=1, old_status=ImageStatus.ACTIVE, new_status=ImageStatus.DEACTIVATED,
        reason_category=DeactivationReason.SPAM, reason="ad spam",
    )
    assert h.reason_category == DeactivationReason.SPAM
    assert h.reason == "ad spam"
    # defaults
    h2 = ImageStatusHistory(image_id=1, old_status=1, new_status=2)
    assert h2.reason_category is None
    assert h2.reason is None
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/unit/test_config_status.py::test_status_history_has_reason_fields -q
```
Expected: FAIL (unexpected keyword `reason_category`).

- [ ] **Step 3: Implement**

In `app/models/image_status_history.py`, add to the `ImageStatusHistory` table class (after `user_id`, before `created_at`):

```python
    # Reason metadata for this transition (mirrors images.reason_category / status_reason
    # at the time of the change). Nullable: legacy rows and non-deactivation transitions.
    reason_category: int | None = Field(default=None)
    reason: str | None = Field(default=None, max_length=1000)
```

- [ ] **Step 4: Run it to confirm it passes**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/unit/test_config_status.py::test_status_history_has_reason_fields -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models/image_status_history.py tests/unit/test_config_status.py
git commit -m "feat(status): add reason_category/reason to ImageStatusHistory"
```

### Task 5: Alembic migration (columns + data conversion + history backfill)

**Files:**
- Create: `alembic/versions/<hash>_image_status_deactivated.py`

This migration runs as part of the test suite's session setup (`setup_test_database` runs the full chain to `head`), so a broken migration fails the whole suite — that is the test for it. Match the repo's hand-written style: raw `op.execute` ALTERs with `ALGORITHM=INSTANT, LOCK=NONE` for the large `images` table, and raw SQL for backfills. Reference migrations: `301d283488cc` for the `ALGORITHM=INSTANT, LOCK=NONE` ALTER style, and `81cdaeb0ff13` for the `admin_actions` JOIN-backfill pattern (ours differs in using `INSERT ... SELECT` with `JSON_EXTRACT`).

- [ ] **Step 1: Generate the empty revision**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run alembic revision -m "image status deactivated"
```
This creates `alembic/versions/<hash>_image_status_deactivated.py` with `down_revision = '301d283488cc'` filled in (current head). Confirm:

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run alembic heads
```
Expected: a single head, the new revision.

- [ ] **Step 2: Write `upgrade()` / `downgrade()`**

Replace the generated bodies with:

```python
def upgrade() -> None:
    """Add reason columns; convert legacy disable statuses to DEACTIVATED; backfill triage history."""
    # --- Schema: add columns ---
    # images is large: metadata-only adds, INSTANT algorithm, no lock.
    op.execute(
        "ALTER TABLE images "
        "ADD COLUMN reason_category INT NULL, "
        "ADD COLUMN status_reason VARCHAR(1000) NULL, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )
    op.execute(
        "ALTER TABLE image_status_history "
        "ADD COLUMN reason_category INT NULL, "
        "ADD COLUMN reason VARCHAR(1000) NULL, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )

    # --- Data: backfill reason_category for deactivated images ---
    # DEACTIVATED == 0, so existing status=0 (old OTHER/disabled) images stay at 0 and
    # only gain a reason_category; legacy -2/-3 images move to 0 + their category.
    # status_reason stays NULL: the old system never captured a reason.
    # ORDER MATTERS: backfill the existing 0-rows FIRST, then convert -2/-3 to 0 —
    # otherwise the cat-4 backfill would clobber the just-converted -2/-3 rows.
    #  0 (was OTHER)   -> stay 0 (DEACTIVATED) + cat 4 (Other)
    # -2 INAPPROPRIATE -> 0 (DEACTIVATED)      + cat 1 (Inappropriate)
    # -3 LOW_QUALITY   -> 0 (DEACTIVATED)      + cat 2 (Low Quality)
    op.execute("UPDATE images SET reason_category = 4 WHERE status = 0")
    op.execute("UPDATE images SET status = 0, reason_category = 1 WHERE status = -2")
    op.execute("UPDATE images SET status = 0, reason_category = 2 WHERE status = -3")

    # --- Data: backfill triage-gap history rows ---
    # action_report (admin_actions.action_type = 2 = REPORT_ACTION) historically updated
    # image status WITHOUT writing an image_status_history row. Reconstruct those rows from
    # the audit log so the public history is complete. Use the ORIGINAL recorded int values
    # (do NOT convert to DEACTIVATED) — history must reflect what happened at the time.
    op.execute(
        """
        INSERT INTO image_status_history (image_id, old_status, new_status, user_id, created_at)
        SELECT aa.image_id,
               CAST(JSON_EXTRACT(aa.details, '$.previous_status') AS SIGNED),
               CAST(JSON_EXTRACT(aa.details, '$.new_status') AS SIGNED),
               aa.user_id,
               aa.created_at
        FROM admin_actions aa
        WHERE aa.action_type = 2
          AND aa.image_id IS NOT NULL
          AND JSON_EXTRACT(aa.details, '$.new_status') IS NOT NULL
          AND JSON_EXTRACT(aa.details, '$.previous_status') IS NOT NULL
          AND CAST(JSON_EXTRACT(aa.details, '$.previous_status') AS SIGNED)
              <> CAST(JSON_EXTRACT(aa.details, '$.new_status') AS SIGNED)
        """
    )


def downgrade() -> None:
    """Drop the added columns. (Legacy status conversion and backfilled history rows are
    not reverted — they represent real, historically-accurate events.)"""
    op.execute("ALTER TABLE image_status_history DROP COLUMN reason, DROP COLUMN reason_category")
    op.execute("ALTER TABLE images DROP COLUMN status_reason, DROP COLUMN reason_category")
```

Note in the docstring/comments that `downgrade()` deliberately does not reverse the data conversion: re-deriving the original `-2/-3/0` would require the now-dropped `reason_category`, and the backfilled history rows are legitimate. This is an expand/contract-friendly, forward-only data migration.

- [ ] **Step 3: Run the migration against the test DB**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run alembic upgrade head
```
Expected: completes without error. Then verify it round-trips:

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run alembic downgrade -1 && uv run alembic upgrade head
```
Expected: both succeed (confirms `downgrade()` is valid SQL).

- [ ] **Step 4: Run the full suite to prove the migration is healthy in CI setup**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_image_status_history_endpoint.py tests/api/v1/test_admin_images.py -q
```
Expected: PASS (suite setup re-runs the chain to head with the new migration).

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/
git commit -m "feat(status): migration adding reason columns + legacy DEACTIVATED conversion + triage history backfill"
```

---

## Chunk 2: Unified service, request/response, and reason visibility

### Task 6: Extract the `change_image_status()` service

**Files:**
- Modify: `app/services/image_status.py` (currently holds only `enqueue_r2_sync_on_status_change`).
- Create: `tests/services/__init__.py` (empty package marker — `tests/services/` has none yet; sibling test dirs do).
- Create: `tests/services/test_image_status_service.py`

The service performs the DB mutation + audit logging that is currently inline in the route handler. It **does not commit** — the caller owns the transaction and the post-commit side effects (R2 enqueue, rating recalc), matching the existing handler structure. It raises `HTTPException` for invalid transitions to preserve the current API error messages.

- [ ] **Step 1: Write the failing test**

First make `tests/services/` a package so collection doesn't fail: `touch /home/dtaylor/shuu/shuushuu-api/tests/services/__init__.py`. Then create `tests/services/test_image_status_service.py`:

```python
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AdminActionType, DeactivationReason, ImageStatus
from app.models.admin_action import AdminActions
from app.models.image import Images
from app.models.image_status_history import ImageStatusHistory
from app.models.user import Users
from app.services.image_status import change_image_status


async def _mk_image(db: AsyncSession, user_id: int, status: int = ImageStatus.ACTIVE) -> Images:
    img = Images(user_id=user_id, filename="x", ext="jpg",
                 md5_hash="a" * 32, status=status)
    db.add(img)
    await db.commit()
    await db.refresh(img)
    return img


async def test_deactivate_sets_fields_history_and_audit(db_session: AsyncSession):
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)

    await change_image_status(
        db_session, img, actor,
        new_status=ImageStatus.DEACTIVATED,
        reason_category=DeactivationReason.SPAM,
        reason="advertising",
    )
    await db_session.commit()
    await db_session.refresh(img)

    assert img.status == ImageStatus.DEACTIVATED
    assert img.reason_category == DeactivationReason.SPAM
    assert img.status_reason == "advertising"
    assert img.status_user_id == actor.user_id

    hist = (await db_session.execute(
        select(ImageStatusHistory).where(ImageStatusHistory.image_id == img.image_id)
    )).scalars().all()
    assert len(hist) == 1
    assert hist[0].new_status == ImageStatus.DEACTIVATED
    assert hist[0].reason_category == DeactivationReason.SPAM
    assert hist[0].reason == "advertising"

    action = (await db_session.execute(
        select(AdminActions).where(AdminActions.image_id == img.image_id)
    )).scalar_one()
    assert action.action_type == AdminActionType.IMAGE_STATUS_CHANGE
    assert action.details["new_status"] == ImageStatus.DEACTIVATED


async def test_repost_requires_replacement(db_session: AsyncSession):
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)
    with pytest.raises(HTTPException) as exc:
        await change_image_status(db_session, img, actor, new_status=ImageStatus.REPOST)
    assert exc.value.status_code == 400


async def test_no_history_row_when_status_unchanged(db_session: AsyncSession):
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)
    await change_image_status(db_session, img, actor, locked=True)  # lock only
    await db_session.commit()
    hist = (await db_session.execute(
        select(ImageStatusHistory).where(ImageStatusHistory.image_id == img.image_id)
    )).scalars().all()
    assert hist == []
    assert img.locked == 1
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/services/test_image_status_service.py -q
```
Expected: FAIL (`cannot import name 'change_image_status'`).

- [ ] **Step 3: Implement the service**

Add to `app/services/image_status.py` (keep the existing `enqueue_r2_sync_on_status_change`). Add imports as needed:

```python
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AdminActionType, DeactivationReason, ImageStatus
from app.models.admin_action import AdminActions
from app.models.image import Images
from app.models.image_status_history import ImageStatusHistory
from app.models.user import Users
from app.services.repost import migrate_repost_data


async def change_image_status(
    db: AsyncSession,
    image: Images,
    actor: Users,
    *,
    new_status: int | None = None,
    reason_category: int | None = None,
    reason: str | None = None,
    replacement_id: int | None = None,
    locked: bool | None = None,
) -> dict[str, int]:
    """
    Apply a moderation status and/or lock change to an image, writing the public
    status-history row and the internal admin-action audit row.

    Does NOT commit — the caller owns the transaction and any post-commit side
    effects (R2 sync enqueue, rating recalculation). Raises HTTPException on
    invalid transitions.

    Returns the repost migration_result dict (empty unless a repost was processed).
    """
    previous_status = image.status
    previous_locked = image.locked
    migration_result: dict[str, int] = {}

    if new_status is not None:
        if new_status == ImageStatus.REPOST:
            if replacement_id is None:
                raise HTTPException(status_code=400, detail="replacement_id is required when marking as repost")
            if replacement_id == image.image_id:
                raise HTTPException(status_code=400, detail="An image cannot be a repost of itself")
            original = (await db.execute(select(Images).where(Images.image_id == replacement_id))).scalar_one_or_none()
            if not original:
                raise HTTPException(status_code=404, detail="Original image not found")
            image.replacement_id = replacement_id
            migration_result = await migrate_repost_data(image.image_id, replacement_id, db)
        else:
            image.replacement_id = None

        # Deactivation reason bookkeeping
        if new_status == ImageStatus.DEACTIVATED:
            image.reason_category = reason_category
            image.status_reason = reason
        else:
            image.reason_category = None
            image.status_reason = reason  # reason allowed on spoiler/repost/etc; cleared if None

        image.status = new_status
        image.status_user_id = actor.user_id
        image.status_updated = datetime.now(UTC)

    if locked is not None:
        image.locked = 1 if locked else 0

    # Public status-history row only when the status actually changed
    if new_status is not None and new_status != previous_status:
        db.add(ImageStatusHistory(
            image_id=image.image_id,
            old_status=previous_status,
            new_status=new_status,
            user_id=actor.user_id,
            reason_category=image.reason_category,
            reason=image.status_reason,
        ))

    db.add(AdminActions(
        user_id=actor.user_id,
        action_type=AdminActionType.IMAGE_STATUS_CHANGE,
        image_id=image.image_id,
        details={
            "previous_status": previous_status,
            "new_status": image.status,
            "previous_locked": previous_locked,
            "new_locked": image.locked,
            "replacement_id": image.replacement_id,
            "reason_category": image.reason_category,
            **migration_result,
        },
    ))

    return migration_result
```

- [ ] **Step 4: Run to confirm it passes**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/services/test_image_status_service.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/image_status.py tests/services/
git commit -m "feat(status): add unified change_image_status service"
```

### Task 7: Wire the request/response schemas and the direct mod endpoint

**Files:**
- Modify: `app/schemas/admin.py` (`ImageStatusUpdate` 249-294, `ImageStatusResponse` 297-307).
- Modify: `app/api/v1/admin.py` (`change_image_status` route handler, 715-825).

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/v1/test_admin_images.py` (reuse the file's existing `create_admin_user`/`grant_permission`/`login_user`/`create_test_image` helpers):

```python
from app.config import DeactivationReason


@pytest.mark.api
class TestDeactivateImage:
    async def test_deactivate_requires_category_and_reason(self, client, db_session):
        admin, pw = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)
        token = await login_user(client, admin.username, pw)

        # Missing category + reason -> 422 (schema validation)
        r = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={"status": ImageStatus.DEACTIVATED},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422

    async def test_deactivate_success(self, client, db_session):
        admin, pw = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)
        token = await login_user(client, admin.username, pw)

        r = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={"status": ImageStatus.DEACTIVATED,
                  "reason_category": DeactivationReason.LOW_QUALITY,
                  "reason": "blurry"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == ImageStatus.DEACTIVATED
        assert data["reason_category"] == DeactivationReason.LOW_QUALITY
        assert data["status_reason"] == "blurry"

    async def test_legacy_statuses_no_longer_settable(self, client, db_session):
        admin, pw = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)
        token = await login_user(client, admin.username, pw)
        # -2/-3 are legacy (history-only) and must be rejected. 0 is now DEACTIVATED (settable).
        for legacy in (ImageStatus.INAPPROPRIATE, ImageStatus.LOW_QUALITY):
            r = await client.patch(
                f"/api/v1/admin/images/{image.image_id}",
                json={"status": legacy, "reason": "x"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 422, f"status {legacy} should be rejected"
```

Keep the existing `test_mark_image_as_spoiler` / audit-log test passing (spoiler still works, no reason required).

- [ ] **Step 2: Run to confirm failure**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_admin_images.py::TestDeactivateImage -q
```
Expected: FAIL (DEACTIVATED rejected by schema / response missing fields).

- [ ] **Step 3: Implement the schema changes**

In `app/schemas/admin.py`, replace `ImageStatusUpdate` with:

```python
class ImageStatusUpdate(BaseModel):
    """Request schema for changing image status and/or locked state."""

    status: int | None = Field(
        None,
        description="New status: 0=Deactivated, -4=Review, -1=Repost, 1=Active, 2=Spoiler",
    )
    replacement_id: int | None = Field(
        None, description="Original image ID when marking as repost (required when status=-1)"
    )
    reason_category: int | None = Field(
        None, description="Deactivation reason (required when status=0): 1=Inappropriate, 2=Low Quality, 3=Spam, 4=Other"
    )
    reason: str | None = Field(
        None, max_length=1000, description="Free-text reason (required when status=0)"
    )
    locked: bool | None = Field(
        None, description="Lock comments on the image (True=locked, False=unlocked)"
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: int | None) -> int | None:
        if v is None:
            return v
        from app.config import ImageStatus

        settable = {
            ImageStatus.DEACTIVATED,
            ImageStatus.REVIEW,
            ImageStatus.REPOST,
            ImageStatus.ACTIVE,
            ImageStatus.SPOILER,
        }
        if v not in settable:
            raise ValueError(
                f"Invalid status: {v}. Must be one of: "
                "0=Deactivated, -4=Review, -1=Repost, 1=Active, 2=Spoiler"
            )
        return v

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return v.strip() or None

    @model_validator(mode="after")
    def validate_combination(self) -> ImageStatusUpdate:
        from app.config import DeactivationReason, ImageStatus

        if self.status is None and self.locked is None:
            raise ValueError("At least one of 'status' or 'locked' must be provided")

        if self.status == ImageStatus.DEACTIVATED:
            if self.reason_category not in DeactivationReason.VALID:
                raise ValueError("reason_category is required and must be valid when deactivating")
            if not self.reason:
                raise ValueError("reason is required when deactivating")
        else:
            # reason_category only applies to DEACTIVATED
            if self.reason_category is not None:
                raise ValueError("reason_category is only valid when status is Deactivated")
        return self
```

Extend `ImageStatusResponse`:

```python
class ImageStatusResponse(BaseModel):
    """Response schema for image status change."""

    image_id: int
    status: int
    locked: int
    replacement_id: int | None
    reason_category: int | None = None
    status_reason: str | None = None
    status_user_id: int | None
    status_updated: UTCDatetimeOptional = None

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Implement the handler refactor**

In `app/api/v1/admin.py`, replace the body of `change_image_status` (keep the decorator/signature/docstring) with a thin wrapper over the service:

```python
    result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = result.scalar_one_or_none()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    previous_status = image.status

    await change_image_status(
        db,
        image,
        current_user,
        new_status=status_data.status,
        reason_category=status_data.reason_category,
        reason=status_data.reason,
        replacement_id=status_data.replacement_id,
        locked=status_data.locked,
    )

    await db.commit()
    await db.refresh(image)

    if status_data.status is not None:
        await enqueue_r2_sync_on_status_change(
            image_id=image_id, old_status=previous_status, new_status=status_data.status,
        )
    if status_data.status == ImageStatus.REPOST and status_data.replacement_id:
        await schedule_rating_recalculation(status_data.replacement_id)

    return ImageStatusResponse.model_validate(image)
```

Add the import at the top of `admin.py` if not present: `from app.services.image_status import change_image_status, enqueue_r2_sync_on_status_change` (the module already imports `enqueue_r2_sync_on_status_change`; extend it).

- [ ] **Step 5: Run the tests**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_admin_images.py -q
```
Expected: PASS (new DEACTIVATED tests + existing spoiler/audit tests).

- [ ] **Step 6: Commit**

```bash
git add app/schemas/admin.py app/api/v1/admin.py tests/api/v1/test_admin_images.py
git commit -m "feat(status): deactivate dialog contract + route direct mod endpoint through service"
```

### Task 8: Reason visibility in the status-history endpoint

**Files:**
- Modify: `app/schemas/audit.py` (`ImageStatusHistoryResponse` 138-159).
- Modify: `app/api/v1/images.py` (`get_image_status_history` 1215-1299).

Rule: `reason_category` is always returned. `reason` text is returned only when the row's transition involves a public status (`old`/`new` in `VISIBLE_USER_STATUSES`) **OR** the requester is the image owner **OR** the requester has `IMAGE_EDIT`/`REVIEW_VIEW`. Otherwise `reason` is `None`.

- [ ] **Step 1: (Pre-investigated) the optional-auth dependency**

Confirmed during review: the endpoint must accept an optional current user via `get_optional_current_user` (`app/core/auth.py:113`), which is **already imported** in `app/api/v1/images.py:50`. No new dependency needs to be created.

- [ ] **Step 2: Write the failing tests**

`test_image_status_history_endpoint.py` currently seeds data directly via `db_session` and GETs the endpoint anonymously — it has no auth helpers. Add these helpers near the top of the file (the codebase duplicates such helpers per test file; this mirrors `test_admin_images.py`), then the three test cases:

```python
from sqlalchemy import select

from app.config import DeactivationReason
from app.core.security import get_password_hash
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups


async def _make_user(db_session, username, password="TestPassword123!"):
    user = Users(username=username, password=get_password_hash(password),
                 password_type="bcrypt", salt="", email=f"{username}@example.com", active=1)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _grant_image_edit(db_session, user_id):
    perm = (await db_session.execute(select(Perms).where(Perms.title == "image_edit"))).scalar_one_or_none()
    if not perm:
        perm = Perms(title="image_edit", desc="edit images")
        db_session.add(perm)
        await db_session.flush()
    group = Groups(title=f"hist_mod_{user_id}", desc="hist mod group")
    db_session.add(group)
    await db_session.flush()
    db_session.add(GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1))
    db_session.add(UserGroups(user_id=user_id, group_id=group.group_id))
    await db_session.commit()


async def _login(client, username, password="TestPassword123!"):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()["access_token"]


async def _seed_deactivation(db_session, owner):
    image = Images(filename="histvis", ext="jpg", md5_hash="a" * 32,
                   user_id=owner.user_id, width=100, height=100, filesize=1000,
                   status=ImageStatus.DEACTIVATED,
                   reason_category=DeactivationReason.LOW_QUALITY,
                   status_reason="blurry and low res")
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    db_session.add(ImageStatusHistory(
        image_id=image.image_id, old_status=ImageStatus.ACTIVE,
        new_status=ImageStatus.DEACTIVATED, user_id=owner.user_id,
        reason_category=DeactivationReason.LOW_QUALITY, reason="blurry and low res"))
    await db_session.commit()
    return image


@pytest.mark.api
class TestStatusHistoryReasonVisibility:
    async def test_reason_hidden_from_anonymous_for_deactivation(self, client, db_session):
        owner = await _make_user(db_session, "histowner1")
        image = await _seed_deactivation(db_session, owner)

        r = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert r.status_code == 200
        row = r.json()["items"][0]
        assert row["reason_category"] == DeactivationReason.LOW_QUALITY  # category always shown
        assert row["reason"] is None  # free-text hidden from anonymous on a hidden-status transition

    async def test_reason_visible_to_owner_and_mod(self, client, db_session):
        owner = await _make_user(db_session, "histowner2")
        image = await _seed_deactivation(db_session, owner)

        owner_token = await _login(client, owner.username)
        r = await client.get(f"/api/v1/images/{image.image_id}/status-history",
                             headers={"Authorization": f"Bearer {owner_token}"})
        assert r.json()["items"][0]["reason"] == "blurry and low res"

        mod = await _make_user(db_session, "histmod2")
        await _grant_image_edit(db_session, mod.user_id)
        mod_token = await _login(client, mod.username)
        r = await client.get(f"/api/v1/images/{image.image_id}/status-history",
                             headers={"Authorization": f"Bearer {mod_token}"})
        assert r.json()["items"][0]["reason"] == "blurry and low res"

    async def test_reason_public_for_spoiler_transition(self, client, db_session):
        owner = await _make_user(db_session, "histowner3")
        image = Images(filename="histspoil", ext="jpg", md5_hash="b" * 32,
                       user_id=owner.user_id, width=100, height=100, filesize=1000,
                       status=ImageStatus.SPOILER, status_reason="mild nudity")
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        db_session.add(ImageStatusHistory(
            image_id=image.image_id, old_status=ImageStatus.ACTIVE,
            new_status=ImageStatus.SPOILER, user_id=owner.user_id, reason="mild nudity"))
        await db_session.commit()

        r = await client.get(f"/api/v1/images/{image.image_id}/status-history")
        assert r.status_code == 200
        row = r.json()["items"][0]
        assert row["reason"] == "mild nudity"  # public: destination status is publicly visible
        assert row["reason_category"] is None
```

- [ ] **Step 3: Run to confirm failure**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_image_status_history_endpoint.py::TestStatusHistoryReasonVisibility -q
```
Expected: FAIL (`reason`/`reason_category` not in the response yet).

- [ ] **Step 4: Implement the response schema + handler**

In `app/schemas/audit.py`, add to `ImageStatusHistoryResponse` (after `new_status_label`):

```python
    reason_category: int | None = None
    reason: str | None = None
```

In `app/api/v1/images.py` `get_image_status_history`:
1. Add to the signature: `current_user: Annotated[Users | None, Depends(get_optional_current_user)] = None` (the dependency is already imported at line 50).
2. Extend the existing permissions import to add `has_any_permission`, e.g. `from app.core.permissions import Permission, has_permission, has_any_permission` (`Permission` is already imported; add only `has_any_permission`).
3. Change the existence check to **retain** the image object (it is currently discarded), then capture the owner id and compute one viewer flag:

```python
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    owner_id = image.user_id

    is_privileged_viewer = False
    if current_user is not None:
        is_privileged_viewer = current_user.user_id == owner_id or await has_any_permission(
            db, current_user.user_id, [Permission.IMAGE_EDIT, Permission.REVIEW_VIEW]
        )
```
4. Per row, the **free-text reason** is public only when the *destination* status is publicly visible (active/spoiler/repost) — this is deliberately NOT the OR-of-both rule used for actor identity, because the reason describes the resulting state (a deactivation reason must stay hidden even when the transition came from ACTIVE):

```python
        reason_is_public = history.new_status in ImageStatus.VISIBLE_USER_STATUSES
        can_see_reason = reason_is_public or is_privileged_viewer
        ...
        items.append(ImageStatusHistoryResponse(
            ...,
            reason_category=history.reason_category,  # always shown to everyone
            reason=history.reason if can_see_reason else None,
        ))
```
Leave the existing `show_user` actor-visibility predicate (old OR new in `VISIBLE_USER_STATUSES`) **unchanged** — only the reason uses the destination-only rule.

- [ ] **Step 5: Run the tests**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_image_status_history_endpoint.py -q
```
Expected: PASS.

- [ ] **Step 6: Full regression of touched areas**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_admin_images.py tests/api/v1/test_image_status_history_endpoint.py tests/api/v1/test_reports.py tests/api/v1/test_reviews.py tests/services/test_image_status_service.py tests/unit/test_config_status.py -q
```
Expected: PASS. `test_reports.py`/`test_reviews.py` must still pass unchanged — they exercise the not-yet-refactored `action_report`/`escalate`/`create_review`, proving Plan 1 left them working.

- [ ] **Step 7: Commit**

```bash
git add app/schemas/audit.py app/api/v1/images.py tests/api/v1/test_image_status_history_endpoint.py
git commit -m "feat(status): scope status-history reason visibility (category public, text owner/mod for hidden statuses)"
```

---

## Done criteria for Plan 1

- `DEACTIVATED = 0` exists (reusing the old OTHER/disable bucket; `OTHER` kept as a deprecated alias); legacy `-2/-3` still render labels in history but are no longer settable via the API.
- The direct mod `PATCH /admin/images/{id}` goes through `change_image_status()`; deactivation requires category + reason.
- `image_status_history` and `images` carry `reason`/`reason_category`; the history endpoint shows category to everyone and the reason text only to owner/mods for hidden transitions.
- The migration converted existing disabled images and backfilled the triage-gap history.
- `action_report`, `escalate_report`, `create_review`, and the owner `PATCH /images/{id}` are untouched and still pass their tests (handed to Plan 2).

## Follow-on (not this plan)

- **Plan 2 (reports & reviews):** route `action_report`/`escalate`/`create_review` through `change_image_status()`; update the triage "Mark as Low Quality/Inappropriate" actions to send `DEACTIVATED + reason_category` and a mod reason; store the resulting status + mod reason on the report; require a reason on review creation; remove `review_type`.
- **Plan 3 (frontend):** Deactivate dialog `{category, reason}`, triage capturing reason + showing the actual action, report dialog optional original-id, reviews UI, regenerate `api-generated.ts`.
- **Tech-debt noted, deferred:** remove the `ImageStatus.OTHER` deprecated alias and migrate the ~15 test references to `ImageStatus.DEACTIVATED`; the three independent public-status sets (`VISIBLE_USER_STATUSES`, `PUBLIC_IMAGE_STATUSES`, `PUBLIC_IMAGE_STATUSES_FOR_R2`) holding the same values in three modules; the dead `IMAGE_MARK_REPOST` permission.
