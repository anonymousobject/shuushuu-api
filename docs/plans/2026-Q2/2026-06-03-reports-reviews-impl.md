# Reports & Reviews Backend — Implementation Plan (Plan 2 of 3)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `change_image_status` the single entry point for every image status change (direct mod, report triage, review escalation/creation, and the review auto-close), so report-triage and review outcomes get identical validation, public history, and reason handling — then repurpose the review "type" into a mod-settable `reason_category`, require reasons on reviews, and surface each report's actual resolution.

**Architecture:** Plan 1 added `change_image_status` and routed only the direct mod endpoint through it. Plan 2 (a) extends that service to accept a system/no-mod actor plus the audit context (`action_type`, `report_id`, `review_id`, extra details) so every path can write one correctly-typed audit row through it; (b) rewrites `action_report` to use the service with the deactivate contract (`status` + `reason_category` + `reason` + `replacement_id`), fixing the repost-with-no-original, missing-history, and low-quality-asymmetry bugs and capturing the *mod's* reason (distinct from the reporter's `reason_text`); (c) derives each report's resolution for the triage UI from the `AdminActions` row it's already linked to (no new report columns, no duplicated reason); (d) repurposes `ImageReviews.review_type` into a mod-settable `reason_category` (reusing the `DeactivationReason` taxonomy) and requires a reason on both review-creation paths; (e) routes the review auto-close through the service so a "remove" outcome becomes `DEACTIVATED` + the review's `reason_category` + reason — the same flow as a direct deactivation.

**Tech Stack:** FastAPI, SQLModel, Pydantic v2, Alembic, MariaDB (aiomysql/pymysql), pytest + pytest-asyncio (auto), `uv`.

**Branch:** `report-review-resolution` (already created off the `image-moderation-redesign` umbrella, which contains Plan 1). PRs into the umbrella, never `main`.

