# Tag Suggestions for Missing Tags Reports

## Overview & Goals

**Problem:** Users currently report missing tags via free-form text, making it difficult for mods to quickly review and apply suggested tags. There's no tracking of who makes good suggestions for potential promotion to tagging roles.

**Solution:** Allow users to suggest specific tags from the database when reporting MISSING_TAGS. Mods can review and selectively approve suggestions with a single action. Track all suggestions permanently to identify helpful contributors.

**Key Requirements:**
- Tag suggestions supplement (not replace) free-form `reason_text` field
- Only available for MISSING_TAGS category (category 4)
- Mods can approve all, reject all, or cherry-pick individual suggestions
- Track acceptance/rejection per suggestion for contribution metrics
- Provide clear feedback when tags are skipped (already on image or invalid)
- Any authenticated user can submit tag suggestions
- Add optional admin notes field to all report dismissals

---

## Database Schema Changes

### New Table: `image_report_tag_suggestions`

```sql
CREATE TABLE image_report_tag_suggestions (
  suggestion_id INT PRIMARY KEY AUTO_INCREMENT,
  report_id INT NOT NULL,
  tag_id INT NOT NULL,
  accepted BOOLEAN NULL DEFAULT NULL,  -- NULL=pending, true=approved, false=rejected
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  FOREIGN KEY (report_id) REFERENCES image_reports(report_id) ON DELETE CASCADE,
  FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE,
  UNIQUE KEY unique_report_tag (report_id, tag_id),  -- Prevent duplicate tags per report
  INDEX idx_report_id (report_id),
  INDEX idx_tag_id (tag_id),
  INDEX idx_accepted (accepted)
);
```

### Modify Table: `image_reports`

```sql
ALTER TABLE image_reports
  ADD COLUMN admin_notes TEXT NULL;  -- Optional reason when dismissing/actioning reports
```

**Design Decisions:**
- `accepted` is nullable to distinguish "not yet reviewed" (NULL) from "explicitly rejected" (false)
- Unique constraint prevents users from suggesting the same tag twice in one report
- CASCADE delete ensures suggestions are cleaned up when reports are deleted
- `admin_notes` available for all report types, not just MISSING_TAGS

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
    suggested_tag_ids: list[int] | None = None  # Only used when category=4 (MISSING_TAGS)

    @field_validator("suggested_tag_ids")
    @classmethod
    def validate_suggested_tags(cls, v: list[int] | None, info) -> list[int] | None:
        # Only allow suggestions for MISSING_TAGS category
        if v is not None and info.data.get("category") != ReportCategory.MISSING_TAGS:
            raise ValueError("Tag suggestions only allowed for MISSING_TAGS reports")
        # Remove duplicates while preserving order
        if v:
            return list(dict.fromkeys(v))
        return v
```

**Response Schema Update:**

```python
class ReportResponse(BaseModel):
    # ... existing fields ...
    suggested_tags: list[TagSuggestion] | None = None
    skipped_tags: SkippedTagsInfo | None = None  # Feedback about filtered tags

class TagSuggestion(BaseModel):
    suggestion_id: int
    tag_id: int
    tag_name: str
    accepted: bool | None  # NULL=pending, true=approved, false=rejected

class SkippedTagsInfo(BaseModel):
    already_on_image: list[int] = []  # Tag IDs already applied to image
    invalid_tag_ids: list[int] = []   # Tag IDs that don't exist
```

---

## Admin Review Endpoints

### New Endpoint: Apply Tag Suggestions

```
POST /api/v1/admin/reports/{report_id}/apply-tag-suggestions
```

**Request:**

```python
class ApplyTagSuggestionsRequest(BaseModel):
    approved_suggestion_ids: list[int]  # Which suggestions to accept
    admin_notes: str | None = None  # Optional notes about the review
```

**Behavior:**
1. Verify report exists and is MISSING_TAGS category
2. Verify all `approved_suggestion_ids` belong to this report
3. Update suggestions table:
   - Set `accepted=true` for IDs in `approved_suggestion_ids`
   - Set `accepted=false` for all other suggestions on this report
4. Add approved tags to `tag_links` table (if not already present)
5. Update `image_reports`:
   - Set `status=REVIEWED`
   - Set `reviewed_by=current_admin_user_id`
   - Set `reviewed_at=now()`
   - Set `admin_notes` if provided
6. Return success response

**Response:**

```python
class ApplyTagSuggestionsResponse(BaseModel):
    message: str
    applied_tags: list[int]  # Tag IDs actually added to image
    already_present: list[int]  # Tag IDs that were already on image (skipped)
