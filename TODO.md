# TODO Items

## Redis Caching
**Priority:** Medium
**Status:** Not Started

Implement Redis caching for frequently accessed data to improve performance.

## Image Processing

### Implement Image Variant Generation
**Priority:** Medium
**Status:** Not Started

The system currently has placeholder functions for generating medium and large image variants but they're not implemented.

**Tasks:**
1. Implement medium variant generation (`app/services/image_processing.py:155`):
   - Check if width or height > settings.MEDIUM_EDGE (1280px)
   - Resize maintaining aspect ratio
   - Save as YYYY-MM-DD-{image_id}-medium.{ext}
   - Use settings.LARGE_QUALITY for compression
2. Implement large variant generation (`app/services/image_processing.py:178`):
   - Check if width or height > settings.LARGE_EDGE (2048px)
   - Resize maintaining aspect ratio
   - Save as YYYY-MM-DD-{image_id}-large.{ext}
   - Use settings.LARGE_QUALITY for compression

**Files Affected:**
- `app/services/image_processing.py` - `create_medium_variant()` and `create_large_variant()` functions

## API Features

### Tag Proposal/Review System
**Priority:** Low
**Status:** Not Started

Allow users to petition for new tags and enable admins to review and approve them.

**Location:** `app/api/v1/tags.py:21`

**Tasks:**
1. Design tag proposal schema (user, tag_name, reason, status)
2. Create database model for tag proposals
3. Add API endpoints for submitting proposals
4. Add admin endpoints for reviewing/approving proposals
5. Add notification system for proposal status changes

### Image Similarity Check Confirmation Flow
**Priority:** Medium
**Status:** Not Started

Currently, IQDB similarity checks are performed but don't block duplicate uploads. Need to implement user confirmation flow.

**Location:** `app/api/v1/images.py:685`

**Tasks:**
1. Return 409 status with list of similar images when high-scoring matches found
2. Add `skip_similarity_check` parameter to allow user to confirm upload anyway
3. Define threshold for "high-scoring" matches
4. Design UI flow for showing matches to user

**Example Implementation:**
```python
if similar_images and not skip_similarity_check:
    raise HTTPException(409, {
        "matches": similar_images,
        "message": "Similar images found"
    })
```

### Comment Search Automatic Mode Detection
**Priority:** Low
**Status:** Not Started

Add intelligent detection of the most efficient search method based on input parameters.

**Location:** `app/api/v1/comments.py:43`

**Tasks:**
1. Analyze input patterns (text length, special characters, operators)
2. Automatically select between LIKE, natural fulltext, or boolean fulltext
3. Document the detection logic
4. Add tests for various input scenarios

## Code Quality & Refactoring

### Extract User Validation Checks
**Priority:** Low
**Status:** Not Started

User creation endpoint has inline validation that should be extracted into reusable functions.

**Location:** `app/api/v1/users.py:474`

**Tasks:**
1. Extract username format validation to utility function
2. Extract email/username uniqueness check to utility function
3. Consider creating a ValidationService or moving to schemas
4. Update tests to cover extracted functions

**Current Validations:**
- Username format: `^[a-zA-Z0-9_.-]{3,20}$`
- Username/email uniqueness check

## Content Migration

### Convert Legacy BBCode to Markdown
**Priority:** Medium
**Status:** Not Started

Legacy image comments in the database use BBCode formatting (`[quote]`, `[spoiler]`, `[url]`).
These need to be converted to Markdown format for consistency with the new API.

**Tasks:**
1. Create migration script to convert BBCode → Markdown:
   - `[quote="author"]text[/quote]` → `> **author wrote:** text`
   - `[spoiler]text[/spoiler]` → `> **Spoiler:** text`
   - `[url]link[/url]` → `link` (auto-linked)
   - `[url=link]text[/url]` → `[text](link)`
2. Test conversion on sample data
3. Run migration against production data (requires downtime or versioned API)
4. Remove BBCode parser from PHP codebase once migration complete

**Files Affected:**
- `app/models/comment.py` - `post_text` field contains BBCode
- Legacy PHP: `shuu-php/common/functions/image.php` - `applybbCode()` function
- New parser: `app/utils/markdown.py`

**Notes:**
- Consider keeping BBCode parser temporarily for backwards compatibility
- May need dual-mode rendering during transition period
- Check if any users have BBCode in their private messages (shouldn't exist but verify)
