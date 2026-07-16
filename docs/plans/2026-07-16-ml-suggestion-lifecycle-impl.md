# ML Suggestion Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ML suggestion rows follow the image-status lifecycle — deleted when an image leaves suggestion-eligible status (reposts lose everything), re-seeded from the raw-prediction store on restore — fixing issue #274.

**Architecture:** A shared hook `sync_suggestions_for_status_transition()` (new module `app/services/ml_suggestion_lifecycle.py`) is called from both status-write sites (`change_image_status()` and the owner-facing `PATCH /images/{image_id}` path). Repost-specific cleanup (wipe-all + resolving the original's pending suggestions) lives inside `migrate_repost_data()`, which both repost paths share. `remap_image()` becomes flush-only so the restore re-seed runs inside the caller's transaction. Creation guards keep ineligible images row-free.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, Alembic, pytest (`make pytest`, isolated MariaDB).

## Global Constraints

- Spec of record: `../shuushuu-frontend/docs/plans/2026-07-16-ml-suggestion-lifecycle-and-history-design.md` (Part 1); decisions: `docs/adr/0001-ml-queue-write-time-invalidation.md`, `docs/adr/0002-suggestion-rows-follow-image-status.md`; vocabulary: `CONTEXT.md` (suggestion-eligible, system resolution).
- Suggestion-eligible statuses are exactly `{ImageStatus.ACTIVE, ImageStatus.SPOILER}` = `{1, 2}`. Real constants: `REVIEW=-4, LOW_QUALITY=-3, INAPPROPRIATE=-2, REPOST=-1, DEACTIVATED=0, ACTIVE=1, SPOILER=2` (`app/config.py:400-410`). Never invent status values.
- Feature branch `feat/ml-suggestion-lifecycle` off `main`. This repo has unrelated uncommitted WIP (`scripts/db_utils.py`, `scripts/prune_inactive_users.py`, `scripts/restore_prod_db.py`, old `docs/plans/*.md`) — **never `git add -A`**; stage only the files you touched.
- Lifecycle hooks are atomic with the status change: no try/except around them; flush-only, caller owns commit.
- Reviewer for repost-migration resolution is **NULL** (system resolution) — do not thread an actor into `migrate_repost_data`.
- Tests: `make pytest ARGS="<paths>"` from the repo root (starts the isolated DB on port 3316). If `pytest-db-up` fails with a container-name conflict but `docker ps` shows `shuushuu-mariadb-pytest` healthy, run pytest directly with the same env the Makefile sets (see `Makefile:124-132`).
- Run `uv run ruff check <files>` and `uv run mypy <files>` on every file you touch before committing; pre-commit runs ruff + ruff-format on staged files.
- Match the codebase's `# type: ignore[...]` idiom when mypy complains about SQLModel column comparisons — copy the style of neighboring queries; if mypy passes without one, leave it off.

---

### Task 1: Make `remap_image()` flush-only (callers own commits)

`remap_image()` currently commits (`app/services/ml_remap.py:86`). The restore hook (Task 3) must run inside the caller's transaction, so the commit moves out to the existing entry points.

**Files:**
- Modify: `app/services/ml_remap.py` (remap_image commit → flush; `remap_images_for_tag` loop commits per image)
- Modify: `scripts/ml_remap.py:79` area (commit after each `remap_image_from_store` call)
- Test: `tests/services/test_ml_remap.py` (existing suite is the safety net)

**Interfaces:**
- Produces: `remap_image(db, image_id, predictions, model_name) -> int` — unchanged signature, now flush-only.
- Later tasks rely on: calling `remap_image_from_store(db, image_id, model_name)` mid-transaction without a commit being issued.

- [ ] **Step 1: Change the commit to a flush**

In `app/services/ml_remap.py`, inside `remap_image()`, replace:

```python
    await db.commit()
```

with:

```python
    await db.flush()
```

Update the `remap_image` docstring: append the line `Flush-only; the caller owns the transaction and commit.`

- [ ] **Step 2: Make the two existing entry points commit**

In `app/services/ml_remap.py`, in `remap_images_for_tag()`, the per-image loop currently reads:

```python
    for image_id in image_ids:
        await remap_image_from_store(db, image_id, model_name)
```

Change to:

```python
    for image_id in image_ids:
        await remap_image_from_store(db, image_id, model_name)
        await db.commit()
```

In `scripts/ml_remap.py`, after `added = await remap_image_from_store(db, image_id, model_name)` (line 79), add on the next line at the same indentation:

```python
            await db.commit()
```

(Confirm indentation from the surrounding loop when editing.)

- [ ] **Step 3: Run the remap suite to prove behavior is preserved**

Run: `make pytest ARGS="tests/services/test_ml_remap.py"`
Expected: all tests PASS. The tests run inside the `db_session` SAVEPOINT fixture, so rows written by `remap_image` are visible via flush; if any test asserted on commit-specific behavior, fix the test's expectation, not the service.

- [ ] **Step 4: Lint, typecheck, commit**

Run: `uv run ruff check app/services/ml_remap.py scripts/ml_remap.py && uv run mypy app/services/ml_remap.py`
Expected: clean.

```bash
git add app/services/ml_remap.py scripts/ml_remap.py
git commit -m "refactor(ml): make remap_image flush-only; callers own commits"
```

---

### Task 2: `SUGGESTION_ELIGIBLE_STATUSES` + eligibility guard in `remap_image()`

**Files:**
- Modify: `app/config.py` (ImageStatus class, after line 415)
- Modify: `app/services/ml_remap.py` (guard at top of `remap_image`)
- Test: `tests/services/test_ml_remap.py`

**Interfaces:**
- Produces: `ImageStatus.SUGGESTION_ELIGIBLE_STATUSES: set[int]` — used by Tasks 3, 6.
- Produces: `remap_image()` returns 0 and creates nothing for ineligible or missing images.

- [ ] **Step 1: Write the failing test**

Append to `tests/services/test_ml_remap.py` (reuse the file's existing `_make_user`, `_make_image`, `_make_tags`, `_preds`, `_resolver_to_tag_ids`, `_resolver_passthrough`, `PIPELINE`, `CAFORMER` helpers):

```python
# ---------------------------------------------------------------------------
# Eligibility guard: ineligible images never get suggestion rows (ADR-0002)
# ---------------------------------------------------------------------------


async def test_remap_skips_suggestion_ineligible_image(db_session, monkeypatch):
    """remap_image returns 0 and writes nothing for an image outside
    SUGGESTION_ELIGIBLE_STATUSES (e.g. a repost)."""
    monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

    user = await _make_user(db_session, "inelig")
    image = await _make_image(db_session, user, "inelig")
    image.status = ImageStatus.REPOST
    await _make_tags(db_session, 301)
    await db_session.commit()

    predictions = _preds(301, model=CAFORMER)

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(predictions)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _resolver_passthrough),
    ):
        added = await remap_image(db_session, image.image_id, predictions, CAFORMER)

    assert added == 0
    rows = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
        )
    ).scalars().all()
    assert rows == []
```

Add `ImageStatus` to the file's existing `from app.config import settings` import: `from app.config import ImageStatus, settings`.

- [ ] **Step 2: Run test to verify it fails**

Run: `make pytest ARGS="tests/services/test_ml_remap.py::test_remap_skips_suggestion_ineligible_image"`
Expected: FAIL — `added == 1` (row created for the repost).

- [ ] **Step 3: Add the constant**

In `app/config.py`, inside `class ImageStatus`, directly after `VISIBLE_USER_STATUSES` (line 415) and before `LABELS`, add:

```python
    # Statuses where ML tag suggestion rows may exist and appear in review
    # surfaces (see CONTEXT.md "suggestion-eligible" and ADR-0002). Everything
    # else — REPOST, DEACTIVATED, REVIEW, legacy hidden — is ineligible.
    SUGGESTION_ELIGIBLE_STATUSES: set[int] = {ACTIVE, SPOILER}
```

- [ ] **Step 4: Add the guard**

In `app/services/ml_remap.py`, at the very top of `remap_image()` (before the `compute_implied_suggestions` call), add:

```python
    image = await db.get(Images, image_id)
    if image is None or image.status not in ImageStatus.SUGGESTION_ELIGIBLE_STATUSES:
        logger.info(
            "ml_remap_skipped_ineligible",
            image_id=image_id,
            status=None if image is None else image.status,
        )
        return 0
```

Add imports to `app/services/ml_remap.py`: `from app.config import ImageStatus` and `from app.models.image import Images` (merge into existing import blocks in sorted order).

- [ ] **Step 5: Run tests to verify pass**

Run: `make pytest ARGS="tests/services/test_ml_remap.py"`
Expected: all PASS (the guard's `db.get` reads the in-session image, so existing eligible-image tests are unaffected).

- [ ] **Step 6: Lint, typecheck, commit**

Run: `uv run ruff check app/config.py app/services/ml_remap.py tests/services/test_ml_remap.py && uv run mypy app/config.py app/services/ml_remap.py`

```bash
git add app/config.py app/services/ml_remap.py tests/services/test_ml_remap.py
git commit -m "feat(ml): suggestion-eligibility guard in remap_image + SUGGESTION_ELIGIBLE_STATUSES"
```

---

### Task 3: Lifecycle service `sync_suggestions_for_status_transition()`

**Files:**
- Create: `app/services/ml_suggestion_lifecycle.py`
- Create: `tests/services/test_ml_suggestion_lifecycle.py`

**Interfaces:**
- Consumes: `remap_image_from_store(db, image_id, model_name)` (Task 1, flush-only), `ImageStatus.SUGGESTION_ELIGIBLE_STATUSES` (Task 2).
- Produces: `async def sync_suggestions_for_status_transition(db: AsyncSession, image_id: int, old_status: int, new_status: int) -> None` — Tasks 4 and 5 call this.

- [ ] **Step 1: Write the failing tests**

Create `tests/services/test_ml_suggestion_lifecycle.py`:

```python
"""Tests for the suggestion-lifecycle hook (ADR-0002).

Pending rows are deleted when an image leaves suggestion-eligible status;
pending rows are re-seeded from the raw store when it returns. Reviewed rows
survive non-repost transitions. All against the real test DB.
"""

from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, settings
from app.models.image import Images
from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.user import Users
from app.services.ml_suggestion_lifecycle import sync_suggestions_for_status_transition

PIPELINE = "app.services.ml_suggestion_pipeline"
MODEL = "caformer_b36.dbv4-full"


async def _make_user(db: AsyncSession, suffix: str) -> Users:
    user = Users(
        username=f"lifecycle_{suffix}",
        email=f"lifecycle_{suffix}@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
        active=1,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_image(db: AsyncSession, user: Users, suffix: str, status: int) -> Images:
    image = Images(
        filename=f"2024-01-01-lifecycle-{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"lifecycle_hash_{suffix}",
        filesize=1024,
        width=800,
        height=600,
        status=status,
    )
    db.add(image)
    await db.flush()
    return image


async def _make_tag(db: AsyncSession, user: Users, suffix: str) -> Tags:
    tag = Tags(title=f"lifecycle tag {suffix}", type=1, user_id=user.user_id)
    db.add(tag)
    await db.flush()
    return tag


async def _make_suggestion(
    db: AsyncSession, image: Images, tag: Tags, status: str = "pending"
) -> MlTagSuggestions:
    suggestion = MlTagSuggestions(
        image_id=image.image_id,
        tag_id=tag.tag_id,
        confidence=0.9,
        model_version=MODEL,
        status=status,
    )
    db.add(suggestion)
    await db.flush()
    return suggestion


async def _suggestion_rows(db: AsyncSession, image_id: int) -> list[MlTagSuggestions]:
    return list(
        (
            await db.execute(
                select(MlTagSuggestions).where(MlTagSuggestions.image_id == image_id)
            )
        )
        .scalars()
        .all()
    )


async def _resolver_passthrough(db, suggestions):
    return suggestions


class TestEligibleToIneligible:
    async def test_deletes_pending_keeps_reviewed(self, db_session: AsyncSession):
        """ACTIVE -> DEACTIVATED deletes pending rows but keeps approved/rejected."""
        user = await _make_user(db_session, "e2i")
        image = await _make_image(db_session, user, "e2i", ImageStatus.ACTIVE)
        t1 = await _make_tag(db_session, user, "e2i_p")
        t2 = await _make_tag(db_session, user, "e2i_a")
        t3 = await _make_tag(db_session, user, "e2i_r")
        await _make_suggestion(db_session, image, t1, status="pending")
        await _make_suggestion(db_session, image, t2, status="approved")
        await _make_suggestion(db_session, image, t3, status="rejected")
        await db_session.commit()

        await sync_suggestions_for_status_transition(
            db_session, image.image_id, ImageStatus.ACTIVE, ImageStatus.DEACTIVATED
        )
        await db_session.commit()

        rows = await _suggestion_rows(db_session, image.image_id)
        statuses = sorted(r.status for r in rows)
        assert statuses == ["approved", "rejected"]


class TestNoOpTransitions:
    async def test_eligible_to_eligible_is_noop(self, db_session: AsyncSession):
        """ACTIVE -> SPOILER leaves pending rows alone."""
        user = await _make_user(db_session, "e2e")
        image = await _make_image(db_session, user, "e2e", ImageStatus.ACTIVE)
        tag = await _make_tag(db_session, user, "e2e")
        await _make_suggestion(db_session, image, tag, status="pending")
        await db_session.commit()

        await sync_suggestions_for_status_transition(
            db_session, image.image_id, ImageStatus.ACTIVE, ImageStatus.SPOILER
        )
        await db_session.commit()

        rows = await _suggestion_rows(db_session, image.image_id)
        assert len(rows) == 1
        assert rows[0].status == "pending"

    async def test_ineligible_to_ineligible_is_noop(self, db_session: AsyncSession):
        """DEACTIVATED -> REVIEW does not touch rows and does not re-seed."""
        user = await _make_user(db_session, "i2i")
        image = await _make_image(db_session, user, "i2i", ImageStatus.DEACTIVATED)
        tag = await _make_tag(db_session, user, "i2i")
        await _make_suggestion(db_session, image, tag, status="approved")
        await db_session.commit()

        await sync_suggestions_for_status_transition(
            db_session, image.image_id, ImageStatus.DEACTIVATED, ImageStatus.REVIEW
        )
        await db_session.commit()

        rows = await _suggestion_rows(db_session, image.image_id)
        assert len(rows) == 1
        assert rows[0].status == "approved"


class TestIneligibleToEligible:
    async def test_reseeds_pending_from_raw_store(
        self, db_session: AsyncSession, monkeypatch
    ):
        """DEACTIVATED -> ACTIVE re-seeds pending rows from ml_raw_predictions."""
        monkeypatch.setattr(settings, "ML_MODEL_NAME", MODEL)
        monkeypatch.setattr(settings, "ML_MIN_CONFIDENCE", 0.35)

        user = await _make_user(db_session, "i2e")
        image = await _make_image(db_session, user, "i2e", ImageStatus.ACTIVE)
        tag = await _make_tag(db_session, user, "i2e")

        # Seed the raw store: model -> external tag -> raw prediction.
        model_row = MlModels(name=MODEL)
        db_session.add(model_row)
        await db_session.flush()
        ext_tag = MlExternalTags(name="lifecycle_ext", category=0)
        db_session.add(ext_tag)
        await db_session.flush()
        db_session.add(
            MlRawPredictions(
                image_id=image.image_id,
                external_tag_id=ext_tag.id,
                model_id=model_row.id,
                confidence=0.9,
            )
        )
        await db_session.commit()

        # Patch resolvers so the external tag maps straight to our internal tag
        # (established pattern from tests/services/test_ml_remap.py).
        resolved = [{"tag_id": tag.tag_id, "confidence": 0.9, "model_version": MODEL}]

        async def _resolve_to_internal(db, suggestions):
            return [dict(r) for r in resolved]

        with (
            patch(f"{PIPELINE}.resolve_external_tags", _resolve_to_internal),
            patch(f"{PIPELINE}.resolve_tag_relationships", _resolver_passthrough),
        ):
            await sync_suggestions_for_status_transition(
                db_session, image.image_id, ImageStatus.DEACTIVATED, ImageStatus.ACTIVE
            )
        await db_session.commit()

        rows = await _suggestion_rows(db_session, image.image_id)
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].tag_id == tag.tag_id
```

Note: the re-seed test creates the image already in ACTIVE status because the lifecycle hook is always called *after* the caller has set the new status (the `remap_image` guard reads the in-session row).

- [ ] **Step 2: Run tests to verify they fail**

Run: `make pytest ARGS="tests/services/test_ml_suggestion_lifecycle.py"`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.ml_suggestion_lifecycle'`.

- [ ] **Step 3: Write the service**

Create `app/services/ml_suggestion_lifecycle.py`:

```python
"""Couple ML suggestion rows to the image-status lifecycle.

Pending suggestions exist only on suggestion-eligible images (ACTIVE, SPOILER —
see CONTEXT.md and ADR-0002). This module owns the transition hook called by
both status-write sites (change_image_status and the owner-facing image-update
endpoint). Repost-specific cleanup lives in app/services/repost.py.
"""

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, settings
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.services.ml_remap import remap_image_from_store


async def sync_suggestions_for_status_transition(
    db: AsyncSession,
    image_id: int,
    old_status: int,
    new_status: int,
) -> None:
    """Keep suggestion rows consistent with an image-status change.

    eligible -> ineligible: delete the image's pending rows. (Marking as REPOST
    additionally wipes reviewed rows — that lives in migrate_repost_data, which
    both repost paths share.)
    ineligible -> eligible: re-seed pending rows from the raw-prediction store
    (no inference; seeds nothing if the image has no raw predictions).

    Must be called AFTER the caller has assigned the new status to the
    in-session Images row. Flush-only; the caller owns the transaction.
    """
    was_eligible = old_status in ImageStatus.SUGGESTION_ELIGIBLE_STATUSES
    is_eligible = new_status in ImageStatus.SUGGESTION_ELIGIBLE_STATUSES
    if was_eligible == is_eligible:
        return

    if was_eligible:
        await db.execute(
            delete(MlTagSuggestions).where(
                MlTagSuggestions.image_id == image_id,  # type: ignore[arg-type]
                MlTagSuggestions.status == "pending",  # type: ignore[arg-type]
            )
        )
    else:
        await remap_image_from_store(db, image_id, settings.ML_MODEL_NAME)
```

(If mypy reports the `# type: ignore` comments as unused, remove them.)

- [ ] **Step 4: Run tests to verify pass**

Run: `make pytest ARGS="tests/services/test_ml_suggestion_lifecycle.py"`
Expected: 4 PASS.

- [ ] **Step 5: Lint, typecheck, commit**

Run: `uv run ruff check app/services/ml_suggestion_lifecycle.py tests/services/test_ml_suggestion_lifecycle.py && uv run mypy app/services/ml_suggestion_lifecycle.py`

```bash
git add app/services/ml_suggestion_lifecycle.py tests/services/test_ml_suggestion_lifecycle.py
git commit -m "feat(ml): suggestion lifecycle hook — delete pending on ineligible, re-seed on restore"
```

---

### Task 4: Wire the hook into both status-write sites

**Files:**
- Modify: `app/services/image_status.py` (`change_image_status`, after the status assignment ~line 140)
- Modify: `app/api/v1/images.py` (owner-facing update endpoint, after its status assignment ~line 1460)
- Test: `tests/services/test_image_status_service.py` (extend), `tests/api/v1/test_images.py` (extend)

**Interfaces:**
- Consumes: `sync_suggestions_for_status_transition(db, image_id, old_status, new_status)` (Task 3).

- [ ] **Step 1: Write the failing service-level test**

Append to `tests/services/test_image_status_service.py` (the file already has `_mk_image` and fetches the seeded actor via `select(Users).where(Users.user_id == 1)`; it already imports `ImageStatus` and `select`):

```python
async def test_status_change_syncs_ml_suggestions(db_session: AsyncSession):
    """Deactivating an image deletes its pending ML suggestions (ADR-0002)."""
    from app.models.ml_tag_suggestion import MlTagSuggestions
    from app.models.tag import Tags

    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)
    tag = Tags(title="lifecycle-hook-tag", type=1)
    db_session.add(tag)
    await db_session.flush()
    db_session.add(
        MlTagSuggestions(
            image_id=img.image_id,
            tag_id=tag.tag_id,
            confidence=0.9,
            model_version="test-model",
            status="pending",
        )
    )
    await db_session.commit()

    await change_image_status(
        db_session,
        img,
        actor,
        new_status=ImageStatus.DEACTIVATED,
        reason_category=DeactivationReason.OTHER,
        reason="lifecycle test",
    )
    await db_session.commit()

    remaining = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == img.image_id)
        )
    ).scalars().all()
    assert remaining == []
```

Check the file's existing deactivation tests for the exact `reason_category` value they pass (`DeactivationReason.OTHER` is the pattern; copy whatever the neighboring test uses if it differs).

- [ ] **Step 2: Run test to verify it fails**

Run: `make pytest ARGS="tests/services/test_image_status_service.py::test_status_change_syncs_ml_suggestions"`
Expected: FAIL — `remaining` has 1 pending row.

- [ ] **Step 3: Wire `change_image_status`**

In `app/services/image_status.py`, add the import (sorted into the existing block):

```python
from app.services.ml_suggestion_lifecycle import sync_suggestions_for_status_transition
```

Inside `change_image_status()`, directly after the block that assigns the new status:

```python
        image.status = new_status
        image.status_user_id = actor_id
        image.status_updated = datetime.now(UTC)
```

add:

```python
        # Keep ML suggestion rows consistent with the new status (ADR-0002).
        if new_status != previous_status:
            await sync_suggestions_for_status_transition(
                db, image.image_id, previous_status, new_status
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `make pytest ARGS="tests/services/test_image_status_service.py"`
Expected: all PASS (new test plus the pre-existing suite).

- [ ] **Step 5: Write the failing endpoint test for the owner path**

Append to `tests/api/v1/test_images.py` a new class next to `TestAddTagApprovesMlSuggestion` (same fixture style):

```python
@pytest.mark.api
class TestOwnerStatusChangeSyncsMlSuggestions:
    """The owner-facing PATCH /images/{id} status path runs the lifecycle hook."""

    async def test_owner_spoiler_marking_keeps_pending(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        sample_user: Users,
        sample_image_data: dict,
    ):
        """ACTIVE -> SPOILER is eligible -> eligible: pending rows survive."""
        image_data = sample_image_data.copy()
        image_data["user_id"] = sample_user.user_id
        image = Images(**image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        tag = Tags(title="owner_spoiler_ml_tag", type=1)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        suggestion = MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            confidence=0.9,
            model_version="test-model",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()
        await db_session.refresh(suggestion)

        response = await authenticated_client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"status": 2},
        )
        assert response.status_code == 200

        await db_session.refresh(suggestion)
        assert suggestion.status == "pending"
```

(The repost variant of the owner path is covered in Task 5, which owns the repost cleanup.)

- [ ] **Step 6: Run it before wiring**

Run: `make pytest ARGS="tests/api/v1/test_images.py::TestOwnerStatusChangeSyncsMlSuggestions"`
Expected: PASS even before Step 7 (eligible→eligible is a no-op with or without the hook). This test exists to pin the no-op contract so the Step 7 wiring can't over-delete; it must STILL pass after Step 7.

- [ ] **Step 7: Wire the owner path**

In `app/api/v1/images.py`, add the module-level import (sorted into the existing `app.services` block):

```python
from app.services.ml_suggestion_lifecycle import sync_suggestions_for_status_transition
```

In the owner-facing update endpoint, the status block currently ends with:

```python
        previous_status = image.status
        image.status = new_status
        image.status_user_id = current_user.id
        image.status_updated = datetime.now(UTC)
```

Add directly after:

```python
        # Keep ML suggestion rows consistent with the new status (ADR-0002).
        if new_status != previous_status:
            await sync_suggestions_for_status_transition(
                db, image_id, previous_status, new_status
            )
```

- [ ] **Step 8: Run both test files**

Run: `make pytest ARGS="tests/services/test_image_status_service.py tests/api/v1/test_images.py"`
Expected: all PASS.

- [ ] **Step 9: Lint, typecheck, commit**

Run: `uv run ruff check app/services/image_status.py app/api/v1/images.py tests/services/test_image_status_service.py tests/api/v1/test_images.py && uv run mypy app/services/image_status.py app/api/v1/images.py`

```bash
git add app/services/image_status.py app/api/v1/images.py tests/services/test_image_status_service.py tests/api/v1/test_images.py
git commit -m "feat(ml): run suggestion lifecycle hook at both image-status write sites"
```

---

### Task 5: Repost cleanup in `migrate_repost_data()`

**Files:**
- Modify: `app/services/repost.py`
- Test: `tests/services/test_ml_suggestion_lifecycle.py` (extend), `tests/api/v1/test_admin_images.py` (extend)

**Interfaces:**
- Consumes: `approve_pending_suggestions_for_links(db, links, user_id)` from `app/services/ml_suggestion_review.py` (exists on main).
- Produces: `migrate_repost_data(repost_id, original_id, db)` — unchanged signature; now also resolves the original's pending suggestions (reviewer NULL) and deletes ALL of the repost's suggestion rows.

- [ ] **Step 1: Write the failing service tests**

Append to `tests/services/test_ml_suggestion_lifecycle.py`:

```python
class TestMigrateRepostData:
    async def test_wipes_repost_rows_and_resolves_original(
        self, db_session: AsyncSession
    ):
        """Repost-marking wipes ALL the repost's suggestion rows and resolves the
        original's matching pending suggestion with a NULL reviewer."""
        from app.models.tag_link import TagLinks
        from app.services.repost import migrate_repost_data

        user = await _make_user(db_session, "repost")
        repost = await _make_image(db_session, user, "repost_r", ImageStatus.ACTIVE)
        original = await _make_image(db_session, user, "repost_o", ImageStatus.ACTIVE)
        shared_tag = await _make_tag(db_session, user, "repost_shared")

        # The repost carries the tag; the original has a pending suggestion for it.
        db_session.add(
            TagLinks(image_id=repost.image_id, tag_id=shared_tag.tag_id, user_id=user.user_id)
        )
        await _make_suggestion(db_session, repost, shared_tag, status="approved")
        original_pending = await _make_suggestion(
            db_session, original, shared_tag, status="pending"
        )
        await db_session.commit()

        await migrate_repost_data(repost.image_id, original.image_id, db_session)
        await db_session.commit()

        # Repost: every suggestion row gone.
        assert await _suggestion_rows(db_session, repost.image_id) == []

        # Original: pending resolved as a system resolution (NULL reviewer).
        await db_session.refresh(original_pending)
        assert original_pending.status == "approved"
        assert original_pending.reviewed_by_user_id is None
        assert original_pending.reviewed_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make pytest ARGS="tests/services/test_ml_suggestion_lifecycle.py::TestMigrateRepostData"`
Expected: FAIL — repost rows still present / original still pending.

- [ ] **Step 3: Implement in `migrate_repost_data`**

In `app/services/repost.py`, add imports (sorted into the existing blocks):

```python
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.services.ml_suggestion_review import approve_pending_suggestions_for_links
```

Inside `migrate_repost_data()`, after the tag-migration section's final statement (the `delete(TagLinks).where(TagLinks.image_id == repost_id)` execute) and before `refresh_images_tag_type_flags(...)`, add:

```python
    # --- ML suggestions ---
    # The migrated tags are now applied to the original: resolve its matching
    # pending suggestions. Reviewer stays NULL — this is data movement, not a
    # human review (system resolution; see CONTEXT.md and ADR-0001).
    original_tag_ids = (
        (
            await db.execute(
                select(TagLinks.tag_id).where(  # type: ignore[call-overload]
                    TagLinks.image_id == original_id
                )
            )
        )
        .scalars()
        .all()
    )
    await approve_pending_suggestions_for_links(
        db, [(original_id, tag_id) for tag_id in original_tag_ids], None
    )

    # A repost is permanently out of review scope: wipe ALL its suggestion rows,
    # matching the favorites/ratings/tags wipe above (ADR-0002).
    await db.execute(
        delete(MlTagSuggestions).where(
            MlTagSuggestions.image_id == repost_id  # type: ignore[arg-type]
        )
    )
```

(Resolving against ALL of the original's tag_links — not only the just-migrated ones — is deliberate: any pending suggestion matching an applied tag is stale by definition, so this also self-heals older drift on the original.)

- [ ] **Step 4: Run tests to verify pass**

Run: `make pytest ARGS="tests/services/test_ml_suggestion_lifecycle.py"`
Expected: all PASS.

- [ ] **Step 5: Endpoint-level test through the admin repost route**

Append to `tests/api/v1/test_admin_images.py` (reuse its `create_admin_user`, `grant_permission`, `create_test_image`, `login_user` helpers; check the file's existing repost test for the exact JSON shape — it PATCHes `/api/v1/admin/images/{id}` with `{"status": ImageStatus.REPOST, "replacement_id": ...}`):

```python
    async def test_repost_marking_cleans_ml_suggestions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Marking a repost via the admin endpoint wipes its suggestion rows."""
        from app.models.ml_tag_suggestion import MlTagSuggestions
        from app.models.tag import Tags

        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        original = await create_test_image(db_session, admin.user_id)
        repost = await create_test_image(db_session, admin.user_id)

        tag = Tags(title="admin_repost_ml_tag", type=1)
        db_session.add(tag)
        await db_session.flush()
        suggestion = MlTagSuggestions(
            image_id=repost.image_id,
            tag_id=tag.tag_id,
            confidence=0.9,
            model_version="test-model",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        token = await login_user(client, admin.username, admin_password)
        response = await client.patch(
            f"/api/v1/admin/images/{repost.image_id}",
            json={"status": ImageStatus.REPOST, "replacement_id": original.image_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        rows = (
            await db_session.execute(
                select(MlTagSuggestions).where(
                    MlTagSuggestions.image_id == repost.image_id
                )
            )
        ).scalars().all()
        assert rows == []
```

Place it inside the existing test class covering the PATCH endpoint; match its indentation and any fixture idioms (e.g. if `create_test_image` requires extra kwargs, copy from the neighboring repost test). Add `from sqlalchemy import select` to the file's imports if not already present.

- [ ] **Step 6: Run, then lint, typecheck, commit**

Run: `make pytest ARGS="tests/api/v1/test_admin_images.py tests/services/test_ml_suggestion_lifecycle.py"`
Expected: all PASS.

Run: `uv run ruff check app/services/repost.py tests/services/test_ml_suggestion_lifecycle.py tests/api/v1/test_admin_images.py && uv run mypy app/services/repost.py`

```bash
git add app/services/repost.py tests/services/test_ml_suggestion_lifecycle.py tests/api/v1/test_admin_images.py
git commit -m "feat(ml): repost marking wipes suggestion rows and resolves the original's pending"
```

---

### Task 6: Eligibility guard in the generation pipeline

**Files:**
- Modify: `app/services/ml_suggestion_pipeline.py` (`generate_and_store_suggestions`)
- Test: `tests/services/test_ml_suggestion_pipeline.py` (extend)

**Interfaces:**
- Consumes: `ImageStatus.SUGGESTION_ELIGIBLE_STATUSES` (Task 2).

- [ ] **Step 1: Write the failing test**

Append to `tests/services/test_ml_suggestion_pipeline.py` (reuse the file's existing image/user creation helpers — read its header first and copy the idiom; the test below assumes helpers equivalent to the lifecycle file's, adapt names accordingly):

```python
async def test_generate_skips_suggestion_ineligible_image(db_session):
    """generate_and_store_suggestions returns 0 for an ineligible image without
    touching the filesystem or the model (guard runs before file resolution)."""
    from app.config import ImageStatus
    from app.services.ml_suggestion_pipeline import generate_and_store_suggestions

    # Create a minimal image row in REPOST status using this file's helpers.
    user = await _make_user(db_session, "inelig")
    image = await _make_image(db_session, user, "inelig")
    image.status = ImageStatus.REPOST
    await db_session.commit()

    # ml_service is never reached: the guard returns before any use.
    created = await generate_and_store_suggestions(db_session, image, None)  # type: ignore[arg-type]

    assert created == 0
```

If the file's helper names differ (`_make_user`/`_make_image` are the convention in sibling ML test files), use the file's own.

- [ ] **Step 2: Run test to verify it fails**

Run: `make pytest ARGS="tests/services/test_ml_suggestion_pipeline.py::test_generate_skips_suggestion_ineligible_image"`
Expected: FAIL with `FileNotFoundError` (pipeline reaches the file check) or `AttributeError` on the `None` service.

- [ ] **Step 3: Add the guard**

In `app/services/ml_suggestion_pipeline.py`, in `generate_and_store_suggestions()`, directly after:

```python
    assert image.image_id is not None  # a persisted image always has an id
    image_id = image.image_id
```

add:

```python
    # Ineligible images (repost/deactivated/etc.) never get suggestion rows
    # (ADR-0002) — return before touching the filesystem or the model.
    if image.status not in ImageStatus.SUGGESTION_ELIGIBLE_STATUSES:
        logger.info(
            "ml_suggestion_pipeline_skipped_ineligible",
            image_id=image_id,
            status=image.status,
        )
        return 0
```

Add `ImageStatus` to the module's `from app.config import ...` line.

- [ ] **Step 4: Run the pipeline suite**

Run: `make pytest ARGS="tests/services/test_ml_suggestion_pipeline.py"`
Expected: all PASS (existing tests create ACTIVE-status images by default).

- [ ] **Step 5: Lint, typecheck, commit**

Run: `uv run ruff check app/services/ml_suggestion_pipeline.py tests/services/test_ml_suggestion_pipeline.py && uv run mypy app/services/ml_suggestion_pipeline.py`

```bash
git add app/services/ml_suggestion_pipeline.py tests/services/test_ml_suggestion_pipeline.py
git commit -m "feat(ml): skip suggestion generation for ineligible images"
```

---

### Task 7: Backfill migration

**Files:**
- Create: `alembic/versions/<generated>_backfill_delete_suggestions_for_ineligible_images.py`

**Interfaces:**
- Chains from current head `a61c09c0f331`.

- [ ] **Step 1: Generate the revision**

Run: `uv run alembic revision -m "backfill delete suggestions for ineligible images"`
Expected: new file under `alembic/versions/` with `down_revision = 'a61c09c0f331'` (verify; if another migration landed meanwhile, alembic fills the actual head).

- [ ] **Step 2: Write the migration**

Replace the generated stubs (keep the generated `revision`/`down_revision` identifiers):

```python
"""backfill delete suggestions for ineligible images

Data migration (ADR-0002): suggestion rows may exist only on
suggestion-eligible images (status ACTIVE=1, SPOILER=2). Reposts (-1) leave
review scope permanently and lose ALL rows (matching the favorites/ratings/
tags wipe at repost-marking); other ineligible statuses (DEACTIVATED=0,
legacy -2/-3, REVIEW=-4) lose only pending rows — reviewed rows keep
provenance for tags still applied to those images.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '<keep generated>'
down_revision: str | Sequence[str] | None = '<keep generated>'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DELETE s FROM ml_tag_suggestions s
        JOIN images i ON i.image_id = s.image_id
        WHERE i.status = -1
        """
    )
    op.execute(
        """
        DELETE s FROM ml_tag_suggestions s
        JOIN images i ON i.image_id = s.image_id
        WHERE i.status NOT IN (1, 2) AND i.status <> -1
          AND s.status = 'pending'
        """
    )


def downgrade() -> None:
    # Irreversible data migration: deleted rows are re-seedable from the
    # raw-prediction store on restore (ADR-0002). Intentionally a no-op.
    pass
```

- [ ] **Step 3: Verify migration linearity and syntax**

Run: `uv run alembic heads && uv run python -c "import ast,glob; [ast.parse(open(f).read()) for f in glob.glob('alembic/versions/*.py')]; print('ok')"`
Expected: exactly one head (the new revision), then `ok`.

(The migration executes against dev/test/prod via the normal `alembic upgrade head` in each deploy; dev application happens at rollout, not in this task.)

- [ ] **Step 4: Lint and commit**

Run: `uv run ruff check alembic/versions/`

```bash
git add alembic/versions/
git commit -m "feat(ml): backfill — delete suggestion rows stranded on ineligible images"
```

---

### Task 8: Full verification

- [ ] **Step 1: Full suite**

Run: `make pytest`
Expected: 0 failures (baseline was 2288 passed, 10 skipped).

- [ ] **Step 2: Repo-wide checks on touched files**

Run: `uv run ruff check app/ tests/services/test_ml_suggestion_lifecycle.py && uv run mypy app/services/ml_suggestion_lifecycle.py app/services/ml_remap.py app/services/repost.py app/services/image_status.py app/services/ml_suggestion_pipeline.py app/api/v1/images.py`
Expected: clean.

- [ ] **Step 3: App imports cleanly (no circular imports)**

Run: `uv run python -c "import app.main; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Push and open PR**

```bash
git push origin HEAD
gh pr create --base main --head feat/ml-suggestion-lifecycle \
  --title "feat(ml): suggestion rows follow the image-status lifecycle" \
  --body "Closes #274. See docs/adr/0002-suggestion-rows-follow-image-status.md and the design doc in shuushuu-frontend/docs/plans/2026-07-16-ml-suggestion-lifecycle-and-history-design.md."
```
