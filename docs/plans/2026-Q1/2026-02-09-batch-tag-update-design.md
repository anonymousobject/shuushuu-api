# Batch Tag Update

## Overview

Add a batch endpoint for applying multiple tags to multiple images in a single API call. Users with the `IMAGE_TAG_ADD` permission can tag up to 100 images with up to 5 tags at once.

## Endpoint

`POST /api/v1/tags/batch`

### Request

```json
{
  "action": "add",
  "tag_ids": [42, 128],
  "image_ids": [1111520, 1111521, 1111522]
}
```

| Field      | Type       | Constraints              |
|------------|------------|--------------------------|
| `action`   | enum       | `"add"` only (for now)   |
| `tag_ids`  | list[int]  | 1-5 items                |
| `image_ids`| list[int]  | 1-100 items              |

The `action` field exists so we can add `"remove"` later without a new endpoint or breaking change.

### Response (200 on success)

```json
{
  "added": [
    {"image_id": 1111520, "tag_id": 42},
    {"image_id": 1111520, "tag_id": 128},
    {"image_id": 1111521, "tag_id": 42}
  ],
  "skipped": [
    {"image_id": 1111522, "tag_id": 42, "reason": "already_tagged"},
    {"image_id": 1111522, "tag_id": 128, "reason": "image_not_found"}
  ]
}
```

Skip reasons:
- `already_tagged` - tag link already exists on this image
- `image_not_found` - image ID does not exist
- `tag_not_found` - tag ID does not exist

A fully-skipped batch is not an error; the caller gets an empty `added` list.

### Authorization

Requires `IMAGE_TAG_ADD` permission (via `require_permission` dependency, consistent with other tag endpoints). Returns 401 without authentication, 403 without permission. Admins are authorized if their group grants `IMAGE_TAG_ADD`. No per-image ownership check.

### Error Responses

- `401` - Not authenticated
- `403` - Missing `IMAGE_TAG_ADD` permission
- `422` - Validation error (empty lists, exceeds caps, invalid action)

## Processing Flow

1. Validate request (Pydantic enforces caps and types)
2. Check permission: `IMAGE_TAG_ADD` via `require_permission`. If missing, return 403.
3. Resolve tag aliases: for each tag_id, resolve to canonical tag. If a tag does not exist, collect for skipped list.
4. Fetch all requested images in one query: `SELECT image_id FROM images WHERE image_id IN (...)`. Missing IDs go to skipped list.
5. Fetch existing tag links in one query: `SELECT image_id, tag_id FROM tag_links WHERE image_id IN (...) AND tag_id IN (...)`. These become `already_tagged` skips.
6. Bulk insert new TagLinks for all valid pairs (user_id = current user).
7. Bulk insert TagHistory records (action `"a"`) for each new link.
8. Single `db.commit()` -- all inserts are atomic.

Three SELECT queries plus two bulk inserts. No N+1 loops.

## Code Organization

- **Schemas:** `app/schemas/tag.py` - request/response models
- **Route:** `app/api/v1/tags.py` - endpoint handler
- **Service:** `app/services/batch_tag.py` - business logic

## Future Extension

Add `"remove"` action support using the same endpoint and response shape. The `action` field and skip-and-report pattern already accommodate this.
