# TODO Items

## Completed Items

### ✅ Redis Caching
**Status:** COMPLETED (2025-12)

Redis caching implemented for:
- Permission caching with TTL (`app/core/permission_cache.py`)
- Rate limiting for registration and search (`app/services/rate_limit.py`)
- Cache invalidation on permission changes

### ✅ Image Variant Generation
**Status:** COMPLETED (2025-11-30)

Medium and large image variants are now automatically generated during upload.

**Implementation:**
- `create_medium_variant()` - Creates 1280px variants with file size validation
- `create_large_variant()` - Creates 2048px variants with file size validation
- File size check: Deletes variants if not smaller than original
- Database updates: Sets `has_medium`/`has_large` to 0 when variants deleted

### ✅ Storage Path Configuration
**Status:** COMPLETED (2025-12)

Storage paths are properly configurable via `app/config.py`:
- `STORAGE_PATH`, `AVATAR_STORAGE_PATH`, `IMAGE_BASE_URL` all from settings

### ✅ Legacy BBCode Handling
**Status:** COMPLETED (2025-12)

Legacy BBCode from PHP database is handled via `app/utils/markdown.py`:
- `parse_markdown()` converts BBCode quotes to HTML blockquotes
- `normalize_legacy_entities()` decodes old HTML entities
- New content uses safe Markdown (bold, italic, links, blockquotes)

### ✅ JPEG Extension Standardization
**Status:** COMPLETED (2025-12)

Thumbnails standardized to `.jpeg` extension intentionally. Original images keep their uploaded extension.

---

## API Features

### Migrate Rating System from 1-10 to 1-5 Scale
**Priority:** Low
**Status:** Not Started

The current system uses a 1-10 rating scale (910,409 existing ratings, avg 8.53). Consider migrating to a simpler 1-5 star system for better user experience.

**Current State:**
- Database: `tinyint(2)` storing 1-10 values
- API validation: 1-10 (fixed 2025-11-30)
- Average rating: 8.53/10
- Total ratings: 910,409

**Migration Tasks:**
1. **Data Migration:**
   - Create Alembic migration to convert ratings: `new_rating = ROUND(old_rating / 2)`
   - Update `image_ratings` table: Convert all ratings proportionally
   - Update `images` table: Recalculate `rating` and `bayesian_rating` fields

2. **Code Changes:**
   - Update API validation: `ge=1, le=5` in `app/api/v1/images.py`
   - Update rating service: Adjust Bayesian formula for 1-5 scale
   - Update documentation and API specs

3. **Testing:**
   - Test data conversion accuracy
   - Verify Bayesian calculations work correctly
   - Test API endpoints accept 1-5 range
   - Load test with 900K+ rating updates

**Considerations:**
- **Data loss:** Some granularity lost (9→5, 10→5)
- **Downtime:** May require brief maintenance window
- **Rollback plan:** Keep backup of original ratings
- **Frontend impact:** Update UI to show 5 stars instead of 10

**Alternative:** Keep 1-10 scale (current decision)
- No migration needed
- More granular user feedback
- Frontend can display as half-stars (1-10 = 0.5-5.0 stars)

### Tag Proposal/Review System
**Priority:** Low
**Status:** Not Started

Allow users to petition for new tags and enable admins to review and approve them.

**Location:** `app/api/v1/tags.py:93`

**Tasks:**
1. Design tag proposal schema (user, tag_name, reason, status)
2. Create database model for tag proposals
3. Add API endpoints for submitting proposals
4. Add admin endpoints for reviewing/approving proposals
5. Add notification system for proposal status changes

### Image Similarity Check Confirmation Flow
**Priority:** Medium
**Status:** Partial

Currently, IQDB similarity checks are performed but don't block duplicate uploads. Need to implement user confirmation flow.

**Location:** `app/api/v1/images.py:1013-1021`

**Current State:**
- IQDB check runs via `check_iqdb_similarity()`
- Results logged but no user confirmation required

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

**Location:** `app/api/v1/comments.py:57`

**Tasks:**
1. Analyze input patterns (text length, special characters, operators)
2. Automatically select between LIKE, natural fulltext, or boolean fulltext
3. Document the detection logic
4. Add tests for various input scenarios

---

## Code Quality & Refactoring

### Extract User Validation Checks
**Priority:** Low
**Status:** Not Started

User creation endpoint has inline validation that should be extracted into reusable functions.

**Location:** `app/api/v1/users.py:609`

**Tasks:**
1. Extract username format validation to utility function
2. Extract email/username uniqueness check to utility function
3. Consider creating a ValidationService or moving to schemas
4. Update tests to cover extracted functions

**Current Validations:**
- Username format: `^[a-zA-Z0-9_.-]{3,20}$`
- Username/email uniqueness check

### Investigate using autogen for Alembic Migrations
**Priority:** Low
**Status:** Not Started

Research if we can leverage Alembic's autogenerate feature to simplify migration creation. Right now, we write migrations manually. The database models and the migrations can get out of sync.
