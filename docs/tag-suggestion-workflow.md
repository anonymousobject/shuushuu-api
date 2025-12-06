# Tag Suggestion System - Workflow Guide

**Last Updated:** 2025-12-04

---

## Overview

The Tag Suggestion System automatically analyzes uploaded images using machine learning models and suggests relevant tags. This reduces manual tagging effort while maintaining human oversight through a review workflow.

**Key Features:**
- Automatic tag suggestions generated during image upload
- ML models analyze image content (theme, source, character tags)
- Human-in-the-loop: all suggestions require approval
- Batch review capabilities for efficiency
- Confidence scores to indicate suggestion quality

---

## Table of Contents

1. [User Workflows](#user-workflows)
2. [API Endpoints](#api-endpoints)
3. [Integration Guide](#integration-guide)
4. [Common Use Cases](#common-use-cases)
5. [Best Practices](#best-practices)
6. [Troubleshooting](#troubleshooting)

---

## User Workflows

### Workflow 1: Image Uploader Reviews Own Image

This is the primary workflow for users uploading images.

**Step 1: Upload Image**

```bash
POST /api/v1/images
Content-Type: multipart/form-data

# Form data with image file and metadata
```

**Response includes:**
- `image_id` - The created image ID
- Background job automatically queued for tag suggestion generation

**Step 2: Wait for ML Processing**

The system processes the image in the background (typically 30-60 seconds):
1. Image is analyzed by ML models
2. Tag suggestions are generated with confidence scores
3. Tag aliases are resolved to canonical tags
4. Suggestions are stored in the database

**Step 3: View Tag Suggestions**

```bash
GET /api/v1/images/{image_id}/tag-suggestions?status=pending
```

**Response:**
```json
{
  "image_id": 12345,
  "suggestions": [
    {
      "suggestion_id": 1,
      "tag": {
        "tag_id": 46,
        "title": "long hair",
        "type": 1
      },
      "confidence": 0.92,
      "model_source": "custom_theme",
      "status": "pending",
      "created_at": "2025-12-04T12:00:00Z",
      "reviewed_at": null
    },
    {
      "suggestion_id": 2,
      "tag": {
        "tag_id": 161,
        "title": "blue eyes",
        "type": 1
      },
      "confidence": 0.88,
      "model_source": "custom_theme",
      "status": "pending",
      "created_at": "2025-12-04T12:00:00Z",
      "reviewed_at": null
    }
  ],
  "total": 2,
  "pending": 2,
  "approved": 0,
  "rejected": 0
}
```

**Step 4: Review and Approve/Reject**

User selects which suggestions to approve or reject:

```bash
POST /api/v1/images/{image_id}/tag-suggestions/review
Content-Type: application/json

{
  "suggestions": [
    {"suggestion_id": 1, "action": "approve"},
    {"suggestion_id": 2, "action": "reject"}
  ]
}
```

**Response:**
```json
{
  "approved": 1,
  "rejected": 1,
  "errors": []
}
```

**What Happens:**
- **Approved suggestions**: Tag is applied to image (TagLink created), suggestion marked as approved
- **Rejected suggestions**: Suggestion marked as rejected, tag NOT applied

**Step 5: Add Additional Tags (Optional)**

User can still manually add tags via the normal tagging interface. ML suggestions complement, not replace, manual tagging.

---

### Workflow 2: Moderator Batch Review

Moderators can review suggestions for any image.

**Step 1: Get List of Images with Pending Suggestions**

```bash
GET /api/v1/images?has_pending_suggestions=true&per_page=20
```

_(Note: This endpoint may need to be extended to support this filter)_

**Step 2: Review Each Image's Suggestions**

For each image with pending suggestions:

```bash
GET /api/v1/images/{image_id}/tag-suggestions?status=pending
```

**Step 3: Batch Approve High-Confidence Suggestions**

Moderators can quickly approve high-confidence suggestions:

```bash
POST /api/v1/images/{image_id}/tag-suggestions/review
Content-Type: application/json

{
  "suggestions": [
    {"suggestion_id": 1, "action": "approve"},
    {"suggestion_id": 2, "action": "approve"},
    {"suggestion_id": 3, "action": "approve"}
  ]
}
```

**Best Practice:** Focus on suggestions with confidence >= 0.8 for quick approval.

---

## API Endpoints

### GET /api/v1/images/{image_id}/tag-suggestions

Get tag suggestions for a specific image.

**Query Parameters:**
- `status` (optional): Filter by status
  - `pending` - Not yet reviewed
  - `approved` - Approved and applied to image
  - `rejected` - Rejected by reviewer
  - Default: All statuses

**Authentication:** Required
**Permissions:**
- Image uploader can view suggestions for their own images
- Moderators (IMAGE_TAG_ADD permission) can view any image's suggestions

**Response:** `TagSuggestionsListResponse`

**Example:**
```bash
# Get only pending suggestions
curl -H "Authorization: Bearer $TOKEN" \
  "https://api.example.com/api/v1/images/12345/tag-suggestions?status=pending"

# Get all suggestions (any status)
curl -H "Authorization: Bearer $TOKEN" \
  "https://api.example.com/api/v1/images/12345/tag-suggestions"
```

---

### POST /api/v1/images/{image_id}/tag-suggestions/review

Review (approve or reject) tag suggestions in batch.

**Request Body:** `ReviewSuggestionsRequest`
```json
{
  "suggestions": [
    {"suggestion_id": 1, "action": "approve"},
    {"suggestion_id": 2, "action": "reject"}
  ]
}
```

**Authentication:** Required
**Permissions:**
- Image uploader can review suggestions for their own images
- Moderators (IMAGE_TAG_ADD permission) can review any image's suggestions

**Response:** `ReviewSuggestionsResponse`

**Behavior:**
- **Idempotent**: Can re-review already reviewed suggestions
- **Prevents duplicates**: Won't create duplicate TagLinks if tag already applied
- **Tracks reviewer**: Records who reviewed and when

**Example:**
```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "suggestions": [
      {"suggestion_id": 1, "action": "approve"},
      {"suggestion_id": 2, "action": "approve"},
      {"suggestion_id": 3, "action": "reject"}
    ]
  }' \
  "https://api.example.com/api/v1/images/12345/tag-suggestions/review"
```

---

## Integration Guide

### Frontend Integration

**Display Suggestions to Users**

After image upload, poll or check for pending suggestions:

```javascript
// After upload completes
const imageId = uploadResponse.image_id;

// Wait a bit for ML processing (or use polling/websocket)
await sleep(30000); // 30 seconds

// Fetch suggestions
const response = await fetch(
  `/api/v1/images/${imageId}/tag-suggestions?status=pending`,
  {
    headers: { Authorization: `Bearer ${token}` }
  }
);

const data = await response.json();

if (data.pending > 0) {
  // Display suggestions to user with checkboxes
  displaySuggestions(data.suggestions);
}
```

**Submit Review**

When user selects suggestions to approve/reject:

```javascript
const reviewActions = selectedSuggestions.map(sugg => ({
  suggestion_id: sugg.suggestion_id,
  action: sugg.approved ? 'approve' : 'reject'
}));

await fetch(`/api/v1/images/${imageId}/tag-suggestions/review`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({ suggestions: reviewActions })
});
```

**UI/UX Recommendations:**
- Sort suggestions by confidence (highest first)
- Color-code by confidence:
  - Green: >= 0.8 (high confidence)
  - Yellow: 0.6-0.8 (medium confidence)
  - Red: < 0.6 (low confidence, rarely shown)
- Show model source badge (custom_theme vs danbooru)
- Provide "Approve All High Confidence" button (>= 0.8)
- Allow filtering by tag type (theme, source, character)

---

### Backend Integration

**Trigger Suggestion Generation After Upload**

The upload endpoint should enqueue the background job:

```python
# In POST /api/v1/images endpoint
from app.tasks.arq import get_arq_queue

# After image is saved
await queue.enqueue_job(
    "generate_tag_suggestions",
    image_id=image.image_id,
    _defer_by=30  # Wait 30s for image processing
)
```

**Check Processing Status (Optional)**

If implementing real-time status notifications:

```python
# In background job
from app.tasks.ml_jobs import generate_tag_suggestions

async def generate_tag_suggestions(ctx, image_id: int):
    # ... ML processing ...

    # Optionally notify user via websocket
    if ctx.get("websocket"):
        await ctx["websocket"].send_to_user(
            user_id=image.user_id,
            message={
                "type": "tag_suggestions_ready",
                "image_id": image_id,
                "pending_count": created_count
            }
        )
```

---

## Common Use Cases

### Use Case 1: Quick Review of High-Confidence Suggestions

**Goal:** Quickly approve obviously correct suggestions.

**Approach:**
1. Filter suggestions by confidence >= 0.85
2. Visually verify they match the image
3. Approve all in one batch

**Example:**
```bash
# Get high-confidence suggestions
GET /api/v1/images/{image_id}/tag-suggestions?status=pending

# Review response, approve high-confidence ones
POST /api/v1/images/{image_id}/tag-suggestions/review
{
  "suggestions": [
    {"suggestion_id": 1, "action": "approve"},  # 0.92 confidence
    {"suggestion_id": 2, "action": "approve"}   # 0.88 confidence
  ]
}
```

---

### Use Case 2: Reject Incorrect Suggestions

**Goal:** Filter out bad suggestions without applying them.

**When to Reject:**
- Suggestion is clearly wrong for the image
- Tag doesn't apply to this specific image
- Low confidence and uncertain

**Example:**
```bash
POST /api/v1/images/{image_id}/tag-suggestions/review
{
  "suggestions": [
    {"suggestion_id": 5, "action": "reject"}  # Model was wrong
  ]
}
```

**Note:** Rejected suggestions are tracked for ML model improvement.

---

### Use Case 3: Partial Approval

**Goal:** Approve some suggestions, reject others, ignore low-confidence ones.

**Approach:**
1. Approve clearly correct suggestions
2. Reject clearly wrong suggestions
3. Leave uncertain ones as "pending" for later review

**Example:**
```bash
POST /api/v1/images/{image_id}/tag-suggestions/review
{
  "suggestions": [
    {"suggestion_id": 1, "action": "approve"},  # Correct
    {"suggestion_id": 2, "action": "approve"},  # Correct
    {"suggestion_id": 3, "action": "reject"}    # Wrong
    # suggestion_id 4 & 5 left as pending
  ]
}
```

---

### Use Case 4: Review All Suggestions (Including Previously Reviewed)

**Goal:** See what was previously approved/rejected.

**Approach:**
1. Fetch suggestions without status filter
2. Review counts and individual statuses
3. Optionally change decisions (idempotent)

**Example:**
```bash
# Get ALL suggestions (any status)
GET /api/v1/images/{image_id}/tag-suggestions

# Response shows previously reviewed ones
{
  "image_id": 12345,
  "suggestions": [
    {..., "status": "approved", "reviewed_at": "2025-12-04T12:30:00Z"},
    {..., "status": "rejected", "reviewed_at": "2025-12-04T12:30:00Z"},
    {..., "status": "pending", "reviewed_at": null}
  ],
  "total": 3,
  "pending": 1,
  "approved": 1,
  "rejected": 1
}
```

---

## Best Practices

### For API Consumers

**1. Sort by Confidence**

Always present suggestions sorted by confidence (highest first) for better UX:

```javascript
suggestions.sort((a, b) => b.confidence - a.confidence);
```

**2. Batch Review**

Submit all review actions in one request rather than individual requests:

```javascript
// GOOD: One batch request
POST /api/v1/images/123/tag-suggestions/review
{
  "suggestions": [
    {"suggestion_id": 1, "action": "approve"},
    {"suggestion_id": 2, "action": "approve"}
  ]
}

// BAD: Multiple individual requests (slower)
POST /api/v1/images/123/tag-suggestions/review
{"suggestions": [{"suggestion_id": 1, "action": "approve"}]}

POST /api/v1/images/123/tag-suggestions/review
{"suggestions": [{"suggestion_id": 2, "action": "approve"}]}
```

**3. Handle Errors Gracefully**

Check the `errors` array in the response:

```javascript
const result = await reviewSuggestions(actions);

if (result.errors.length > 0) {
  console.error('Some suggestions failed:', result.errors);
  // Show errors to user
}

console.log(`Successfully approved: ${result.approved}`);
console.log(`Successfully rejected: ${result.rejected}`);
```

**4. Don't Auto-Approve Without User Confirmation**

Always require user interaction to approve suggestions. Never auto-approve even high-confidence suggestions without user review.

**5. Provide Context**

Show users:
- The image they're tagging
- Current tags already applied
- Confidence scores for suggestions
- Model source (which ML model made the suggestion)

---

### For Moderators

**1. Focus on High-Confidence Pending Suggestions**

Use filtering to find the best suggestions first:
- Status: pending
- Confidence: >= 0.8
- Sort by upload date (newest first)

**2. Review in Batches**

Process 10-20 images at a time rather than one-by-one.

**3. Trust High-Confidence Theme Tags**

Theme tags (like "long hair", "blue eyes") with confidence >= 0.85 are usually reliable.

**4. Be Cautious with Source/Character Tags**

Source and character tags may have lower accuracy. Verify before approving.

**5. Report Patterns**

If you notice a specific tag being suggested incorrectly frequently, report it for model retraining.

---

## Troubleshooting

### Issue: No Suggestions Generated

**Symptoms:**
- Uploaded image, but `/tag-suggestions` returns empty list
- `pending` count is 0

**Possible Causes:**
1. Background job hasn't completed yet (wait 30-60 seconds)
2. Background job failed (check logs)
3. ML models not loaded or configured
4. Image format not supported by ML models

**Debugging:**
```bash
# Check if suggestions exist (any status)
GET /api/v1/images/{image_id}/tag-suggestions

# If empty, check background job logs
# Look for "generate_tag_suggestions" job for this image_id
```

---

### Issue: All Suggestions Have Low Confidence

**Symptoms:**
- Suggestions generated but all have confidence < 0.6
- Few or no suggestions pass filter threshold

**Possible Causes:**
1. Image content doesn't match training data (unusual style, content)
2. Poor image quality (very small, blurry, corrupted)
3. Models not trained on this type of content

**Solution:**
- Manually tag the image
- These edge cases help improve the model through retraining

---

### Issue: Permission Denied When Reviewing

**Symptoms:**
- GET works but POST returns 403 Forbidden

**Possible Causes:**
1. User trying to review another user's image (not a moderator)
2. User lacks IMAGE_TAG_ADD permission
3. Authentication token expired

**Solution:**
- Verify user permissions: `has_permission(db, user_id, Permission.IMAGE_TAG_ADD)`
- Ensure user is image owner OR has moderator permissions
- Check authentication token validity

---

### Issue: Duplicate Tags After Approval

**Symptoms:**
- Approving suggestion for tag that's already applied

**Expected Behavior:**
- System prevents duplicate TagLinks automatically
- Suggestion is marked as approved but no duplicate created
- No error returned (this is normal)

**Explanation:**
The code checks for existing TagLinks before creating new ones:
```python
if (image_id, suggestion.tag_id) not in existing_links:
    tag_link = TagLinks(...)
    db.add(tag_link)
```

---

## Support and Feedback

For issues, questions, or feature requests related to the Tag Suggestion System:

1. Check the logs for background job errors
2. Review the design document: `docs/plans/2025-12-04-tag-suggestion-system-design.md`
3. Review the implementation plan: `docs/plans/2025-12-04-tag-suggestion-system-implementation.md`
4. File a bug report or feature request with your team

---

**Document Version:** 1.0
**Last Updated:** 2025-12-04
**Related Documents:**
- [Tag Suggestion System Design](plans/2025-12-04-tag-suggestion-system-design.md)
- [Tag Suggestion System Implementation](plans/2025-12-04-tag-suggestion-system-implementation.md)
