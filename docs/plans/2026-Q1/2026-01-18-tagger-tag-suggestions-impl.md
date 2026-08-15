# Tagger Tag Suggestions Permission Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow users with `TAG_SUGGESTION_APPLY` permission to view and apply tag suggestions without full moderator permissions.

**Architecture:** Add new permission to Permission enum, create custom FastAPI dependencies that check for either `REPORT_VIEW/REPORT_MANAGE` OR `TAG_SUGGESTION_APPLY`, with automatic category filtering for taggers.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest

---

## Task 1: Add Permission Constant

**Files:**
- Modify: `app/core/permissions.py:68-77` (Permission enum, Report & Review section)
- Modify: `app/core/permissions.py:110-116` (PERMISSION_DESCRIPTIONS dict)

**Step 1: Add to Permission enum**

In `app/core/permissions.py`, add after line 72 (`REPORT_MANAGE`):

```python
    TAG_SUGGESTION_APPLY = "tag_suggestion_apply"
```

**Step 2: Add description**

In the `_PERMISSION_DESCRIPTIONS` dict, add after `Permission.REPORT_MANAGE` entry:

```python
    Permission.TAG_SUGGESTION_APPLY: "Apply or reject tag suggestions on TAG_SUGGESTIONS reports",
```

**Step 3: Verify**

Run: `python -c "from app.core.permissions import Permission; print(Permission.TAG_SUGGESTION_APPLY.description)"`
Expected: `Apply or reject tag suggestions on TAG_SUGGESTIONS reports`

**Step 4: Commit**

```bash
git add app/core/permissions.py
git commit -m "feat: add TAG_SUGGESTION_APPLY permission constant"
```

---

## Task 2: Write Tests for Tagger List Reports Access

**Files:**
- Modify: `tests/api/v1/test_reports.py`

**Step 1: Add test for tagger can list TAG_SUGGESTIONS reports**

Add to `TestAdminReportsList` class:

```python
    async def test_tagger_can_list_tag_suggestion_reports(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user with TAG_SUGGESTION_APPLY can list TAG_SUGGESTIONS reports."""
        tagger, password = await create_auth_user(db_session, username="tagger1")
        await grant_permission(db_session, tagger.user_id, "tag_suggestion_apply")
        image = await create_test_image(db_session, tagger.user_id)
        token = await login_user(client, tagger.username, password)

        # Create TAG_SUGGESTIONS report
        report = ImageReports(
            image_id=image.image_id,
            user_id=tagger.user_id,
            category=4,  # TAG_SUGGESTIONS
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()

        response = await client.get(
            "/api/v1/admin/reports",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        # All returned reports should be TAG_SUGGESTIONS
        for item in data["items"]:
            assert item["category"] == 4
```

**Step 2: Add test for tagger cannot list other categories**

```python
    async def test_tagger_cannot_list_other_report_categories(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user with only TAG_SUGGESTION_APPLY cannot request other categories."""
        tagger, password = await create_auth_user(db_session, username="tagger2")
        await grant_permission(db_session, tagger.user_id, "tag_suggestion_apply")
        token = await login_user(client, tagger.username, password)

        # Try to list REPOST reports
        response = await client.get(
            "/api/v1/admin/reports?category=1",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
```

**Step 3: Add test for tagger auto-filters to TAG_SUGGESTIONS**

```python
    async def test_tagger_auto_filters_to_tag_suggestions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test tagger without category param auto-filters to TAG_SUGGESTIONS."""
        tagger, password = await create_auth_user(db_session, username="tagger3")
        await grant_permission(db_session, tagger.user_id, "tag_suggestion_apply")
        image = await create_test_image(db_session, tagger.user_id)
        token = await login_user(client, tagger.username, password)

        # Create both types of reports
        repost_report = ImageReports(
            image_id=image.image_id,
            user_id=tagger.user_id,
            category=1,  # REPOST
            status=ReportStatus.PENDING,
        )
        tag_report = ImageReports(
            image_id=image.image_id,
            user_id=tagger.user_id,
            category=4,  # TAG_SUGGESTIONS
            status=ReportStatus.PENDING,
        )
        db_session.add(repost_report)
        db_session.add(tag_report)
        await db_session.commit()

        response = await client.get(
            "/api/v1/admin/reports",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        # Should only see TAG_SUGGESTIONS, not REPOST
        for item in data["items"]:
            assert item["category"] == 4
```

