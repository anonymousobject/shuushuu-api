# Tag Removal Suggestions for Reports

## Overview & Goals

**Problem:** Users can currently suggest tags to add when reporting missing tags, but cannot suggest tags that should be removed. Incorrect or inappropriate tags require free-form text explanations that moderators must interpret manually.

**Solution:** Extend the existing tag suggestions system to support both additions and removals. Users can suggest specific tags to remove alongside tags to add, and moderators can approve/reject each suggestion with a single action.

**Key Requirements:**
- Support both tag additions and removals in the same report
- Rename MISSING_TAGS category to TAG_SUGGESTIONS (value stays 4)
- Add `suggestion_type` column to distinguish additions from removals
- Validate removals against tags currently on the image
- Unified admin approval workflow handles both types
- Clear feedback when tags are skipped (not on image for removals, already on image for additions)

---

## Database Schema Changes

### Modify Table: `image_report_tag_suggestions`

```sql
ALTER TABLE image_report_tag_suggestions
  ADD COLUMN suggestion_type TINYINT UNSIGNED NOT NULL DEFAULT 1;
```

Values:
- `1` = add (default, matches all existing rows)
- `2` = remove

**Design Decisions:**
- Single column addition minimizes migration risk to existing data
- Default value of 1 ensures backward compatibility with existing suggestions
- Using TINYINT for efficiency (only 2 values needed)

---

## API Changes

### Modified Endpoint: Create Report

```
POST /api/v1/images/{image_id}/report
```

**Request Schema Update:**

```python
class ReportCreate(BaseModel):
    category: int
    reason_text: str | None = None
    suggested_tag_ids_add: list[int] | None = None     # Tags to add
    suggested_tag_ids_remove: list[int] | None = None  # Tags to remove
```

Both fields are only valid when `category == TAG_SUGGESTIONS (4)`.

**Response Schema Update:**

```python
class TagSuggestion(BaseModel):
    suggestion_id: int
    tag_id: int
    tag_name: str
    tag_type: int | None = None
    suggestion_type: int  # 1 = add, 2 = remove
    accepted: bool | None  # NULL=pending, true=approved, false=rejected

class SkippedTagsInfo(BaseModel):
    already_on_image: list[int] = []    # Addition skipped: tag already present
    not_on_image: list[int] = []        # Removal skipped: tag not present
    invalid_tag_ids: list[int] = []     # Tag ID doesn't exist
```

The `suggested_tags` list in `ReportResponse` now includes `suggestion_type` for each suggestion.

---

## Admin Review Endpoint

### Modified Endpoint: Apply Tag Suggestions

```
POST /api/v1/admin/reports/{report_id}/apply-tag-suggestions
```

**Request:** (unchanged)

```python
class ApplyTagSuggestionsRequest(BaseModel):
    approved_suggestion_ids: list[int]
    admin_notes: str | None = None
```

**Updated Behavior:**

For each suggestion in the report:
1. If `suggestion_id` in `approved_suggestion_ids`:
   - Mark `accepted=True`
   - If `suggestion_type=1` (add): Create `TagLinks` entry (existing behavior)
   - If `suggestion_type=2` (remove): Delete the `TagLinks` entry for that image/tag
2. Else:
   - Mark `accepted=False`

**Response Schema Update:**

```python
class ApplyTagSuggestionsResponse(BaseModel):
    message: str
    applied_tags: list[int]     # Tags that were added
    removed_tags: list[int]     # Tags that were removed
    already_present: list[int]  # Additions skipped (already on image)
    already_absent: list[int]   # Removals skipped (not on image)
```

---

## Business Logic & Validation

### When creating a TAG_SUGGESTIONS report:

**For `suggested_tag_ids_add`** (existing behavior):
1. Validate each tag exists in database
2. Skip tags already on the image
3. Create suggestion with `suggestion_type=1`

**For `suggested_tag_ids_remove`** (new):
1. Validate each tag exists in database
2. Skip tags not currently on the image (nothing to remove)
3. Create suggestion with `suggestion_type=2`

### Edge Cases:
- Tag removed between report creation and admin review: `already_absent` in response
- Tag added between report creation and admin review: `already_present` in response
- Both add and remove same tag in one report: Allowed (user might be confused, but valid)
- Empty lists for both add/remove: Valid, user might only want to use `reason_text`

---

## Category Rename

In `app/models/image_report.py`:

```python
# Before
MISSING_TAGS = 4

# After
TAG_SUGGESTIONS = 4
```

The numeric value stays the same for backward compatibility with existing data.

---

## Files to Modify

### Migration
- `alembic/versions/xxx_add_suggestion_type.py` - Add `suggestion_type` column with default 1

### Models
- `app/models/image_report_tag_suggestion.py` - Add `suggestion_type` column
- `app/models/image_report.py` - Rename `MISSING_TAGS` to `TAG_SUGGESTIONS`

### Schemas
- `app/schemas/report.py`:
  - Update `ReportCreate` with `suggested_tag_ids_add` and `suggested_tag_ids_remove`
  - Update `TagSuggestion` with `suggestion_type`
  - Update `SkippedTagsInfo` with `not_on_image`
  - Update `ApplyTagSuggestionsResponse` with `removed_tags` and `already_absent`

### API Endpoints
- `app/api/v1/images.py` - Update `report_image()` to handle removal suggestions
- `app/api/v1/admin.py`:
  - Update `apply_tag_suggestions()` to handle removals (delete TagLinks)
  - Update `list_reports()` to include `suggestion_type` in response

### Tests
- `tests/api/v1/test_images.py` - Tests for creating reports with removal suggestions
- `tests/api/v1/test_admin.py` - Tests for approving removal suggestions

---

## Summary

| Component | Change |
|-----------|--------|
| Database | Add `suggestion_type` column (1=add, 2=remove) |
| Category | Rename `MISSING_TAGS` → `TAG_SUGGESTIONS` |
| Create report | Accept `suggested_tag_ids_add` and `suggested_tag_ids_remove` |
| Validation | Additions: tag not on image. Removals: tag on image. |
| Response | Single `suggested_tags` list with `suggestion_type` field |
| Admin apply | Check type, add or remove `TagLinks` accordingly |
| Admin response | Add `removed_tags` and `already_absent` fields |