```

**Permissions:** Requires existing report review permission.

### Modified Endpoint: Dismiss Report

```
POST /api/v1/admin/reports/{report_id}/dismiss
```

**Request Schema Update:**

```python
class ReportDismissRequest(BaseModel):
    admin_notes: str | None = None  # Optional reason for dismissal
```

**Behavior Updates:**
1. Existing dismiss logic (mark report as dismissed)
2. **NEW:** If report has tag suggestions, set `accepted=false` for all suggestions
3. **NEW:** Save `admin_notes` if provided
4. Set `reviewed_by` and `reviewed_at` as usual

This applies to all report categories, not just MISSING_TAGS.

---

## Business Logic & Validation

### When creating a MISSING_TAGS report with tag suggestions:

1. **Tag Validation:**
   - Query database to verify which `suggested_tag_ids` exist in `tags` table
   - Separate into `valid_tags` and `invalid_tags`

2. **Duplication Check:**
   - Query `tag_links` table to find which `valid_tags` are already on the image
   - Separate into `new_tags` and `already_present_tags`

3. **Deduplication:**
   - Remove duplicates from user's submission (handled by validator)

4. **Saving:**
   - Create report in `image_reports` table
   - Create suggestion records in `image_report_tag_suggestions` for each tag in `new_tags`
   - Don't save suggestions for tags already on image or invalid tags

5. **Response Feedback:**
   - Return `skipped_tags` object showing:
     - `already_on_image`: IDs that were filtered because they exist on image
     - `invalid_tag_ids`: IDs that don't exist in database
   - Include `suggested_tags` array showing what was actually saved

### Edge Cases:
- If all suggested tags are invalid/already present: Still create the report, just with zero suggestions
- If `suggested_tag_ids` is empty array or null for MISSING_TAGS: Valid, user might only want to use `reason_text`
- If user suggests 100+ tags: No hard limit (for now), can add one later if abused

---

## Error Handling

### Report Creation Errors

| Scenario | HTTP Status | Response |
|----------|-------------|----------|
| Image not found | 404 | `{"detail": "Image not found"}` |
| User already has pending report for this image | 409 | `{"detail": "You already have a pending report for this image"}` |
| Tag suggestions provided for non-MISSING_TAGS category | 422 | `{"detail": "Tag suggestions only allowed for MISSING_TAGS reports"}` |
| Not authenticated | 401 | `{"detail": "Not authenticated"}` |

### Admin Apply Suggestions Errors

| Scenario | HTTP Status | Response |
|----------|-------------|----------|
| Report not found | 404 | `{"detail": "Report not found"}` |
| Report is not MISSING_TAGS category | 400 | `{"detail": "This report has no tag suggestions"}` |
| Report already reviewed | 400 | `{"detail": "Report has already been reviewed"}` |
| Suggestion ID doesn't belong to this report | 400 | `{"detail": "Invalid suggestion ID: {id}"}` |
| Missing permission | 403 | `{"detail": "Permission denied"}` |

---

## Files to Modify

### New Files
- `app/models/image_report_tag_suggestion.py` - New SQLModel for the suggestions table
- `alembic/versions/xxx_add_tag_suggestions.py` - Migration for new table and `admin_notes` column

### Modified Files
- `app/schemas/report.py` - Add `TagSuggestion`, `SkippedTagsInfo`, update `ReportCreate` and `ReportResponse`
- `app/api/v1/images.py` - Update `report_image()` to handle tag suggestions
- `app/api/v1/admin.py` - Add `apply_tag_suggestions()` endpoint, update dismiss to handle `admin_notes` and reject suggestions
- `app/models/__init__.py` - Export new model

### Test Files
- `tests/api/v1/test_images.py` - Tests for creating reports with tag suggestions
- `tests/api/v1/test_admin.py` - Tests for applying/dismissing tag suggestions

---

## Summary

**What we're building:**
- Users can suggest specific tags when reporting MISSING_TAGS, alongside optional notes
- Suggestions stored in `image_report_tag_suggestions` table for permanent contribution tracking
- Mods can approve all, reject all, or cherry-pick individual suggestions via single endpoint
- Approved tags immediately applied to image
- Users get feedback on skipped tags (already present or invalid)
- Optional `admin_notes` field added for all report dismissals/reviews

**Key design choices:**
- New table (not JSON) for contribution tracking and analytics
- Immediate application on approval (not staged)
- Graceful handling of invalid/duplicate tags with user feedback
- Reuse existing dismiss endpoint with auto-rejection of suggestions