**Design decisions locked with the maintainer (do not relitigate):**
- The reporter's reason (`report.reason_text`) and the mod's resolution reason are **separate** fields and are never conflated. The mod's reason lives with the status change (history + the one resolving audit row); the report keeps the reporter's reason.
- Report resolution ("what action was taken") is **derived** from the resolving `AdminActions` row (linked by `report_id`), not stored as a duplicate column. Accepted caveat: `admin_actions` is pruned after 2 years, so 2-year-old resolved reports lose the action detail from the card (the image's own status-history retains the transition + reason permanently).
- `ImageReviews.review_type` is **repurposed**, not removed: renamed to `reason_category`, using the `DeactivationReason` enum (1=Inappropriate, 2=Low Quality, 3=Spam, 4=Other), **mod-settable** at review creation/escalation. Existing `review_type=1` (Appropriateness) maps to `reason_category=1` (Inappropriate) — same int, compatible meaning, no data conversion.
- Reviews require **both** a `reason_category` and a free-text `reason` on creation and escalation.
- Review auto-close: KEEP → ACTIVE; REMOVE → DEACTIVATED + `reason_category` = the review's category + the review's reason — routed through `change_image_status` (system actor).
- Triage action contract mirrors the deactivate dialog: `{new_status, reason_category?, reason?, replacement_id?}`. Deactivation requires category + reason; repost requires `replacement_id` (now enforced on the triage path too).

**Scope (Plan 2):** `app/services/image_status.py`, `app/api/v1/admin.py` (`action_report`, `escalate_report`, `create_review`, `list_reports`/`get_report` response build), `app/services/review_jobs.py` (auto-close), `app/api/v1/images.py` (review label fn + public review response), `app/schemas/report.py`, `app/schemas/audit.py`, `app/models/image_review.py`, `app/config.py`, one Alembic migration, and the test files listed per task.
**Out of scope:** all frontend (Plan 3 — triage UI, report dialog, reviews UI, regenerated types).

---

## Pre-flight

- [ ] **Confirm branch + green baseline for the surfaces we touch:**

```bash
git -C /home/dtaylor/shuu/shuushuu-api branch --show-current   # expect: report-review-resolution
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_reports.py tests/api/v1/test_reviews.py tests/api/v1/test_image_reviews_endpoint.py tests/services/test_image_status_service.py -q
```
Expected: PASS (baseline before changes).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `app/services/image_status.py` | Modify | `change_image_status`: accept `actor: Users \| None`, `action_type`, `report_id`, `review_id`, `extra_details`; write one parameterized audit row. |
| `app/schemas/report.py` | Modify | `ReportActionRequest` (new contract + validation); `ReviewCreate`/`ReportEscalateRequest` (require `reason_category`+`reason`); `ReviewResponse` (`review_type`→`reason_category`); `ReportResponse` (resolution fields). |
| `app/schemas/audit.py` | Modify | `ImageReviewPublicResponse`: `review_type`→`reason_category`. |
| `app/api/v1/admin.py` | Modify | `action_report`, `escalate_report`, `create_review` route through the service; `list_reports`/`get_report` enrich resolution. |
| `app/api/v1/images.py` | Modify | review label fn + public-review response field. |
| `app/services/review_jobs.py` | Modify | auto-close routes through the service; REMOVE→DEACTIVATED+category. |
| `app/models/image_review.py` | Modify | rename `review_type`→`reason_category`; drop `ReviewType` import. |
| `app/config.py` | Modify | delete `ReviewType` class. |
| `alembic/versions/<hash>_*.py` | Create | rename `image_reviews.review_type`→`reason_category`. |
| test files (per task) | Modify | TDD + fix `review_type` construction/assertion sites. |

---

## Chunk 1: Unify the service as the single status-change entry point

### Task 1: Extend `change_image_status`

**Files:**
- Modify: `app/services/image_status.py` (the `change_image_status` function).
- Test: `tests/services/test_image_status_service.py`

The service currently hardcodes `actor.user_id` and `action_type=IMAGE_STATUS_CHANGE`. Plan 2 callers need a **system actor** (auto-close, `user_id=None`) and **context** (`REPORT_ACTION`/`REVIEW_START`/`REVIEW_CLOSE` with `report_id`/`review_id` and extra details). Make these parameters; defaults preserve Plan 1 behavior.

- [ ] **Step 1: Write the failing tests** (append to `tests/services/test_image_status_service.py`)

```python
async def test_system_actor_writes_null_user(db_session: AsyncSession):
    img = await _mk_image(db_session, 1)
    await change_image_status(db_session, img, None, new_status=ImageStatus.ACTIVE,
                              action_type=AdminActionType.REVIEW_CLOSE, review_id=None,
                              extra_details={"automatic": True})
    await db_session.commit()
    hist = (await db_session.execute(
        select(ImageStatusHistory).where(ImageStatusHistory.image_id == img.image_id)
    )).scalars().all()
    assert hist and hist[0].user_id is None
    action = (await db_session.execute(
        select(AdminActions).where(AdminActions.image_id == img.image_id)
    )).scalar_one()
    assert action.action_type == AdminActionType.REVIEW_CLOSE
    assert action.user_id is None
    assert action.details["automatic"] is True


async def test_report_id_stamped_on_audit_row(db_session: AsyncSession):
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)
    await change_image_status(db_session, img, actor, new_status=ImageStatus.DEACTIVATED,
                              reason_category=DeactivationReason.SPAM, reason="ad",
                              action_type=AdminActionType.REPORT_ACTION, report_id=4242)
    await db_session.commit()
    action = (await db_session.execute(
        select(AdminActions).where(AdminActions.report_id == 4242)
    )).scalar_one()
    assert action.action_type == AdminActionType.REPORT_ACTION
    assert action.details["reason"] == "ad"
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/services/test_image_status_service.py -k "system_actor or report_id_stamped" -q
```
Expected: FAIL (unexpected keyword args / `actor` None → AttributeError).

- [ ] **Step 3: Implement** — change the signature and the actor/audit handling in `change_image_status`:

Signature (add params; `actor` becomes nullable):
```python
async def change_image_status(
    db: AsyncSession,
    image: Images,
    actor: Users | None,
    *,
    new_status: int | None = None,
    reason_category: int | None = None,
    reason: str | None = None,
    replacement_id: int | None = None,
    locked: bool | None = None,
    action_type: int = AdminActionType.IMAGE_STATUS_CHANGE,
    report_id: int | None = None,
    review_id: int | None = None,
    extra_details: dict[str, object] | None = None,
) -> dict[str, int]:
```
Replace every `actor.user_id` with a local `actor_id` computed once after the `assert`:
```python
    assert image.image_id is not None  # caller passes a persisted image
    actor_id = actor.user_id if actor is not None else None
```
Use `actor_id` for `image.status_user_id`, the `ImageStatusHistory(user_id=actor_id, ...)`, and the audit row. Replace the audit row with the parameterized form:
```python
    db.add(
        AdminActions(
            user_id=actor_id,
            action_type=action_type,
            report_id=report_id,
            review_id=review_id,
            image_id=image.image_id,
            details={
                "previous_status": previous_status,
                "new_status": image.status,
                "previous_locked": previous_locked,
                "new_locked": image.locked,
                "replacement_id": image.replacement_id,
                "reason_category": image.reason_category,
                "reason": image.status_reason,
                **(extra_details or {}),
                **migration_result,
            },
        )
    )
```
(Existing Plan-1 callers pass no new args, so they still get `IMAGE_STATUS_CHANGE`, `user_id=actor.user_id`, no `report_id`/`review_id` — unchanged behavior.)

- [ ] **Step 4: Run to confirm pass** (whole service test file — existing Plan-1 tests must still pass)

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/services/test_image_status_service.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/image_status.py tests/services/test_image_status_service.py
git commit -m "feat(status): make change_image_status accept system actor + audit context"
```

---

## Chunk 2: Report action through the service + derived resolution

### Task 2: New `ReportActionRequest` contract + validation

**Files:**
- Modify: `app/schemas/report.py` (`ReportActionRequest`, `report.py:142-148`).
- Test: `tests/api/v1/test_reports.py`

Mirror Plan 1's `ImageStatusUpdate` validation so triage deactivations require category + reason. (Factor the shared rule by importing `DeactivationReason`; keep it a self-contained validator to avoid coupling the two schemas.)

- [ ] **Step 1: Write the failing test** (add to `test_reports.py`, in the action test class)

```python
async def test_action_deactivate_requires_category_and_reason(self, client, db_session):
    admin, password = await create_auth_user(db_session, username="admin", admin=True)
    await grant_permission(db_session, admin.user_id, "report_manage")
    token = await login_user(client, admin.username, password)
    image = await create_test_image(db_session, admin.user_id)
    report = ImageReports(image_id=image.image_id, user_id=admin.user_id, category=2,
                          status=ReportStatus.PENDING)
    db_session.add(report); await db_session.commit(); await db_session.refresh(report)

    r = await client.post(f"/api/v1/admin/reports/{report.report_id}/action",
                          json={"new_status": ImageStatus.DEACTIVATED},
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422  # category + reason required
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_reports.py -k action_deactivate_requires -q
```
Expected: FAIL (currently 200/400 — no such validation).

- [ ] **Step 3: Implement** — replace `ReportActionRequest`:
```python
class ReportActionRequest(BaseModel):
    """Schema for taking action on a report (mirrors the deactivate contract)."""

    new_status: int = Field(..., description="0=Deactivated, -1=Repost, 1=Active, 2=Spoiler")
    replacement_id: int | None = Field(None, description="Required when new_status=-1 (repost)")
    reason_category: int | None = Field(
        None, description="Required when new_status=0: 1=Inappropriate,2=Low Quality,3=Spam,4=Other"
    )
    reason: str | None = Field(None, max_length=1000, description="Required when new_status=0")

    @field_validator("new_status")
    @classmethod
    def validate_status(cls, v: int) -> int:
        from app.config import ImageStatus
        settable = {ImageStatus.DEACTIVATED, ImageStatus.REPOST, ImageStatus.ACTIVE, ImageStatus.SPOILER}
        if v not in settable:
            raise ValueError("new_status must be one of: 0=Deactivated, -1=Repost, 1=Active, 2=Spoiler")
        return v

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else v

    @model_validator(mode="after")
    def validate_combination(self) -> "ReportActionRequest":
        from app.config import DeactivationReason, ImageStatus
        if self.new_status == ImageStatus.DEACTIVATED:
            if self.reason_category not in DeactivationReason.VALID:
                raise ValueError("reason_category is required and must be valid when deactivating")
            if not self.reason:
                raise ValueError("reason is required when deactivating")
        elif self.reason_category is not None:
            raise ValueError("reason_category is only valid when deactivating")
        return self
```
Note: `REVIEW` is intentionally NOT settable here — escalation is a separate endpoint. Add `field_validator`/`model_validator` to the `report.py` imports if missing.

- [ ] **Step 4: Run to confirm pass**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_reports.py -k action_deactivate_requires -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/report.py tests/api/v1/test_reports.py
git commit -m "feat(reports): deactivate contract on ReportActionRequest"
```

### Task 3: Route `action_report` through the service

**Files:**
- Modify: `app/api/v1/admin.py` (`action_report`).
- Test: `tests/api/v1/test_reports.py`

- [ ] **Step 1: Write the failing tests** — (a) marking repost via triage now requires `replacement_id` and writes a history row; (b) deactivate-via-triage sets category + reason and writes a history row.

```python
async def test_action_repost_requires_replacement_and_writes_history(self, client, db_session):
    admin, password = await create_auth_user(db_session, username="admin", admin=True)
    await grant_permission(db_session, admin.user_id, "report_manage")
    token = await login_user(client, admin.username, password)
    image = await create_test_image(db_session, admin.user_id)
    report = ImageReports(image_id=image.image_id, user_id=admin.user_id, category=1,
                          status=ReportStatus.PENDING)
    db_session.add(report); await db_session.commit(); await db_session.refresh(report)
    # repost with no replacement_id -> 400 (previously silently succeeded)
    r = await client.post(f"/api/v1/admin/reports/{report.report_id}/action",
                          json={"new_status": ImageStatus.REPOST},
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400


async def test_action_deactivate_writes_history_with_reason(self, client, db_session):
    admin, password = await create_auth_user(db_session, username="admin", admin=True)
    await grant_permission(db_session, admin.user_id, "report_manage")
    token = await login_user(client, admin.username, password)
    image = await create_test_image(db_session, admin.user_id)
    report = ImageReports(image_id=image.image_id, user_id=admin.user_id, category=2,
                          status=ReportStatus.PENDING)
    db_session.add(report); await db_session.commit(); await db_session.refresh(report)
    iid = image.image_id
    r = await client.post(f"/api/v1/admin/reports/{report.report_id}/action",
                          json={"new_status": ImageStatus.DEACTIVATED,
                                "reason_category": DeactivationReason.LOW_QUALITY,
                                "reason": "too blurry"},
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    db_session.expire_all()
    img = (await db_session.execute(select(Images).where(Images.image_id == iid))).scalar_one()
    assert img.status == ImageStatus.DEACTIVATED
    assert img.reason_category == DeactivationReason.LOW_QUALITY
    hist = (await db_session.execute(
        select(ImageStatusHistory).where(ImageStatusHistory.image_id == iid)
    )).scalars().all()
    assert any(h.new_status == ImageStatus.DEACTIVATED and h.reason == "too blurry" for h in hist)
```
(Import `DeactivationReason` + `ImageStatusHistory` at the top of `test_reports.py` if absent.)

- [ ] **Step 2: Run to confirm failure**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_reports.py -k "action_repost_requires or action_deactivate_writes" -q
```
Expected: FAIL (repost currently succeeds with no replacement; no history row written by `action_report`).

- [ ] **Step 3: Implement** — replace the body of `action_report` after the report/image fetch + pending check (keep those). Replace the inline status mutation + audit with a service call:
```python
    previous_status = image.status

    await change_image_status(
        db,
        image,
        current_user,
        new_status=action_data.new_status,
        reason_category=action_data.reason_category,
        reason=action_data.reason,
        replacement_id=action_data.replacement_id,
        action_type=AdminActionType.REPORT_ACTION,
        report_id=report_id,
    )

    report.status = ReportStatus.REVIEWED
    report.reviewed_by = current_user.user_id
    report.reviewed_at = datetime.now(UTC)

    await db.commit()
    await enqueue_r2_sync_on_status_change(
        image_id=report.image_id, old_status=previous_status, new_status=action_data.new_status
    )
    if action_data.new_status == ImageStatus.REPOST and action_data.replacement_id:
        await schedule_rating_recalculation(action_data.replacement_id)
    return MessageResponse(message="Report processed and image status updated")
```
Use the same `change_image_status as apply_image_status_change` alias already imported in `admin.py` (Plan 1) — call `apply_image_status_change(...)`. Remove the now-unused inline `AdminActions(... REPORT_ACTION ...)` block. Confirm `schedule_rating_recalculation` is imported (it is, Plan 1).

- [ ] **Step 4: Update every existing test that posts a now-rejected `new_status` to `/reports/{id}/action`** (the new validator rejects `INAPPROPRIATE(-2)`/`LOW_QUALITY(-3)`/`REVIEW(-4)`):
- `tests/api/v1/test_reports.py::test_action_report_changes_image_status` — payload `{"new_status": INAPPROPRIATE}` → `{"new_status": DEACTIVATED, "reason_category": DeactivationReason.INAPPROPRIATE, "reason": "..."}`; assertion `image.status == INAPPROPRIATE` → `== DEACTIVATED`.
- `tests/api/v1/test_admin_actions.py::test_action_creates_audit_entry` (~:195) — same payload swap; assert `details["new_status"] == DEACTIVATED`.
- `tests/api/v1/test_reports.py::test_action_without_report_manage_permission` (~:1149) — change its payload to a VALID one (`DEACTIVATED` + category + reason) so it still deterministically exercises the 403 (otherwise body-validation 422 may preempt the permission check).

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_reports.py tests/api/v1/test_admin_actions.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/admin.py tests/api/v1/test_reports.py
git commit -m "feat(reports): route action_report through change_image_status (fixes repost/history/low-quality)"
```

### Task 4: Derive report resolution for the triage response

**Files:**
- Modify: `app/schemas/report.py` (`ReportResponse` — add resolution fields).
- Modify: `app/api/v1/admin.py` (`list_reports` + `get_report` — batch-fetch resolving `AdminActions`).
- Test: `tests/api/v1/test_reports.py`

The report row stores no resolution. Enrich the response from the `AdminActions` row linked by `report_id` (`REPORT_ACTION` → resulting status + reason; `REVIEW_START` → escalated; `REPORT_DISMISS` → dismissed).

- [ ] **Step 1: Write the failing test**

```python
async def test_resolved_report_exposes_action_and_reason(self, client, db_session):
    admin, password = await create_auth_user(db_session, username="admin", admin=True)
    await grant_permission(db_session, admin.user_id, "report_manage")
    await grant_permission(db_session, admin.user_id, "report_view")
    token = await login_user(client, admin.username, password)
    image = await create_test_image(db_session, admin.user_id)
    report = ImageReports(image_id=image.image_id, user_id=admin.user_id, category=2,
                          reason_text="i think this is AI", status=ReportStatus.PENDING)
    db_session.add(report); await db_session.commit(); await db_session.refresh(report)
    rid = report.report_id
    await client.post(f"/api/v1/admin/reports/{rid}/action",
                      json={"new_status": ImageStatus.DEACTIVATED,
                            "reason_category": DeactivationReason.LOW_QUALITY, "reason": "not AI, low quality"},
                      headers={"Authorization": f"Bearer {token}"})
    # fetch the single report
    r = await client.get(f"/api/v1/admin/reports/{rid}", headers={"Authorization": f"Bearer {token}"})
    data = r.json()
    assert data["reason_text"] == "i think this is AI"             # reporter's reason untouched
    assert data["resolution_status"] == ImageStatus.DEACTIVATED    # action taken
    assert data["resolution_reason"] == "not AI, low quality"      # mod's reason, distinct
```
(`get_report` route is `GET /api/v1/admin/reports/{id}`; confirm the exact path in `admin.py:1028`.)

- [ ] **Step 2: Run to confirm failure**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_reports.py -k resolved_report_exposes -q
```
Expected: FAIL (no `resolution_*` keys).

- [ ] **Step 3: Implement**

In `ReportResponse`, add computed fields (populated by the endpoint, not `model_post_init`):
```python
    resolution_status: int | None = None
    resolution_status_label: str | None = None
    resolution_reason: str | None = None
    resolution_kind: str | None = None  # "action" | "escalated" | "dismissed"
```
In `list_reports` and `get_report`, after building the `ReportResponse` objects for non-pending reports, batch-fetch the resolving actions and populate:
```python
    # Resolution lookup for reviewed/dismissed reports
    resolved_ids = [r.report_id for r in reports if r.status != ReportStatus.PENDING]
    actions_by_report: dict[int, AdminActions] = {}
    if resolved_ids:
        action_rows = (await db.execute(
            select(AdminActions)
            .where(AdminActions.report_id.in_(resolved_ids))  # type: ignore[union-attr]
            .where(AdminActions.action_type.in_([
                AdminActionType.REPORT_ACTION, AdminActionType.REVIEW_START, AdminActionType.REPORT_DISMISS,
            ]))
            .order_by(AdminActions.created_at.desc())  # type: ignore[union-attr]
        )).scalars().all()
        for a in action_rows:
            actions_by_report.setdefault(a.report_id, a)  # newest wins
    # ... per report, after constructing `response`:
        act = actions_by_report.get(report.report_id)
        if act is not None:
            if act.action_type == AdminActionType.REPORT_ACTION:
                ns = (act.details or {}).get("new_status")
                response.resolution_kind = "action"
                response.resolution_status = ns
                response.resolution_status_label = ImageStatus.get_label(ns) if ns is not None else None
                response.resolution_reason = (act.details or {}).get("reason")
            elif act.action_type == AdminActionType.REVIEW_START:
                response.resolution_kind = "escalated"
            elif act.action_type == AdminActionType.REPORT_DISMISS:
                response.resolution_kind = "dismissed"
```
Do the same in the single-report `get_report` handler (one report → one lookup). Import `ImageStatus` / `AdminActionType` in `admin.py` if not already (they are, Plan 1).

- [ ] **Step 4: Run to confirm pass**

```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_reports.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/report.py app/api/v1/admin.py tests/api/v1/test_reports.py
git commit -m "feat(reports): derive resolution (action/escalated/dismissed + mod reason) from audit log"
```

---

## Chunk 3: Reviews — repurpose type, require reasons

### Task 5: Rename `review_type` → `reason_category` (model + migration)

**Files:**
- Modify: `app/models/image_review.py` (rename field, drop `ReviewType` import).
- Modify: `app/config.py` (delete `ReviewType` class).
- Create: `alembic/versions/<hash>_review_reason_category.py`
- Test: `tests/unit/test_config_status.py` (a small model test)

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_config_status.py`)
```python
def test_review_uses_reason_category():
    from app.models.image_review import ImageReviews
    from app.config import DeactivationReason
    r = ImageReviews(image_id=1, reason_category=DeactivationReason.LOW_QUALITY)
    assert r.reason_category == DeactivationReason.LOW_QUALITY
    assert not hasattr(r, "review_type")
```

- [ ] **Step 2: Run to confirm failure**
```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/unit/test_config_status.py::test_review_uses_reason_category -q
```
Expected: FAIL.

- [ ] **Step 3: Implement**
- `app/models/image_review.py:41`: replace `review_type: int = Field(default=ReviewType.APPROPRIATENESS)` with `reason_category: int = Field(default=DeactivationReason.INAPPROPRIATE)`; change the import on line 26 from `ReviewType` to `DeactivationReason`.
- `app/config.py`: delete the `class ReviewType:` block (lines ~350-353).
- Generate the migration:
```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run alembic revision -m "review reason_category"
```
Edit `upgrade()`/`downgrade()` (chains off head `ba007b19d0f1`):
```python
def upgrade() -> None:
    """Rename image_reviews.review_type -> reason_category (values now DeactivationReason)."""
    op.execute("ALTER TABLE image_reviews CHANGE COLUMN review_type reason_category INT NOT NULL DEFAULT 1")

def downgrade() -> None:
    op.execute("ALTER TABLE image_reviews CHANGE COLUMN reason_category review_type INT NOT NULL DEFAULT 1")
```
(Existing value `1` is unchanged — was Appropriateness, now Inappropriate. No data conversion.)

- [ ] **Step 4: Run to confirm pass** (this also exercises the migration via session setup)
```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/unit/test_config_status.py -q && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head
```
Expected: PASS + clean round-trip. **Do NOT run the broader review suites yet — every `ImageReviews(review_type=…)` construction site is now broken; Task 6/7 fix them.**

- [ ] **Step 5: Commit**
```bash
git add app/models/image_review.py app/config.py alembic/versions/ tests/unit/test_config_status.py
git commit -m "feat(reviews): rename review_type -> reason_category (DeactivationReason taxonomy)"
```

### Task 6: Require `reason_category` + `reason`; route review creation through the service

**Files:**
- Modify: `app/schemas/report.py` (`ReviewCreate`, `ReportEscalateRequest`).
- Modify: `app/api/v1/admin.py` (`create_review`, `escalate_report`).
- Test: `tests/api/v1/test_reviews.py`

- [ ] **Step 1: Write the failing tests**
```python
async def test_create_review_requires_reason_and_category(self, client, db_session):
    admin, password = await create_auth_user(db_session, username="admin", admin=True)
    await grant_permission(db_session, admin.user_id, "review_start")
    token = await login_user(client, admin.username, password)
    image = await create_test_image(db_session, admin.user_id)
    r = await client.post(f"/api/v1/admin/images/{image.image_id}/review",
                          json={"deadline_days": 10},  # no reason/category
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422

async def test_create_review_stores_category_and_reason(self, client, db_session):
    admin, password = await create_auth_user(db_session, username="admin", admin=True)
    await grant_permission(db_session, admin.user_id, "review_start")
    token = await login_user(client, admin.username, password)
    image = await create_test_image(db_session, admin.user_id)
    r = await client.post(f"/api/v1/admin/images/{image.image_id}/review",
                          json={"reason_category": DeactivationReason.LOW_QUALITY,
                                "reason": "borderline quality"},
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201
    data = r.json()
    assert data["reason_category"] == DeactivationReason.LOW_QUALITY
    assert data["reason_category_label"] == "Low Quality"
    assert data["reason"] == "borderline quality"
```
(Add an analogous escalate test: `POST /reports/{id}/escalate` with `{reason_category, reason}` required → 422 without them.)

- [ ] **Step 2: Run to confirm failure**
```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_reviews.py -k "requires_reason_and_category or stores_category" -q
```
Expected: FAIL.

- [ ] **Step 3: Implement**
- `ReviewCreate`: make `reason` required (`Field(..., min_length=1, max_length=1000)`), add `reason_category: int = Field(...)` with a validator against `DeactivationReason.VALID`.
- `ReportEscalateRequest`: add the same required `reason` + `reason_category`.
- `create_review`: pass `reason_category` to the `ImageReviews(...)` constructor; create the review + `await db.flush()` to get `review_id`, then set the image to REVIEW **through the service**:
```python
    review = ImageReviews(image_id=image_id, initiated_by=current_user.user_id,
                          reason_category=review_data.reason_category, deadline=deadline,
                          status=ReviewStatus.OPEN, outcome=ReviewOutcome.PENDING,
                          reason=review_data.reason)
    db.add(review); await db.flush()
    await apply_image_status_change(db, image, current_user, new_status=ImageStatus.REVIEW,
                                    reason=review_data.reason, action_type=AdminActionType.REVIEW_START,
                                    review_id=review.review_id,
                                    extra_details={"deadline_days": deadline_days})
    await db.commit(); await db.refresh(review)
```
Remove the handler's inline `image.status = REVIEW`, the inline `ImageStatusHistory`, and the inline `AdminActions(REVIEW_START)` (the service now writes them). Keep the existing-open-review 409 check + R2 enqueue + `_build_user_summaries` response.
- `escalate_report`: same shape — create review with `reason_category=escalate_data.reason_category` (KEEP `source_report_id=report_id` on the constructor), flush, then `apply_image_status_change(..., new_status=REVIEW, reason=escalate_data.reason, action_type=REVIEW_START, review_id=review.review_id, report_id=report_id, ...)`; set the report fields; remove ONLY the inline status/history/audit writes.
- After both handlers drop `review_type=ReviewType.APPROPRIATENESS`, `ReviewType` is unused in `admin.py` — remove it from the `from app.config import (...)` block (`admin.py:32`) or CI's `ruff check` fails (F401).

- [ ] **Step 4: Run to confirm pass**
```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_reviews.py -q
```
Expected: PASS. (Update the existing `test_create_review_success` to include `reason_category` + `reason` in its payload.)

- [ ] **Step 5: Commit**
```bash
git add app/schemas/report.py app/api/v1/admin.py tests/api/v1/test_reviews.py
git commit -m "feat(reviews): require reason_category+reason; route review creation through the service"
```

### Task 7: Swap `review_type` for `reason_category` in responses + fix dependent tests

**Files:**
- Modify: `app/schemas/report.py` (`ReviewResponse`), `app/schemas/audit.py` (`ImageReviewPublicResponse`), `app/api/v1/images.py` (label fn + response build).
- Test: `tests/api/v1/test_image_reviews_endpoint.py`, `tests/unit/test_review_system.py`, `tests/api/v1/test_admin_actions.py`, `tests/integration/test_review_constraints.py`, `tests/api/v1/test_image_status_history.py`, `tests/unit/test_review_deadline_job.py`.

- [ ] **Step 1: Update response schemas + label fn**
- `report.py` `ReviewResponse`: replace `review_type: int` / `review_type_label: str | None` with `reason_category: int` / `reason_category_label: str | None`; in `model_post_init` replace the `type_labels` block with `self.reason_category_label = DeactivationReason.get_label(self.reason_category)`.
- `audit.py` `ImageReviewPublicResponse`: replace `review_type`/`review_type_label` with `reason_category`/`reason_category_label`.
- `images.py`: delete `get_review_type_label`; where it built the public response, pass `reason_category=review.reason_category, reason_category_label=DeactivationReason.get_label(review.reason_category)`; drop the `ReviewType` import (add `DeactivationReason`).

- [ ] **Step 2: Fix every `review_type` construction/assertion site** (the migration dropped the column, so these break)

```bash
cd /home/dtaylor/shuu/shuushuu-api && rg -n 'review_type' tests/
```
For each `ImageReviews(... review_type=ReviewType.APPROPRIATENESS ...)` or `review_type=1`, change the kwarg to `reason_category=DeactivationReason.INAPPROPRIATE`. For response assertions, change `item["review_type"]` → `item["reason_category"]` and `review_type_label == "appropriateness"`/`"Appropriateness"` → `reason_category_label == "Inappropriate"`. Update `tests/unit/test_review_system.py::test_review_type_values` to assert the `DeactivationReason` values (or delete it — `ReviewType` no longer exists). Replace `from app.config import ReviewType` with `DeactivationReason` in those test modules.

- [ ] **Step 3: Run the review suites**
```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/api/v1/test_image_reviews_endpoint.py tests/unit/test_review_system.py tests/api/v1/test_admin_actions.py tests/integration/test_review_constraints.py tests/api/v1/test_image_status_history.py tests/unit/test_review_deadline_job.py -q
```
Expected: PASS.

- [ ] **Step 4: Commit**
```bash
git add app/schemas/report.py app/schemas/audit.py app/api/v1/images.py tests/
git commit -m "feat(reviews): expose reason_category(+label) in responses; drop review_type"
```

---

## Chunk 4: Review auto-close through the service

### Task 8: Route BOTH review-close paths through the service; REMOVE → DEACTIVATED + category

There are **two** close paths and BOTH currently set the legacy `INAPPROPRIATE(-2)` on REMOVE outside the service:
- `app/services/review_jobs.py::_close_review` (deadline job / auto-close). Signature `(db, review, outcome, reason)` where `reason` is the close-CODE ("quorum_reached"/"early_close_margin"/"default_after_extension"). Audit `details` = `{outcome, outcome_label, reason:<close-code>, automatic}`. Writes a history row.
- `app/api/v1/admin.py::close_review` (`POST /reviews/{review_id}/close`, manual early-close, lines 2114-2214). Sets status inline (line 2172 = INAPPROPRIATE on REMOVE); audit `details` = `{outcome, vote_count, keep_votes, remove_votes, early_close}`; writes **no** history row today.

Both route through `change_image_status` (system actor `None` for the job; `current_user` for the manual endpoint). The mod's resolution reason is the **review's** `reason`; the deadline-job close-CODE moves to `extra_details["close_reason"]` so it isn't lost.

**Files:**
- Modify: `app/services/review_jobs.py` (`_close_review`), `app/api/v1/admin.py` (`close_review`).
- Test: `tests/unit/test_review_deadline_job.py`, `tests/api/v1/test_admin_actions.py`, `tests/integration/test_review_constraints.py`.

- [ ] **Step 1: Write the failing test** — REMOVE deactivates with the review's category + reason (write fully against the file's existing close-test harness; the helper `create_test_review` must pass `reason_category=` not `review_type=`):
```python
async def test_review_remove_deactivates_with_category(db_session):
    # image ACTIVE + open review (reason_category=LOW_QUALITY, reason="borderline");
    # drive _close_review(db, review, ReviewOutcome.REMOVE, "quorum_reached");
    # assert image.status == DEACTIVATED, image.reason_category == LOW_QUALITY,
    #        image.status_reason == "borderline",
    #        ImageStatusHistory row new_status=DEACTIVATED + reason_category=LOW_QUALITY,
    #        AdminActions REVIEW_CLOSE details["close_reason"] == "quorum_reached".
```

