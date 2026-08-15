# Image Reporting and Review System Design

## Overview

A system for users to report images and for admins/mods to review and take action. Separates user-initiated reports (triage queue) from admin-initiated appropriateness reviews (voting process).

## Workflows

### 1. User Reports

Users can report images for various reasons. Reports go into a triage queue for admin review. The image remains visible until an admin takes action.

```
User submits report
    ↓
image_reports created (status=pending)
    ↓
Admin views triage queue
    ↓
Admin chooses action:
    ├── Dismiss → report status=dismissed, no change to image
    ├── Quick action → report status=reviewed, image status changed
    └── Escalate to review → report status=reviewed, new image_review created
```

### 2. Admin Quick Actions

Single admin can take immediate action on an image without a voting process. Used for clear-cut cases: duplicates, spam, low quality, obvious rule violations.

Actions are logged in `admin_actions` table with reason and previous/new status.

### 3. Appropriateness Review (Voting Process)

For content appropriateness disputes where interpretation may vary. Can be initiated:
- By escalating a user report
- Directly by an admin on any image (no report required)

```
Admin initiates review
    ↓
image_review created (status=open, deadline set)
image.status = REVIEW (hidden from users)
    ↓
Admins cast votes in review_votes
    ↓
Quorum reached (3+ votes)?
    ├── Yes + majority → close review, apply outcome
    └── No or tie at deadline:
            ├── extension_used=false → extend deadline, set extension_used=true
            └── extension_used=true → default to keep
    ↓
Review closed:
    ├── outcome=keep → image.status = ACTIVE
    └── outcome=remove → image.status = INAPPROPRIATE
```

**Voting Rules:**
- Quorum: 3 votes minimum
- Decision: Simple majority once quorum met
- Deadline: Configurable (default 7 days), visible countdown to admins
- Tie at deadline: One extension allowed (default 3 days)
- Still tied or no quorum after extension: Default to keep

## Data Model

### Migration Strategy

The database has existing `image_reports` and `image_reviews` tables with simpler schemas. Rather than creating new tables with different names, we'll migrate the existing tables:

1. **`image_reports`** - Migrate in place:
   - Rename `open` → `status` (convert existing values: 1→'pending', 0→'dismissed')
   - Rename `text` → `reason_text`
   - Add columns: `reviewed_by`, `reviewed_at`

2. **`image_reviews`** (existing) - Rename to `review_votes`:
   - The existing table is already a vote table (image_id, user_id, vote)
   - Add column: `comment`
   - Add column: `review_id` FK (nullable initially for migration, then required for new votes)

3. **`image_reviews`** (new) - Create the review session table:
   - This is the missing piece - the actual review with deadline, status, outcome, etc.

4. **`admin_actions`** - Create new table

### image_reports

User-submitted reports awaiting admin triage.

**Existing columns (to keep/modify):**
| Column | Type | Migration |
|--------|------|-----------|
| image_report_id | INT PK | Keep as-is (rename conceptually to report_id) |
| image_id | INT FK | Keep |
| user_id | INT FK | Keep |
| category | INT | Keep |
| text | TEXT NULL | Rename to `reason_text` |
| open | INT | Rename to `status`, convert values (1→0 pending, 0→2 dismissed) |
| date | DATETIME | Keep as `created_at` |

**New columns to add:**
| Column | Type | Description |
|--------|------|-------------|
| reviewed_by | INT FK NULL | Admin who triaged |
| reviewed_at | DATETIME NULL | When triaged |

