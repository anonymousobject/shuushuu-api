# Avatar Upload Feature Design

## Overview

Allow users to upload avatar images for their profile. Avatars can be JPG, PNG, or GIF (animated supported). Images are resized to fit within 200x200 pixels while preserving aspect ratio.

## Configuration

New settings in `app/config.py`:

```python
AVATAR_STORAGE_PATH: str = "/shuushuu/avatars"  # Separate from main image storage
MAX_AVATAR_SIZE: int = 1 * 1024 * 1024  # 1MB max upload size
MAX_AVATAR_DIMENSION: int = 200  # Max width/height after resize
```

## Storage

**Location:** `AVATAR_STORAGE_PATH/{md5_hash}.{ext}`

**Filename format:** MD5 hash of processed (resized) image content + extension
- Enables deduplication across users
- Compatible with existing avatar filenames in production DB

**Example:** `5c8395dc8f331a551a5310311802b75e.png`

## API Endpoints

### Upload Avatar

**`POST /users/me/avatar`**
- Upload avatar for current user
- Auth: Required (any authenticated user)
- Request: `multipart/form-data` with `avatar` file field
- Response: `UserResponse`

**`POST /users/{user_id}/avatar`**
- Upload avatar for specified user
- Auth: Required (self or admin)
- Request: `multipart/form-data` with `avatar` file field
- Response: `UserResponse`

### Delete Avatar

**`DELETE /users/me/avatar`**
- Remove avatar for current user
- Auth: Required (any authenticated user)
- Response: `UserResponse`

**`DELETE /users/{user_id}/avatar`**
- Remove avatar for specified user
- Auth: Required (self or admin)
- Response: `UserResponse`

## Avatar Service

New file `app/services/avatar.py`:

### `validate_avatar_upload(file: UploadFile, temp_path: Path)`
- Reuse `validate_image_file()` from `image_processing.py`
- Additional check: file size <= `MAX_AVATAR_SIZE`
- Allowed extensions: .jpg, .jpeg, .png, .gif

### `resize_avatar(file_path: Path) -> tuple[bytes, str]`
- Resize to fit within 200x200 preserving aspect ratio
- For GIFs: use `ImageSequence` to preserve animation frames
- Returns processed bytes and extension

### `save_avatar(content: bytes, ext: str) -> str`
- Calculate MD5 hash of content
- Save to `AVATAR_STORAGE_PATH/{hash}.{ext}`
- Returns filename (hash.ext)

### `delete_avatar_if_orphaned(filename: str, db: AsyncSession)`
- Query users table for count with this avatar filename
- If count == 0, delete file from disk

## Schema Changes

### `UserUpdate` (app/schemas/user.py)
- Remove `avatar` field (avatar changes only via dedicated routes)

### `UserBase` (app/models/user.py)
- Remove `avatar_type` field (deprecated, unused)

## Database Migration

Alembic migration to drop `avatar_type` column from `users` table.

No data migration needed - existing `avatar` values are already MD5 hash filenames compatible with the new system.

## Security

- File extension validation
- Content-Type header validation
- Magic byte verification via PIL
- File size limit (1MB)
- PIL image verification (prevents malicious files with fake extensions)

## Design Decisions

### GIF resizing
No post-resize size check. If upload was under 1MB and we resized to 200x200, the result should be reasonable.

### Orphan cleanup timing
Immediate deletion when user changes/deletes avatar. The race condition (two users uploading identical avatar simultaneously) is exceedingly unlikely and not worth the added complexity of a background job.

### Avatar serving
Out of scope for this feature. URL construction left to client based on filename. Serving mechanism (API endpoint vs nginx) to be determined later.

### Error responses
- File too large: **413 Request Entity Too Large**
- Invalid file type: **400 Bad Request**
- Structured JSON error responses matching existing API pattern

### Avatar field in responses
Returns filename only. Client constructs full URL.

### avatar_type removal
Drop unconditionally - deprecated legacy field.

### Upload size check timing
The current implementation writes the uploaded file to a temp location before checking size in `validate_avatar_upload()`. This means oversized files are fully written to disk before rejection. This is acceptable because:
1. FastAPI/Starlette has configurable request body limits at the server level
2. Temp files are always cleaned up in a `finally` block
3. The 1MB limit is small enough that temporary disk usage is negligible

For additional hardening, consider adding server-level request body limits (e.g., nginx `client_max_body_size`) as a first line of defense.

## Files to Modify

1. `app/config.py` - add avatar settings
2. `app/models/user.py` - remove `avatar_type` from `UserBase`
3. `app/schemas/user.py` - remove `avatar` from `UserUpdate`
4. `app/api/v1/users.py` - add 4 avatar routes

## Files to Create

1. `app/services/avatar.py` - avatar processing service
2. `alembic/versions/xxx_drop_avatar_type.py` - migration

## Testing

- Unit tests for avatar service functions
- Integration tests for all 4 routes
- Test GIF animation preservation
- Test orphan cleanup logic (delete old file only when no users reference it)