- [ ] **Step 2: Run to confirm failure**
```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/unit/test_review_deadline_job.py -k remove_deactivates -q
```
Expected: FAIL (currently sets INAPPROPRIATE(-2), no reason_category).

- [ ] **Step 3a: Implement `_close_review`** — replace the inline image-status block + the inline `AdminActions(REVIEW_CLOSE)` (lines ~204-237) with:
```python
    image = await db.get(Images, review.image_id)
    transition: tuple[int, int, int] | None = None
    if image:
        old_status = image.status
        if outcome == ReviewOutcome.KEEP:
            new_status, cat, why = ImageStatus.ACTIVE, None, None
        else:  # REMOVE
            new_status = ImageStatus.DEACTIVATED
            cat = review.reason_category
            why = review.reason or "Removed by community review"
        await change_image_status(
            db, image, None, new_status=new_status, reason_category=cat, reason=why,
            action_type=AdminActionType.REVIEW_CLOSE, review_id=review.review_id,
            extra_details={
                "outcome": outcome,
                "outcome_label": "keep" if outcome == ReviewOutcome.KEEP else "remove",
                "close_reason": reason,   # the close-CODE (was details["reason"])
                "automatic": True,
            },
        )
        transition = (review.image_id, old_status, new_status)
```
Keep the `review.status/outcome/closed_at` updates above it and the `return transition`. Import `change_image_status` + `AdminActionType` in `review_jobs.py`; drop `ImageStatusHistory` import if it becomes unused.

