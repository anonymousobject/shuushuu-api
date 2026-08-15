# Comment Reporting Feature Design

## Overview

Add the ability for users to report comments that violate community rules. Reports flow into the existing admin triage queue alongside image reports, with filtering to view them separately or together.

## Goals

- Allow users to report rule-violating comments (harassment, spam, illegal content)
- Unified admin experience with existing image reports
- Simple actions: dismiss report or delete comment
- Prevent report spam with rate limiting

## Data Model

### New Table: `comment_reports`

| Column | Type | Description |
|--------|------|-------------|
| `report_id` | int, PK | Auto-increment primary key |
| `comment_id` | int, FK | References `posts.post_id`, CASCADE on delete |
| `user_id` | int, FK | Reporter, references `users.user_id`, CASCADE on delete |
| `category` | int | 1=RULE_VIOLATION, 2=SPAM, 127=OTHER |
| `reason_text` | varchar | Optional details from reporter |
| `status` | int | 0=pending, 1=reviewed, 2=dismissed |
| `admin_notes` | text | Optional admin notes |
| `created_at` | datetime | When report was created |
| `reviewed_by` | int, FK | Admin who handled it, SET NULL on delete |
| `reviewed_at` | datetime | When report was processed |

### Indexes

- `(comment_id, user_id, status)` - Enforces 1 pending report per user per comment
- `(status, category)` - Admin triage filtering

### Category Constants

Add to `app/config.py`:

```python
class CommentReportCategory:
    RULE_VIOLATION = 1
    SPAM = 2
    OTHER = 127
```

## API Endpoints

### User Endpoint

**POST `/api/v1/comments/{comment_id}/report`**

Creates a new comment report.

- Request: `CommentReportCreate(category: int, reason_text: str | None)`
- Response: `CommentReportResponse` (201 Created)
- Auth: Required
- Validation:
  - Comment must exist
  - Comment must not be deleted
  - User must not have a pending report on this comment

### Admin Endpoints

**GET `/api/v1/admin/reports`** (modified)

Add `report_type` query parameter:
- `image` - Only image reports
- `comment` - Only comment reports
- `all` - Both (default)

Response structure when `report_type=all`:
```python
class UnifiedReportListResponse(BaseModel):
    image_reports: list[ReportListItem]
    comment_reports: list[CommentReportListItem]
    total: int
```

**POST `/api/v1/admin/reports/comments/{report_id}/dismiss`**

Dismiss a comment report without action.

- Request: `CommentReportDismissRequest(admin_notes: str | None)`
- Sets status to DISMISSED (2)
- Permission: REPORT_MANAGE

**POST `/api/v1/admin/reports/comments/{report_id}/delete`**

Delete the reported comment.

- Request: `CommentReportDeleteRequest(admin_notes: str | None)`
- Soft-deletes comment (sets `deleted=True`)
- Sets report status to REVIEWED (1)
- Creates AdminAction audit log
- Permission: REPORT_MANAGE

## Schemas

### `app/schemas/comment_report.py`

```python
class CommentReportCreate(BaseModel):
    category: int  # 1=RULE_VIOLATION, 2=SPAM, 127=OTHER
    reason_text: str | None = None

class CommentReportResponse(BaseModel):
    report_id: int
    comment_id: int
    image_id: int  # Denormalized for convenience
    user_id: int
    category: int
    reason_text: str | None
    status: int
    created_at: UTCDatetime

class CommentReportListItem(CommentReportResponse):
    username: str  # Reporter's username
    comment_author: UserSummary  # Who wrote the comment
    comment_preview: str  # First 100 chars of comment text
    admin_notes: str | None
    reviewed_by: int | None
    reviewed_at: UTCDatetime | None
```

## Validation & Error Handling

### Create Report

| Condition | Status | Message |
|-----------|--------|---------|
| Comment not found | 404 | "Comment not found" |
| Comment already deleted | 400 | "Cannot report a deleted comment" |
| User has pending report | 409 | "You already have a pending report on this comment" |
| Invalid category | 422 | Pydantic validation error |
| Not authenticated | 401 | Standard auth error |

### Admin Actions

| Condition | Status | Message |
|-----------|--------|---------|
| Report not found | 404 | "Report not found" |
| Report already processed | 400 | "Report has already been processed" |
| Comment already deleted | 400 | "Comment has already been deleted" |
| Missing permission | 403 | Standard permission error |

### Edge Cases

- If a comment is deleted outside the reporting system, the report can still be dismissed but the delete action returns "already deleted"
- Deleted comments cannot be reported

## File Changes

### New Files

- `app/models/comment_report.py` - CommentReports model
- `app/schemas/comment_report.py` - Request/response schemas
- `alembic/versions/xxx_add_comment_reports.py` - Migration
- `tests/api/v1/test_comment_reports.py` - Tests

### Modified Files

- `app/config.py` - Add CommentReportCategory constants
- `app/api/v1/comments.py` - Add POST /{comment_id}/report endpoint
- `app/api/v1/admin/reports.py` - Add report_type filter, comment report actions

## Permissions

Reuses existing permissions:
- `REPORT_VIEW` - View reports in admin queue
- `REPORT_MANAGE` - Dismiss reports, delete comments via reports

## Implementation Order

1. Add `CommentReportCategory` to config.py
2. Create `CommentReports` model with migration
3. Create schemas
4. Add user-facing `POST /comments/{id}/report` endpoint
5. Extend admin reports listing with `report_type` filter
6. Add admin dismiss/delete endpoints for comment reports
7. Tests for each layer