**Constraints:**
- Unique on (image_id, user_id) for pending reports (user can't report same image twice)

### image_reviews (NEW TABLE)

Voting sessions for appropriateness decisions. This is a new table - the existing `image_reviews` becomes `review_votes`.

| Column | Type | Description |
|--------|------|-------------|
| review_id | INT PK | Primary key |
| image_id | INT FK | Image under review |
| source_report_id | INT FK NULL | Report that triggered this review (null if initiated directly) |
| initiated_by | INT FK | Admin who started review |
| review_type | ENUM | appropriateness (extensible for future types) |
| deadline | DATETIME | When voting closes |
| extension_used | BOOL | Whether deadline was already extended |
| status | ENUM | open, closed |
| outcome | ENUM | pending, keep, remove |
| created_at | DATETIME | When review started |
| closed_at | DATETIME NULL | When review concluded |

**Constraints:**
- Only one open review per image at a time

### review_votes (RENAMED FROM image_reviews)

Individual admin votes on a review. This is the existing `image_reviews` table renamed.

**Existing columns (to keep/modify):**
| Column | Type | Migration |
|--------|------|-----------|
| image_review_id | INT PK | Keep as-is (rename conceptually to vote_id) |
| image_id | INT FK | Keep (for legacy votes without review session) |
| user_id | INT FK | Keep |
| vote | INT | Keep (1=keep/approve, 0=remove/reject) |

**New columns to add:**
| Column | Type | Description |
|--------|------|-------------|
| review_id | INT FK NULL | Review session this vote belongs to (null for legacy votes) |
| comment | TEXT NULL | Optional reasoning |
| created_at | DATETIME | When vote cast (default to migration timestamp for existing rows) |

**Constraints:**
- Partial unique on `(review_id, user_id) WHERE review_id IS NOT NULL` - for new votes with review sessions
- Keep existing unique on `(image_id, user_id)` - for legacy votes without review sessions

**Migration notes:**
- Set `created_at` to `NOW()` for existing rows (timestamp won't be accurate for legacy votes, but avoids NULL inconsistency)

### admin_actions (NEW TABLE)

Audit log for all admin moderation actions. Pruned after 2 years.

| Column | Type | Description |
|--------|------|-------------|
| action_id | INT PK | Primary key |
| user_id | INT FK | Admin who acted |
| action_type | ENUM | report_dismiss, report_action, review_start, review_vote, review_close, review_extend |
| report_id | INT FK NULL | Related report (if applicable) |
| review_id | INT FK NULL | Related review (if applicable) |
| image_id | INT FK NULL | Related image (if applicable) |
| details | JSON | Context (previous_status, new_status, vote value, etc.) |
| created_at | DATETIME | When action occurred (indexed for pruning) |

## API Endpoints

### User-facing

```
POST /api/v1/images/{image_id}/report
    Body: { category: ReportCategory, reason_text?: string }
    Auth: logged-in user
    → Creates image_report (status=pending)
```

### Admin Triage

```
GET /api/v1/admin/reports
    Query: status=pending|reviewed|dismissed, page, per_page
    Permission: REPORT_VIEW
    → Paginated list of reports with image info

POST /api/v1/admin/reports/{report_id}/dismiss
    Permission: REPORT_MANAGE
    → Sets report status=dismissed

POST /api/v1/admin/reports/{report_id}/action
    Body: { new_status: ImageStatus }
    Permission: REPORT_MANAGE
    → Sets report status=reviewed, changes image status

POST /api/v1/admin/reports/{report_id}/escalate
    Body: { deadline_days?: int }
    Permission: REPORT_MANAGE + REVIEW_START
    → Sets report status=reviewed, creates image_review
```

### Reviews (Voting Process)

```
GET /api/v1/admin/reviews
    Query: status=open|closed, page, per_page
    Permission: REVIEW_VIEW
    → Paginated list of reviews with vote counts, deadline

POST /api/v1/admin/images/{image_id}/review
    Body: { deadline_days?: int }
    Permission: REVIEW_START
    → Creates image_review directly (no report needed)

POST /api/v1/admin/reviews/{review_id}/vote
    Body: { vote: keep|remove, comment?: string }
    Permission: REVIEW_VOTE
    → Casts or updates vote

GET /api/v1/admin/reviews/{review_id}
    Permission: REVIEW_VIEW
    → Review details with all votes and comments

POST /api/v1/admin/reviews/{review_id}/close
    Body: { outcome: keep|remove }
    Permission: REVIEW_CLOSE_EARLY
    → Closes review early with specified outcome

POST /api/v1/admin/reviews/{review_id}/extend
    Body: { days?: int }
    Permission: REVIEW_START
    → Manually extends deadline (counts against extension limit)
```

## Configuration

New settings in `app/config.py`:

```python
# Review System
REVIEW_DEADLINE_DAYS: int = 7           # Default deadline
REVIEW_EXTENSION_DAYS: int = 3          # Extension period
REVIEW_QUORUM: int = 3                  # Minimum votes required
```

## Permissions

New permissions to add to `Permission` enum in `app/core/permissions.py`:

```python
# Report & Review system
REPORT_VIEW = "report_view"              # View report triage queue
REPORT_MANAGE = "report_manage"          # Dismiss/action/escalate reports
REVIEW_VIEW = "review_view"              # View open reviews
REVIEW_START = "review_start"            # Initiate appropriateness review
REVIEW_VOTE = "review_vote"              # Cast votes on reviews
REVIEW_CLOSE_EARLY = "review_close_early"  # Close review before deadline
```

## Status Constants

Following the existing codebase pattern, all status/enum values are stored as INT with app-level constants.

### Image Status

Add to `ImageStatus` in `app/config.py`:

```python
LOW_QUALITY = -3  # New status for low quality images
```

Existing statuses used:
- `REVIEW = -4` - Image under appropriateness review (hidden)
- `INAPPROPRIATE = -2` - Removed for content issues
- `REPOST = -1` - Duplicate of another image
- `ACTIVE = 1` - Normal visible state

### Report Status (NEW)

Add to `app/config.py`:

```python
class ReportStatus:
    """Report status constants"""
    PENDING = 0
    REVIEWED = 1
    DISMISSED = 2
```

### Review Status (NEW)

Add to `app/config.py`:

```python
class ReviewStatus:
    """Review session status constants"""
    OPEN = 0
    CLOSED = 1

class ReviewOutcome:
    """Review outcome constants"""
    PENDING = 0
    KEEP = 1
    REMOVE = 2

class ReviewType:
    """Review type constants"""
    APPROPRIATENESS = 1
```

## Background Jobs

Location: `app/services/review_jobs.py` (or similar)

### check_review_deadlines

Runs hourly or daily to process expired reviews.

**Function signature:**
```python
async def check_review_deadlines(db: AsyncSession) -> dict:
    """
    Process all open reviews past their deadline.

    Returns:
        dict with counts: {"closed": int, "extended": int, "skipped": int}
    """
```

**Algorithm:**
```python
# 1. Query all open reviews past deadline
reviews = SELECT * FROM image_reviews
          WHERE status = ReviewStatus.OPEN
          AND deadline < NOW()

for review in reviews:
    # 2. Count votes
    votes = SELECT vote, COUNT(*) FROM review_votes
            WHERE review_id = review.review_id
            GROUP BY vote

    keep_votes = votes.get(1, 0)  # vote=1 is keep
    remove_votes = votes.get(0, 0)  # vote=0 is remove
    total_votes = keep_votes + remove_votes

    # 3. Check quorum (configurable, default 3)
    quorum = settings.REVIEW_QUORUM
    has_quorum = total_votes >= quorum

    # 4. Determine outcome
    if has_quorum and keep_votes != remove_votes:
        # Has quorum and clear majority - close with outcome
        outcome = ReviewOutcome.KEEP if keep_votes > remove_votes else ReviewOutcome.REMOVE
        close_review(review, outcome)
    elif not review.extension_used:
        # No quorum or tie, first time - extend deadline
        extend_review(review)
    else:
        # No quorum or tie after extension - default to keep
        close_review(review, ReviewOutcome.KEEP)
```

**Helper functions:**
```python
async def close_review(db: AsyncSession, review: ImageReviews, outcome: int) -> None:
    """Close review and update image status."""
    review.status = ReviewStatus.CLOSED
    review.outcome = outcome
    review.closed_at = datetime.now(UTC)

    # Update image status
    image = await db.get(Images, review.image_id)
    if outcome == ReviewOutcome.KEEP:
        image.status = ImageStatus.ACTIVE
    else:  # REMOVE
        image.status = ImageStatus.INAPPROPRIATE

    # Create admin_action (system action, user_id could be NULL or system user)
    admin_action = AdminActions(
        user_id=None,  # or a system user ID
        action_type=AdminActionType.REVIEW_CLOSE,
        review_id=review.review_id,
        image_id=review.image_id,
        details={"outcome": outcome, "reason": "deadline_expired", "automatic": True}
    )
    db.add(admin_action)

async def extend_review(db: AsyncSession, review: ImageReviews) -> None:
    """Extend review deadline."""
    extension_days = settings.REVIEW_EXTENSION_DAYS
    review.deadline = datetime.now(UTC) + timedelta(days=extension_days)
    review.extension_used = 1

    # Create admin_action
    admin_action = AdminActions(
        user_id=None,
        action_type=AdminActionType.REVIEW_EXTEND,
        review_id=review.review_id,
        image_id=review.image_id,
        details={"reason": "deadline_expired_auto_extend", "automatic": True}
    )
    db.add(admin_action)
```

**Scheduling:**
- Can be run via ARQ worker, Celery beat, or simple cron calling an endpoint
- Recommended: Run every hour to ensure timely processing
- Should be idempotent - safe to run multiple times

### prune_admin_actions

Runs monthly to maintain audit log size.

**Function signature:**
```python
async def prune_admin_actions(db: AsyncSession, retention_years: int = 2) -> int:
    """
    Delete admin_actions older than retention period.

    Args:
        db: Database session
        retention_years: How many years of history to keep (default 2)

    Returns:
        Number of rows deleted
    """
```

**Implementation:**
```python
cutoff_date = datetime.now(UTC) - timedelta(days=retention_years * 365)
result = await db.execute(
    delete(AdminActions).where(AdminActions.created_at < cutoff_date)
)
await db.commit()
return result.rowcount
```

**Scheduling:**
- Run monthly (first of month, off-peak hours)
- Log the number of deleted rows for monitoring

### Implementation Guidelines

**Architecture:**
- Create standalone service module (`app/services/review_jobs.py`) with pure async functions
- Job runners (ARQ, cron, admin endpoint) call these service functions
- This allows flexibility - same logic can be triggered from anywhere

**System user for audit log:**
- Use `user_id=None` for automatic actions
- The `automatic: True` flag in details JSON distinguishes system actions
- Queries needing to filter can check `user_id IS NULL` or `details->>'automatic'`

**Transaction handling:**
- Process each review in its own transaction
- One failure should not block other reviews from processing
- Wrap each review in try/except, log errors, continue to next
- Job can be re-run to retry failures

**Logging and return value:**
- Log to application logger (structlog) for observability
- Return summary dict for caller (ARQ result, manual invocation, etc.)

```python
async def check_review_deadlines(db: AsyncSession) -> dict:
    """Process expired reviews."""
    logger = get_logger(__name__)
    results = {
        "processed": 0,
        "closed": 0,
        "extended": 0,
        "errors": 0,
        "error_details": []
    }

    reviews = await get_expired_open_reviews(db)

    for review in reviews:
        try:
            async with db.begin_nested():  # Savepoint for each review
                outcome = await process_single_review(db, review)
                results["processed"] += 1
                if outcome == "closed":
                    results["closed"] += 1
                elif outcome == "extended":
                    results["extended"] += 1
        except Exception as e:
            results["errors"] += 1
            results["error_details"].append({
                "review_id": review.review_id,
                "error": str(e)
            })
            logger.error("review_processing_failed",
                        review_id=review.review_id,
                        error=str(e))

    logger.info("check_review_deadlines_complete", **results)
    return results
```

## Image Visibility

- Non-active images filtered from normal user queries
- Admins can always see all images
- Users can optionally see placeholders for non-active images (profile setting)
- `SPOILER` status is soft - shows placeholder, click to reveal

## Validation Rules

- User cannot report the same image twice (unique constraint for pending reports)
- Admin cannot vote twice on same review (can change existing vote)
- Cannot start a new review on an image that already has an open review
- Cannot escalate an already-reviewed report

## Clarifications

### Concurrent Report Handling

Multiple users can report the same image for different reasons. Each report is handled independently - dismissing one report does not affect others. The triage UI should group pending reports by image for admin convenience.

### Vote Changes

Admins can change their vote before a review closes. Only the current vote is stored in `review_votes`. Vote changes are not tracked in the audit log - only the final vote at close time matters for the outcome.

### Resolved Questions

- **Early close permission**: Uses `REVIEW_CLOSE_EARLY` permission (decided: separate permission for this significant action)
- **Legacy votes constraint**: Dual constraints - partial unique on `(review_id, user_id)` for new votes, keep existing `(image_id, user_id)` for legacy
- **Status storage**: INT with app-level constants (matches existing `ImageStatus`, `ReportCategory` patterns)
- **Legacy vote timestamps**: Set to `NOW()` at migration time (avoids NULL, acknowledges inaccuracy for pre-migration data)

## Testing

Tests should be placed in `tests/api/v1/` for API tests and `tests/unit/` for unit tests.

### Unit Tests (`tests/unit/test_review_system.py`)

**Status constants:**
- Verify `ReportStatus`, `ReviewStatus`, `ReviewOutcome`, `ReviewType`, `AdminActionType` have expected values
- Verify `ImageStatus.LOW_QUALITY` exists with value -3

**Model validation:**
- `ImageReports`: required fields (image_id, user_id, category), defaults (status=PENDING)
- `ImageReviews`: required fields (image_id, initiated_by, deadline), defaults (status=OPEN, outcome=PENDING, extension_used=False)
- `ReviewVotes`: required fields (user_id, vote), optional fields (review_id, comment)
- `AdminActions`: required fields (user_id, action_type), JSON details field accepts dict

### Integration Tests (`tests/integration/test_review_constraints.py`)

**Database constraints:**
- `image_reports`: Unique constraint on (image_id, user_id) for pending reports - second report from same user on same image should fail
- `review_votes`: Legacy unique constraint on (image_id, user_id) enforced
- `review_votes`: Unique constraint on (review_id, user_id) - same admin cannot vote twice on same review
- `image_reviews`: Only one open review per image - creating second open review for same image should fail
- Foreign key cascades: Deleting image cascades to reports, reviews, votes

### API Tests (`tests/api/v1/test_reports.py`)

**User report endpoint (`POST /images/{image_id}/report`):**
- Success: logged-in user can report image with valid category
- Success: report with optional reason_text
- Fail: unauthenticated user cannot report (401)
- Fail: invalid category rejected (422)
- Fail: reporting non-existent image (404)
- Fail: duplicate report from same user on same image (409 or 422)

**Admin triage endpoints:**

`GET /admin/reports`:
- Success: admin with REPORT_VIEW sees pending reports
- Success: filter by status works (pending, reviewed, dismissed)
- Success: pagination works
- Fail: user without REPORT_VIEW permission denied (403)
- Fail: unauthenticated (401)

`POST /admin/reports/{report_id}/dismiss`:
- Success: sets report status to dismissed, no image change
- Success: creates admin_action audit log entry
- Fail: dismissing already-reviewed report (400)
- Fail: without REPORT_MANAGE permission (403)

`POST /admin/reports/{report_id}/action`:
- Success: sets report status to reviewed, changes image status
- Success: creates admin_action audit log entry
- Fail: invalid image status (422)
- Fail: without REPORT_MANAGE permission (403)

`POST /admin/reports/{report_id}/escalate`:
- Success: creates image_review, sets image status to REVIEW
- Success: links source_report_id to the report
- Success: sets deadline based on config or request param
- Fail: without REPORT_MANAGE + REVIEW_START permissions (403)
- Fail: escalating already-reviewed report (400)
- Fail: image already has open review (409)

### API Tests (`tests/api/v1/test_reviews.py`)

**Review management endpoints:**

`GET /admin/reviews`:
- Success: admin with REVIEW_VIEW sees open reviews
- Success: filter by status (open, closed)
- Success: includes vote counts and deadline info
- Fail: without REVIEW_VIEW permission (403)

`POST /admin/images/{image_id}/review`:
- Success: creates review, sets image status to REVIEW
- Success: deadline calculated from config default
- Success: custom deadline_days parameter respected
- Fail: without REVIEW_START permission (403)
- Fail: image already has open review (409)

`POST /admin/reviews/{review_id}/vote`:
- Success: casts new vote (keep or remove)
- Success: updates existing vote (changed mind)
- Success: vote with optional comment
- Fail: without REVIEW_VOTE permission (403)
- Fail: voting on closed review (400)

`GET /admin/reviews/{review_id}`:
- Success: returns review with all votes and comments
- Fail: without REVIEW_VIEW permission (403)
- Fail: non-existent review (404)

`POST /admin/reviews/{review_id}/close`:
- Success: closes review early with specified outcome
- Success: sets image status based on outcome (ACTIVE or INAPPROPRIATE)
- Success: creates admin_action audit log entry
- Fail: without REVIEW_CLOSE_EARLY permission (403)
- Fail: closing already-closed review (400)

`POST /admin/reviews/{review_id}/extend`:
- Success: extends deadline by config or param days
- Success: sets extension_used to true
- Fail: extending when extension already used (400)
- Fail: without REVIEW_START permission (403)
- Fail: extending closed review (400)

### Background Job Tests (`tests/unit/test_review_deadline_job.py`)

**check_review_deadlines job:**

Quorum and majority scenarios:
- 3 keep, 0 remove → close with outcome=KEEP, image status=ACTIVE
- 0 keep, 3 remove → close with outcome=REMOVE, image status=INAPPROPRIATE
- 2 keep, 1 remove → close with outcome=KEEP (majority)
- 1 keep, 2 remove → close with outcome=REMOVE (majority)

No quorum scenarios:
- 2 votes total, deadline passed, extension_used=false → extend deadline, set extension_used=true
- 2 votes total, deadline passed, extension_used=true → close with outcome=KEEP (default)

Tie scenarios:
- 2 keep, 2 remove, extension_used=false → extend deadline
- 2 keep, 2 remove, extension_used=true → close with outcome=KEEP (default)

Edge cases:
- Review not past deadline → no action taken
- Review already closed → skipped
- 0 votes, deadline passed → extend (first time) or default keep (after extension)

### Audit Log Tests (`tests/api/v1/test_admin_actions.py`)

**Admin action logging:**
- Each admin action creates corresponding admin_actions entry
- Correct action_type for each operation
- Correct foreign keys populated (report_id, review_id, image_id as applicable)
- Details JSON contains relevant context (previous_status, new_status, vote value, etc.)