- [ ] **Step 3b: Implement `close_review` (manual endpoint)** — replace the inline `image.status=…`/`status_user_id`/`status_updated` (lines 2167-2174) and the inline `AdminActions(REVIEW_CLOSE)` (2177-2190) with a single service call (`apply_image_status_change` is the Plan-1 import alias):
```python
    previous_image_status = image.status
    if close_data.outcome == ReviewOutcome.KEEP:
        new_status, cat, why = ImageStatus.ACTIVE, None, None
    else:
        new_status = ImageStatus.DEACTIVATED
        cat, why = review.reason_category, (review.reason or "Removed by community review")
    await apply_image_status_change(
        db, image, current_user, new_status=new_status, reason_category=cat, reason=why,
        action_type=AdminActionType.REVIEW_CLOSE, review_id=review_id,
        extra_details={"outcome": close_data.outcome, "vote_count": vote_count,
                       "keep_votes": keep_votes, "remove_votes": remove_votes, "early_close": True},
    )
```
Keep the `review.status/outcome/closed_at/closed_by` updates, the vote-count query, R2 enqueue, and response build. (This newly adds a public history row for manual early-close — an intended consistency fix.)

- [ ] **Step 4: Update existing tests that asserted the legacy behavior**
- `tests/unit/test_review_deadline_job.py`: `test_unanimous_remove` (:182) and `test_majority_remove` (:212) — `image.status == INAPPROPRIATE` → `== DEACTIVATED` (+ assert `reason_category == DeactivationReason.INAPPROPRIATE` for default-category reviews). `test_close_creates_audit_log` (:385) — `details["reason"] == "quorum_reached"` → `details["close_reason"] == "quorum_reached"`. Fix the `create_test_review` helper (:64) to pass `reason_category=` not `review_type=`.
- Manual-close audit assertions in `test_admin_actions.py` keep working — `extra_details` preserves `vote_count`/`keep_votes`/`remove_votes`/`early_close`.