**Step 4: Run tests to verify they fail**

Run: `pytest tests/api/v1/test_reports.py::TestAdminReportsList::test_tagger_can_list_tag_suggestion_reports -v`
Expected: FAIL (403 because permission not implemented yet)

**Step 5: Commit**

```bash
git add tests/api/v1/test_reports.py
git commit -m "test: add tests for tagger list reports access"
```

---

## Task 3: Implement Tagger Access to List Reports

**Files:**
- Modify: `app/api/v1/admin.py:761-777`

**Step 1: Add category parameter and update permission check**

Replace the `list_reports` function signature and permission handling (lines 761-777):

```python
@router.get("/reports", response_model=ReportListResponse)
async def list_reports(
    current_user: Annotated[Users, Depends(get_current_user)],
    status_filter: Annotated[
        int | None,
        Query(alias="status", description="Filter by status (0=pending, 1=reviewed, 2=dismissed)"),
    ] = ReportStatus.PENDING,
    category: Annotated[
        int | None,
        Query(description="Filter by category (4=TAG_SUGGESTIONS)"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> ReportListResponse:
    """
    List image reports in the triage queue.

    Requires REPORT_VIEW permission OR TAG_SUGGESTION_APPLY permission.
    Users with only TAG_SUGGESTION_APPLY can only see TAG_SUGGESTIONS reports.
    """
    from app.core.permissions import has_permission

    has_report_view = await has_permission(
        db, current_user.user_id, Permission.REPORT_VIEW, redis_client
    )
    has_tag_suggestion_apply = await has_permission(
        db, current_user.user_id, Permission.TAG_SUGGESTION_APPLY, redis_client
    )

    if not has_report_view and not has_tag_suggestion_apply:
        raise HTTPException(status_code=403, detail="Permission denied")

    # Taggers can only see TAG_SUGGESTIONS
    if has_tag_suggestion_apply and not has_report_view:
        if category is not None and category != ReportCategory.TAG_SUGGESTIONS:
            raise HTTPException(
                status_code=403,
                detail="You can only view TAG_SUGGESTIONS reports",
            )
        category = ReportCategory.TAG_SUGGESTIONS
```

**Step 2: Update query to use category filter**

After the permission check, update the base query building (around line 778):

```python
    # Build a filtered base query against ImageReports (used for counting)
    base_query = select(ImageReports)
    if status_filter is not None:
        base_query = base_query.where(ImageReports.status == status_filter)  # type: ignore[arg-type]
    if category is not None:
        base_query = base_query.where(ImageReports.category == category)  # type: ignore[arg-type]
```

And similarly update the main query (around line 789-792):

```python
    query = select(ImageReports, Users.username)  # type: ignore[call-overload]
    query = query.join(Users, Users.user_id == ImageReports.user_id)
    if status_filter is not None:
        query = query.where(ImageReports.status == status_filter)
    if category is not None:
        query = query.where(ImageReports.category == category)  # type: ignore[arg-type]
```

**Step 3: Add imports at top of file**

Ensure these imports exist:

```python
import redis.asyncio as redis
from app.core.redis import get_redis
```

**Step 4: Run tests**

Run: `pytest tests/api/v1/test_reports.py::TestAdminReportsList -v`
Expected: All tagger tests PASS

**Step 5: Commit**

```bash
git add app/api/v1/admin.py
git commit -m "feat: allow taggers to list TAG_SUGGESTIONS reports"
```

---

## Task 4: Write Tests for Tagger Apply Tag Suggestions

**Files:**
- Modify: `tests/api/v1/test_reports.py`

**Step 1: Add test for tagger can apply tag suggestions**

Add to `TestApplyTagSuggestions` class:

```python
    async def test_tagger_can_apply_tag_suggestions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user with TAG_SUGGESTION_APPLY can apply tag suggestions."""
        tagger, password = await create_auth_user(db_session, username="tagger_apply1")
        await grant_permission(db_session, tagger.user_id, "tag_suggestion_apply")
        image = await create_test_image(db_session, tagger.user_id)
        tags = await create_test_tags(db_session, count=2)
        token = await login_user(client, tagger.username, password)

        # Create TAG_SUGGESTIONS report with suggestions
        report = ImageReports(
            image_id=image.image_id,
            user_id=tagger.user_id,
            category=4,  # TAG_SUGGESTIONS
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.flush()

        suggestion = ImageReportTagSuggestions(
            report_id=report.report_id,
            tag_id=tags[0].tag_id,
            suggestion_type=1,  # add
        )
        db_session.add(suggestion)
        await db_session.commit()
        await db_session.refresh(suggestion)

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
            json={"approved_suggestion_ids": [suggestion.suggestion_id]},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert tags[0].tag_id in data["applied_tags"]
```

**Step 2: Add test for tagger cannot apply to non-TAG_SUGGESTIONS report**

```python
    async def test_tagger_cannot_apply_to_non_tag_suggestions_report(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test tagger cannot apply tag suggestions to non-TAG_SUGGESTIONS report."""
        tagger, password = await create_auth_user(db_session, username="tagger_apply2")
        await grant_permission(db_session, tagger.user_id, "tag_suggestion_apply")
        image = await create_test_image(db_session, tagger.user_id)
        token = await login_user(client, tagger.username, password)

        # Create REPOST report
        report = ImageReports(
            image_id=image.image_id,
            user_id=tagger.user_id,
            category=1,  # REPOST
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/apply-tag-suggestions",
            json={"approved_suggestion_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
```

**Step 3: Add test for tagger cannot dismiss report**

Add to `TestReportPermissionDenials` class:

```python
    async def test_tagger_cannot_dismiss_report(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user with only TAG_SUGGESTION_APPLY cannot dismiss reports."""
        tagger, password = await create_auth_user(db_session, username="tagger_dismiss1")
        await grant_permission(db_session, tagger.user_id, "tag_suggestion_apply")
        image = await create_test_image(db_session, tagger.user_id)
        token = await login_user(client, tagger.username, password)

        report = ImageReports(
            image_id=image.image_id,
            user_id=tagger.user_id,
            category=4,  # TAG_SUGGESTIONS
            status=ReportStatus.PENDING,
        )
        db_session.add(report)
        await db_session.commit()

        response = await client.post(
            f"/api/v1/admin/reports/{report.report_id}/dismiss",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
```

**Step 4: Run tests to verify they fail**

Run: `pytest tests/api/v1/test_reports.py::TestApplyTagSuggestions::test_tagger_can_apply_tag_suggestions -v`
Expected: FAIL (403)

**Step 5: Commit**

```bash
git add tests/api/v1/test_reports.py
git commit -m "test: add tests for tagger apply tag suggestions"
```

---

## Task 5: Implement Tagger Access to Apply Tag Suggestions

**Files:**
- Modify: `app/api/v1/admin.py:905-922`

**Step 1: Update apply_tag_suggestions permission check**

Replace the function signature and add permission logic (lines 905-922):

