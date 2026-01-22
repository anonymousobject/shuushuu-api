# Audit Trail Design

## Overview

Implement a comprehensive audit trail for tracking changes across the system. Most audit data is publicly visible, with some admin-only restrictions.

## Data Model

### New Tables

#### `tag_audit_log`

Tracks all tag metadata changes with explicit columns per field type.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INT PK | Auto-increment primary key |
| `tag_id` | INT FK | The tag being modified |
| `action_type` | ENUM | `rename`, `type_change`, `alias_set`, `alias_removed`, `parent_set`, `parent_removed`, `source_linked`, `source_unlinked` |
| `old_title` | VARCHAR | Previous title (for renames) |
| `new_title` | VARCHAR | New title (for renames) |
| `old_type` | INT | Previous tag type (for type changes) |
| `new_type` | INT | New tag type (for type changes) |
| `old_alias_of` | INT FK | Previous alias target (for alias changes) |
| `new_alias_of` | INT FK | New alias target (for alias changes) |
| `old_parent_id` | INT FK | Previous parent tag (for inheritance changes) |
| `new_parent_id` | INT FK | New parent tag (for inheritance changes) |
| `character_tag_id` | INT FK | Character tag (for char-source link changes) |
| `source_tag_id` | INT FK | Source tag (for char-source link changes) |
| `user_id` | INT FK | User who made the change |
| `created_at` | DATETIME | Timestamp |

Most columns are NULL for any given row - only the relevant fields for the action type are populated.

#### `image_status_history`

Tracks image status changes for public visibility.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INT PK | Auto-increment primary key |
| `image_id` | INT FK | The image |
| `old_status` | INT | Previous status |
| `new_status` | INT | New status |
| `user_id` | INT FK | User who made the change |
| `created_at` | DATETIME | Timestamp |

### Existing Tables

#### `tag_history` (wire up to API)

Already exists with schema: `image_id`, `tag_id`, `action` ('a'/'r'), `user_id`, `date`

Currently contains legacy data only - needs API to populate on tag add/remove from images.

#### `image_reviews` (add public endpoint)

Already tracks review outcomes. Add public endpoint exposing non-sensitive fields only.

## Visibility Rules

### Tag Changes (Always Public)
- User who made the change is always visible
- Applies to: renames, type changes, aliases, inheritance, char-source links, tag add/remove on images

### Image Status Changes (Conditional)
| Status | User Visible? |
|--------|--------------|
| REPOST (-1) | Yes |
| SPOILER (2) | Yes |
| ACTIVE (1) | Yes |
| REVIEW (-4) | No |
| LOW_QUALITY (-3) | No |
| INAPPROPRIATE (-2) | No |
| OTHER (0) | No |

### Review Outcomes
- Outcome and timestamps: Public
- Individual votes: Admin-only
- `initiated_by`: Admin-only

## API Endpoints

### Tag Endpoints

#### `GET /tags/{tag_id}/history`

Tag metadata changes (renames, type, aliases, inheritance, char-source links).

```json
{
  "items": [
    {
      "id": 123,
      "action_type": "rename",
      "old_title": "Cirno (9)",
      "new_title": "Cirno",
      "user": {"user_id": 456, "username": "editor1"},
      "created_at": "2024-06-01T12:00:00Z"
    },
    {
      "id": 122,
      "action_type": "source_linked",
      "character_tag": {"tag_id": 100, "title": "Cirno"},
      "source_tag": {"tag_id": 200, "title": "Touhou Project"},
      "user": {"user_id": 789, "username": "editor2"},
      "created_at": "2024-02-01T10:00:00Z"
    }
  ],
  "total": 5,
  "page": 1,
  "per_page": 50
}
```

#### `GET /tags/{tag_id}/usage-history`

Tag applied/removed from images.

```json
{
  "items": [
    {
      "image_id": 1111520,
      "action": "added",
      "user": {"user_id": 456, "username": "tagger1"},
      "date": "2024-07-15T08:30:00Z"
    }
  ],
  "total": 1234,
  "page": 1,
  "per_page": 50
}
```

### Image Endpoints