- [ ] **Step 5: Run to confirm pass**
```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run pytest tests/unit/test_review_deadline_job.py tests/api/v1/test_admin_actions.py tests/integration/test_review_constraints.py tests/api/v1/test_reviews.py -q
```
Expected: PASS.

- [ ] **Step 6: Commit**
```bash
git add app/services/review_jobs.py app/api/v1/admin.py tests/
git commit -m "feat(reviews): route both close paths through service; REMOVE -> DEACTIVATED + category"
```

---

## Done criteria for Plan 2

- Every image status change (direct mod, report action, review start, review close) goes through `change_image_status`; each writes exactly one correctly-typed audit row + a history row carrying reason/reason_category.
- `action_report` enforces `replacement_id` for repost, writes public history, requires category+reason for deactivation, and captures the mod's reason separately from the reporter's `reason_text`.
- The triage response exposes the real resolution (action + resulting status + mod reason, or escalated, or dismissed), derived from the audit log — no new report columns, no duplicated reason.
- Reviews carry a mod-set `reason_category` (DeactivationReason) + required reason; a REMOVE outcome deactivates with that category + reason, identical in shape to a direct deactivation.
- `review_type`/`ReviewType` are gone; responses expose `reason_category` + label.

## Final verification (after all tasks)
```bash
cd /home/dtaylor/shuu/shuushuu-api && uv run ruff check app/ && uv run mypy app/ && uv run pytest -q
```
Expected: ruff clean (no F401 from the removed `ReviewType`); mypy clean; full suite green. Then `/code-review` the branch and open a PR into `image-moderation-redesign`.

## Follow-on (Plan 3, frontend)
Deactivate dialog; triage UI sending the new action contract + showing the derived resolution; escalate/review dialogs sending `reason_category`+`reason`; reviews UI showing `reason_category_label`; regenerate `api-generated.ts`.