```python
@router.post(
    "/reports/{report_id}/apply-tag-suggestions",
    response_model=ApplyTagSuggestionsResponse,
)
async def apply_tag_suggestions(
    report_id: Annotated[int, Path(description="Report ID")],
    request_data: ApplyTagSuggestionsRequest,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> ApplyTagSuggestionsResponse:
    """
    Apply tag suggestions from a TAG_SUGGESTIONS report.

    Approves specified suggestions, rejects others, adds approved tags
    to the image, and marks the report as reviewed.

    Requires REPORT_MANAGE permission OR TAG_SUGGESTION_APPLY permission.
    Users with only TAG_SUGGESTION_APPLY can only apply to TAG_SUGGESTIONS reports.
    """
    from app.core.permissions import has_permission

    has_report_manage = await has_permission(
        db, current_user.user_id, Permission.REPORT_MANAGE, redis_client
    )
    has_tag_suggestion_apply = await has_permission(
        db, current_user.user_id, Permission.TAG_SUGGESTION_APPLY, redis_client
    )

    if not has_report_manage and not has_tag_suggestion_apply:
        raise HTTPException(status_code=403, detail="Permission denied")

    # Get the report
    result = await db.execute(
        select(ImageReports).where(ImageReports.report_id == report_id)  # type: ignore[arg-type]
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status != ReportStatus.PENDING:
        raise HTTPException(status_code=400, detail="Report has already been processed")

    # Validate this is a TAG_SUGGESTIONS report
    if report.category != ReportCategory.TAG_SUGGESTIONS:
        # Taggers can only apply to TAG_SUGGESTIONS
        if has_tag_suggestion_apply and not has_report_manage:
            raise HTTPException(status_code=403, detail="Permission denied")
        raise HTTPException(
            status_code=400, detail="Tag suggestions can only be applied to TAG_SUGGESTIONS reports"
        )
```

**Step 2: Run tests**

Run: `pytest tests/api/v1/test_reports.py::TestApplyTagSuggestions -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add app/api/v1/admin.py
git commit -m "feat: allow taggers to apply tag suggestions"
```

---

## Task 6: Test Backward Compatibility

**Files:**
- Modify: `tests/api/v1/test_reports.py`

**Step 1: Add test for mod behavior unchanged**

Add to `TestAdminReportsList` class:

```python
    async def test_mod_with_report_view_sees_all_categories(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user with REPORT_VIEW can see all report categories."""
        mod, password = await create_auth_user(db_session, username="mod_compat1")
        await grant_permission(db_session, mod.user_id, "report_view")
        image = await create_test_image(db_session, mod.user_id)
        token = await login_user(client, mod.username, password)

        # Create reports of different categories
        repost_report = ImageReports(
            image_id=image.image_id,
            user_id=mod.user_id,
            category=1,  # REPOST
            status=ReportStatus.PENDING,
        )
        tag_report = ImageReports(
            image_id=image.image_id,
            user_id=mod.user_id,
            category=4,  # TAG_SUGGESTIONS
            status=ReportStatus.PENDING,
        )
        db_session.add(repost_report)
        db_session.add(tag_report)
        await db_session.commit()

        # Without category filter, mod sees all
        response = await client.get(
            "/api/v1/admin/reports",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        categories = {item["category"] for item in data["items"]}
        assert 1 in categories  # REPOST
        assert 4 in categories  # TAG_SUGGESTIONS
```

**Step 2: Run full test suite**

Run: `pytest tests/api/v1/test_reports.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add tests/api/v1/test_reports.py
git commit -m "test: add backward compatibility test for mod access"
```

---

## Task 7: Run Type Checks and Lint

**Step 1: Run mypy**

Run: `mypy app/ --no-error-summary`
Expected: No errors

**Step 2: Run ruff**

Run: `ruff check app/`
Expected: No errors

**Step 3: Fix any issues found**

If issues found, fix and commit:

```bash
git add -A
git commit -m "fix: address type and lint issues"
```

---

## Task 8: Final Integration Test

**Step 1: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 2: Sync permissions to database**

Run: `python -c "from app.core.permission_sync import sync_permissions; import asyncio; asyncio.run(sync_permissions())"`

This inserts the new `TAG_SUGGESTION_APPLY` permission into the database.

---

## Summary

| Task | Description |
|------|-------------|
| 1 | Add `TAG_SUGGESTION_APPLY` permission constant |
| 2 | Write tests for tagger list reports access |
| 3 | Implement tagger access to list reports |
| 4 | Write tests for tagger apply tag suggestions |
| 5 | Implement tagger access to apply tag suggestions |
| 6 | Test backward compatibility |
| 7 | Run type checks and lint |
| 8 | Final integration test |
