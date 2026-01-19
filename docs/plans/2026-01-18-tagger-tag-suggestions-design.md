# Tag Suggestion Apply Permission for Image Taggers

## Overview & Goals

**Problem:** Currently, only users with `REPORT_MANAGE` permission (typically moderators) can apply tag suggestions. Image taggers, who have domain expertise in tagging, cannot help process TAG_SUGGESTIONS reports even though this is directly within their competency.

**Solution:** Add a new permission `TAG_SUGGESTION_APPLY` that allows image taggers to:
- View TAG_SUGGESTIONS reports (filtered view of report queue)
- Apply or reject tag suggestions on those reports

**What taggers CAN do:**
- View reports where `category == TAG_SUGGESTIONS`
- Approve/reject individual tag suggestions via `apply_tag_suggestions` endpoint

**What taggers CANNOT do:**
- View other report categories (REPOST, INAPPROPRIATE, SPAM, etc.)
- Dismiss reports (only apply the suggestions)
- Take image actions (change status, escalate to review)
- Access any other moderation functions

**Key Constraints:**
- Backward compatible: `REPORT_MANAGE` continues to work as before
- Mods see TAG_SUGGESTIONS in their normal queue (no change)
- Taggers get a filtered view showing only TAG_SUGGESTIONS

---

## Permission & API Changes

**New Permission:**

```python
# In app/core/permissions.py
TAG_SUGGESTION_APPLY = "tag_suggestion_apply"

# Description:
"Apply or reject tag suggestions on TAG_SUGGESTIONS reports"
```

**Endpoint Changes:**

| Endpoint | Current Permission | New Permission |
|----------|-------------------|----------------|
| `GET /admin/reports` | `REPORT_VIEW` | `REPORT_VIEW` OR `TAG_SUGGESTION_APPLY`* |
| `POST /admin/reports/{id}/apply-tag-suggestions` | `REPORT_MANAGE` | `REPORT_MANAGE` OR `TAG_SUGGESTION_APPLY` |
| `POST /admin/reports/{id}/dismiss` | `REPORT_MANAGE` | No change (mods only) |
| `POST /admin/reports/{id}/action` | `REPORT_MANAGE` | No change (mods only) |

*When user has only `TAG_SUGGESTION_APPLY` (not `REPORT_VIEW`), the list is automatically filtered to `category=TAG_SUGGESTIONS`.

**Authorization Logic for `apply-tag-suggestions`:**
- If user has `REPORT_MANAGE`: allow (existing behavior)
- If user has `TAG_SUGGESTION_APPLY`: allow only if report is TAG_SUGGESTIONS category
- Otherwise: 403 Forbidden

---

## Implementation Details

**Files to Modify:**

### 1. `app/core/permissions.py`
- Add `TAG_SUGGESTION_APPLY = "tag_suggestion_apply"` to Permission enum
- Add description to PERMISSION_DESCRIPTIONS

### 2. `app/api/v1/admin.py`

**`list_reports()` (~line 763):**
- Change from `require_permission(REPORT_VIEW)` to custom dependency
- Custom dependency checks: has `REPORT_VIEW` OR `TAG_SUGGESTION_APPLY`
- If only `TAG_SUGGESTION_APPLY`: force `category` filter to `TAG_SUGGESTIONS`
- If only `TAG_SUGGESTION_APPLY` and user requests different category: 403

**`apply_tag_suggestions()` (~line 912):**
- Change from `require_permission(REPORT_MANAGE)` to custom dependency
- Check: has `REPORT_MANAGE` OR (`TAG_SUGGESTION_APPLY` AND report.category == TAG_SUGGESTIONS)

**No changes to:** `dismiss_report()`, `action_report()`, `escalate_to_review()`

### 3. Database Migration
- Run permission sync to insert new permission into `permissions` table
- Assign `TAG_SUGGESTION_APPLY` to "Image Taggers" group (manual or migration)

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Tagger calls `list_reports` without category filter | Auto-filter to TAG_SUGGESTIONS |
| Tagger calls `list_reports` with `category=TAG_SUGGESTIONS` | Works normally |
| Tagger calls `list_reports` with `category=REPOST` | 403 Forbidden |
| Tagger calls `apply_tag_suggestions` on TAG_SUGGESTIONS report | Works |
| Tagger calls `apply_tag_suggestions` on REPOST report | 403 Forbidden |
| Tagger calls `dismiss_report` | 403 (no REPORT_MANAGE) |
| Mod with REPORT_MANAGE does everything | No change to existing behavior |
| User with both permissions | Full access (REPORT_VIEW/MANAGE takes precedence) |

---

## Tests to Add

1. `test_tagger_can_list_tag_suggestion_reports` - filtered view works
2. `test_tagger_cannot_list_other_report_categories` - 403 on wrong category
3. `test_tagger_can_apply_tag_suggestions` - happy path
4. `test_tagger_cannot_apply_on_non_tag_suggestion_report` - 403 on wrong category
5. `test_tagger_cannot_dismiss_report` - 403
6. `test_mod_behavior_unchanged` - backward compatibility

---

## Frontend Considerations

Questions to investigate before implementation:

- Can the report list component be filtered by category?
- Can action buttons (dismiss, escalate, etc.) be hidden based on permission?
- Would taggers need a separate route/view, or can they use the existing report queue?
- Does the frontend make assumptions about users having full `REPORT_MANAGE`?