#### `GET /images/{image_id}/tag-history`

Tags added/removed from this image.

```json
{
  "items": [
    {
      "tag": {"tag_id": 100, "title": "Cirno"},
      "action": "added",
      "user": {"user_id": 456, "username": "tagger1"},
      "date": "2024-07-15T08:30:00Z"
    },
    {
      "tag": {"tag_id": 50, "title": "outdated_tag"},
      "action": "removed",
      "user": {"user_id": 789, "username": "tagger2"},
      "date": "2024-07-16T09:00:00Z"
    }
  ],
  "total": 25,
  "page": 1,
  "per_page": 50
}
```

#### `GET /images/{image_id}/status-history`

Status changes for this image.

```json
{
  "items": [
    {
      "id": 1,
      "old_status": 1,
      "old_status_label": "active",
      "new_status": -1,
      "new_status_label": "repost",
      "user": {"user_id": 123, "username": "mod1"},
      "created_at": "2024-08-01T14:00:00Z"
    },
    {
      "id": 2,
      "old_status": -4,
      "old_status_label": "review",
      "new_status": 1,
      "new_status_label": "active",
      "user": null,
      "created_at": "2024-08-05T10:00:00Z"
    }
  ]
}
```

Note: `user` is `null` when status change involves hidden statuses.

#### `GET /images/{image_id}/reviews`

Review outcomes for this image.

```json
{
  "items": [
    {
      "review_id": 45,
      "review_type": 1,
      "review_type_label": "appropriateness",
      "outcome": 1,
      "outcome_label": "keep",
      "created_at": "2024-08-01T12:00:00Z",
      "closed_at": "2024-08-08T12:00:00Z"
    }
  ],
  "total": 1
}
```

Note: `initiated_by` and individual votes are hidden.

### User Endpoints

#### `GET /users/{user_id}/history`

All changes made by this user.

```json
{
  "items": [
    {
      "type": "tag_metadata",
      "action_type": "rename",
      "tag": {"tag_id": 100, "title": "Cirno"},
      "old_title": "Cirno (9)",
      "new_title": "Cirno",
      "created_at": "2024-06-01T12:00:00Z"
    },
    {
      "type": "tag_usage",
      "action": "added",
      "tag": {"tag_id": 100, "title": "Cirno"},
      "image_id": 1111520,
      "date": "2024-07-15T08:30:00Z"
    },
    {
      "type": "status_change",
      "image_id": 555000,
      "old_status": 1,
      "new_status": -1,
      "new_status_label": "repost",
      "created_at": "2024-08-01T14:00:00Z"
    }
  ],
  "total": 150,
  "page": 1,
  "per_page": 50
}
```

Aggregates from all audit tables, sorted by date descending. Status changes with hidden user types excluded for non-admins.

## Implementation Hooks

### Tag Metadata Changes

Hook into tag update endpoints in `app/api/v1/tags.py`:
- Renames: When `title` field changes
- Type changes: When `type` field changes
- Alias changes: When `alias_of` field set/removed
- Inheritance changes: When `inheritedfrom_id` field set/removed

### Character-Source Links

Hook into `app/api/v1/tags.py` where `CharacterSourceLinks` are created/deleted:
- Log to `tag_audit_log` with `action_type` = `source_linked` or `source_unlinked`

### Tag Add/Remove on Images

Hook into `app/api/v1/images.py`:
- `add_tag_to_image`: Write to `tag_history` with action='a'
- `remove_tag_from_image`: Write to `tag_history` with action='r'

### Image Status Changes

Hook into `app/api/v1/admin.py`:
- Status change endpoint: Write to `image_status_history`
- Review close logic in `app/services/review_jobs.py`: Write to `image_status_history` when review outcome changes image status

### Pattern

Each audit write is a simple `db.add(AuditRecord(...))` alongside the existing operation, committed in the same transaction.

## Retention Policy

No pruning - audit tables are permanent records.

## Migration Notes

- Create `tag_audit_log` table
- Create `image_status_history` table
- `tag_history` already exists - no schema changes needed
- `image_reviews` already exists - no schema changes needed
