# Tag Suggestion System - Design Document

**Date:** 2025-12-04
**Status:** Design Complete, Ready for Implementation
**Author:** System Design (with User Input)

---

## Executive Summary

This document describes a machine learning-powered tag suggestion system for the Shuushuu API. The system will analyze uploaded images and suggest relevant tags (themes, sources, characters) to reduce manual tagging effort and improve tag consistency.

**Key Features:**
- Hybrid ML approach: Custom theme model + Pre-trained Danbooru model
- Post-upload background processing with human review workflow
- 1.1M images with 14M tag applications available for training
- Focus on Theme tags (359 tags, 76% of all applications)
- CPU-optimized inference on dedicated server

**Expected Impact:**
- Reduce manual tagging time by 40-60%
- Improve tag consistency across similar images
- Enable faster content discovery through better tagging

---

## Table of Contents

1. [Context & Motivation](#context--motivation)
2. [System Architecture](#system-architecture)
3. [ML Models & Inference Pipeline](#ml-models--inference-pipeline)
4. [Database Schema](#database-schema)
5. [API Endpoints](#api-endpoints)
6. [Background Processing](#background-processing)
7. [Review UI & Workflow](#review-ui--workflow)
8. [Deployment & Operations](#deployment--operations)
9. [Testing Strategy](#testing-strategy)
10. [Rollout Plan](#rollout-plan)
11. [Success Metrics](#success-metrics)
12. [Future Enhancements](#future-enhancements)

---

## Context & Motivation

### Current State

The Shuushuu API manages approximately:
- **1,095,377 images**
- **227,568 unique tags**
- **14,570,748 tag applications** (avg 13.3 tags per image)

Tags are categorized into 4 types:
1. **Theme** (359 tags) - Visual style, mood, composition - **76.28% of all applications**
2. **Source** (11,674 tags) - Anime/manga/game series - 6.46% of applications
3. **Character** (66,367 tags) - Specific characters - 10.72% of applications
4. **Artist** (149,168 tags) - Artwork creators - 6.54% of applications

### Problem Statement

All tags are currently applied manually by uploaders and moderators. This creates:
- **High manual effort** - Users must type/search for 10-15 tags per image
- **Inconsistency** - Similar images may have different tags
- **Incomplete tagging** - Users may miss relevant tags
- **Barrier to entry** - New users struggle with tag vocabulary

### Opportunity

Our analysis shows that:
- 5,033 tags have ≥100 examples (excellent training data)
- Theme tags dominate usage but represent tiny vocabulary (359 tags)
- Top 20 theme tags have 100K-700K examples each
- This is ideal for ML training

### Design Goals

1. **Reduce manual effort** - Auto-suggest tags based on image content
2. **Human-in-the-loop** - All suggestions require approval (no auto-tagging)
3. **High precision** - Only show high-confidence suggestions (minimize noise)
4. **Prioritize themes** - Focus on theme tags (76% of usage, easiest to predict)
5. **CPU-friendly** - Run on dedicated CPU server (no GPU requirement initially)
6. **Iterative rollout** - Start with moderators, expand to all users over time

---

## System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI Application                      │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │   Upload   │  │   Review     │  │  Tag Suggestion  │   │
│  │  Endpoint  │  │   Dashboard  │  │    API          │   │
│  └─────┬──────┘  └──────┬───────┘  └────────┬─────────┘   │
│        │                 │                    │              │
└────────┼─────────────────┼────────────────────┼──────────────┘
         │                 │                    │
         ↓                 ↓                    ↓
    ┌────────────────────────────────────────────────┐
    │            MariaDB Database                     │
    │  ┌──────────┐  ┌──────────────────┐           │
    │  │  Images  │  │  TagSuggestions  │ (new)     │
    │  │  Tags    │  │  SuggestionReview│ (new)     │
    │  │ TagLinks │  │  TagMappings     │ (new)     │
    │  └──────────┘  └──────────────────┘           │
    └────────────────────────────────────────────────┘
         ↑                                   ↑
         │                                   │
    ┌────┴───────────────────────────────────┴──────┐
    │           Arq Task Queue (Redis)              │
    │  ┌────────────────────────────────────────┐  │
    │  │  generate_tag_suggestions(image_id)    │  │
    │  │    ↓                                    │  │
    │  │  [Custom Theme Model] + [Danbooru]     │  │
    │  │    ↓                                    │  │
    │  │  Merge predictions & store in DB       │  │
    │  └────────────────────────────────────────┘  │
    └──────────────────────────────────────────────┘
```

### Key Components

1. **ML Inference Service** - Runs both models, merges predictions, resolves tag aliases/hierarchies
2. **Suggestion Storage** - New database tables for storing and tracking suggestions
3. **Review Interface** - UI for uploaders/moderators to approve/reject suggestions
4. **Background Processing** - Arq jobs trigger ML inference post-upload
5. **API Layer** - New endpoints for fetching and reviewing suggestions

### Integration Points

- **Upload Flow**: After image saved → Queue suggestion generation job
- **Review System**: Suggestions shown alongside manual tagging interface
- **Permissions**: Leverages existing `IMAGE_TAG_ADD` permission for approval
- **Tag System**: Respects tag aliases and hierarchies from `tags` table

---

## ML Models & Inference Pipeline

### Hybrid Two-Model Approach

We use two complementary models working together:

**Model 1: Custom Theme Classifier (Primary for Themes)**
- **Purpose**: Specialized for your 359 theme tags
- **Architecture**: EfficientNet-B0 or MobileNetV3 (CPU-optimized)
- **Training**: Fine-tune on your 1.1M images with theme tag labels
- **Output**: 359 theme tag probabilities
- **Inference Time**: ~500ms-1s per image on CPU
- **Format**: ONNX Runtime for optimized CPU inference

**Model 2: Pre-trained Danbooru Tagger (Fallback + Source/Character)**
- **Purpose**: Leverage external knowledge for source/character identification
- **Model**: WD14 Tagger v2 (SwinV2-based, proven for anime)
- **Training**: Pre-trained, no fine-tuning initially
- **Output**: ~10K tags across all types
- **Inference Time**: ~1-2s per image on CPU
- **Format**: ONNX or PyTorch

### Why This Hybrid Approach?

1. **Theme tags** (your priority, 76% of usage) → Custom model optimized for YOUR vocabulary and visual style
2. **Source/Character** (harder, long-tail distribution) → Danbooru's broader knowledge helps
3. **Fast deployment** → Use Danbooru immediately, train custom model over 2-4 weeks
4. **Best of both worlds** → High accuracy for themes, broad coverage for other types

### Inference Pipeline

```python
class TagSuggestionService:
    async def generate_suggestions(self, image_path: str) -> dict:
        # 1. Run both models in parallel (faster!)
        custom_preds, danbooru_preds = await asyncio.gather(
            self.custom_model.predict(image_path),
            self.danbooru_model.predict(image_path)
        )

        # 2. Filter by confidence thresholds
        theme_suggestions = self._filter_custom_themes(
            custom_preds,
            min_confidence=0.6
        )

        source_char_suggestions = self._filter_danbooru(
            danbooru_preds,
            min_confidence={
                "source": 0.7,
                "character": 0.75,
                "theme": 0.85  # Higher bar for Danbooru themes
            }
        )

        # 3. Map Danbooru tags to your vocabulary
        mapped_suggestions = self._map_tags(source_char_suggestions)

        # 4. Resolve tag aliases and hierarchies
        resolved = self._resolve_tag_relationships(mapped_suggestions)

        # 5. De-duplicate and merge
        final = self._merge_suggestions(
            theme_suggestions,
            resolved,
            prioritize_custom_themes=True
        )

        return final
```

### Tag Alias & Hierarchy Handling

The `tags` table has two important fields:
- `alias` - Points to canonical tag (this tag is a synonym)
- `inheritedfrom_id` - Points to parent tag (tag hierarchy)

**Resolution Strategy:**

1. **Alias Resolution**: If model predicts tag A (an alias), look up `tags.alias` → use canonical tag B instead
2. **Hierarchy Propagation**: Optionally suggest parent tags when child tags are predicted with high confidence
3. **Canonical Storage**: `tag_suggestions.tag_id` always references the canonical tag

```python
async def resolve_tag_relationships(
    db: AsyncSession,
    suggestions: list[dict]
) -> list[dict]:
    """Resolve tag aliases and hierarchies."""
    resolved = []

    for sugg in suggestions:
        tag = await db.get(Tags, sugg["tag_id"])

        # Resolve alias
        if tag.alias:
            sugg["tag_id"] = tag.alias  # Use canonical tag
            sugg["resolved_from_alias"] = True

        resolved.append(sugg)

        # Optionally add parent tag
        if tag.inheritedfrom_id and sugg["confidence"] > 0.7:
            parent_sugg = sugg.copy()
            parent_sugg["tag_id"] = tag.inheritedfrom_id
            parent_sugg["confidence"] *= 0.9  # Slightly lower
            parent_sugg["from_hierarchy"] = True
            resolved.append(parent_sugg)

    return resolved
```

### Model Storage & Versioning

```
/shuushuu/ml_models/
├── custom_theme/
│   ├── v1/
│   │   ├── model.onnx          # Model weights
│   │   ├── config.json         # Hyperparameters, thresholds
│   │   └── metrics.json        # Accuracy, precision, recall
│   └── v2/
│       └── ...
├── danbooru/
│   ├── wd14_v2/
│   │   ├── model.onnx
│   │   └── tags.txt            # Tag vocabulary
│   └── ...
└── tag_mappings.json           # Danbooru → Your tags mapping
```

Models are:
- Loaded once at worker startup
- Cached in memory for fast inference
- Hot-swappable without downtime via version tracking

---

## Database Schema

### New Tables

#### 1. `tag_suggestions` - Stores ML-generated suggestions

```sql
CREATE TABLE tag_suggestions (
    suggestion_id INT PRIMARY KEY AUTO_INCREMENT,
    image_id INT NOT NULL,
    tag_id INT NOT NULL,
    confidence FLOAT NOT NULL,  -- 0.0 to 1.0
    model_source ENUM('custom_theme', 'danbooru') NOT NULL,
    model_version VARCHAR(50) NOT NULL,  -- e.g., 'theme_v1', 'wd14_v2'
    status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP NULL,
    reviewed_by_user_id INT NULL,

    FOREIGN KEY (image_id) REFERENCES images(image_id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE,
    FOREIGN KEY (reviewed_by_user_id) REFERENCES users(user_id),

    -- Prevent duplicate suggestions for same image+tag
    UNIQUE KEY (image_id, tag_id),

    INDEX idx_image_status (image_id, status),
    INDEX idx_tag_status (tag_id, status),
    INDEX idx_pending (status, created_at)
);
```

**Key Design Decisions:**
- `UNIQUE(image_id, tag_id)` prevents duplicate suggestions
- `status` tracks lifecycle: pending → approved/rejected
- `model_source` enables A/B testing and comparison
- Cascade delete ensures cleanup when images/tags deleted

#### 2. `tag_mappings` - Maps external tags to your vocabulary

```sql
CREATE TABLE tag_mappings (
    mapping_id INT PRIMARY KEY AUTO_INCREMENT,
    external_tag VARCHAR(255) NOT NULL,  -- e.g., 'long_hair' from Danbooru
    external_source ENUM('danbooru', 'other') NOT NULL,
    internal_tag_id INT NULL,  -- Maps to tags.tag_id, NULL if no mapping
    confidence FLOAT DEFAULT 1.0,  -- Mapping confidence
    created_by_user_id INT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (internal_tag_id) REFERENCES tags(tag_id) ON DELETE SET NULL,

    UNIQUE KEY (external_source, external_tag),
    INDEX idx_internal_tag (internal_tag_id)
);
```

**Usage:**
- Initially populated with common mappings (e.g., `long_hair` → tag_id 46)
- Grows over time as moderators map unknown tags
- `NULL internal_tag_id` means "ignore this external tag"

#### 3. `ml_model_versions` - Track model deployments

```sql
CREATE TABLE ml_model_versions (
    version_id INT PRIMARY KEY AUTO_INCREMENT,
    model_name VARCHAR(100) NOT NULL,  -- 'custom_theme', 'danbooru'
    version VARCHAR(50) NOT NULL,  -- 'v1', 'v2', etc.
    file_path VARCHAR(500) NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,
    deployed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metrics JSON,  -- Store accuracy, precision, recall, etc.

    UNIQUE KEY (model_name, version),
    INDEX idx_active (model_name, is_active)
);
```

**Usage:**
- Workers query for `is_active=TRUE` to find current model
- Enables A/B testing by activating multiple versions
- Stores evaluation metrics for comparison

### SQLModel Schemas (Python)

```python
# app/models/tag_suggestion.py

from sqlmodel import SQLModel, Field, Column, Enum as SQLEnum
from datetime import datetime

class TagSuggestion(SQLModel, table=True):
    __tablename__ = "tag_suggestions"

    suggestion_id: int | None = Field(default=None, primary_key=True)
    image_id: int = Field(foreign_key="images.image_id")
    tag_id: int = Field(foreign_key="tags.tag_id")
    confidence: float = Field(ge=0.0, le=1.0)
    model_source: str = Field(
        sa_column=Column(SQLEnum('custom_theme', 'danbooru', name='model_source_enum'))
    )
    model_version: str = Field(max_length=50)
    status: str = Field(
        default='pending',
        sa_column=Column(SQLEnum('pending', 'approved', 'rejected', name='status_enum'))
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: datetime | None = None
    reviewed_by_user_id: int | None = Field(default=None, foreign_key="users.user_id")

class TagMapping(SQLModel, table=True):
    __tablename__ = "tag_mappings"

    mapping_id: int | None = Field(default=None, primary_key=True)
    external_tag: str = Field(max_length=255)
    external_source: str = Field(
        sa_column=Column(SQLEnum('danbooru', 'other', name='external_source_enum'))
    )
    internal_tag_id: int | None = Field(default=None, foreign_key="tags.tag_id")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    created_by_user_id: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class MLModelVersion(SQLModel, table=True):
    __tablename__ = "ml_model_versions"

    version_id: int | None = Field(default=None, primary_key=True)
    model_name: str = Field(max_length=100)
    version: str = Field(max_length=50)
    file_path: str = Field(max_length=500)
    is_active: bool = Field(default=False)
    deployed_at: datetime = Field(default_factory=datetime.utcnow)
    metrics: dict | None = Field(default=None, sa_column=Column(JSON))
```

---

## API Endpoints

All endpoints under `/api/v1/images/{image_id}/tag-suggestions` and `/api/v1/tag-suggestions`.

### 1. Get Suggestions for an Image

```
GET /api/v1/images/{image_id}/tag-suggestions
```

**Query Parameters:**
- `status` (optional): Filter by status (`pending`, `approved`, `rejected`) - default: `pending`

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
            "created_at": "2025-12-04T12:00:00Z"
        }
    ],
    "total": 15,
    "pending": 15,
    "approved": 0,
    "rejected": 0
}
```

**Permissions:**
- Image uploader can view their own suggestions
- Moderators can view all suggestions

### 2. Review Suggestions (Approve/Reject)

```
POST /api/v1/images/{image_id}/tag-suggestions/review
```

**Request Body:**
```json
{
    "suggestions": [
        {"suggestion_id": 1, "action": "approve"},
        {"suggestion_id": 2, "action": "reject"},
        {"suggestion_id": 3, "action": "approve"}
    ]
}
```

**Response:**
```json
{
    "approved": 2,
    "rejected": 1,
    "errors": []
}
```

**Behavior:**
- `approve` → Create `TagLink`, mark suggestion as `approved`
- `reject` → Mark suggestion as `rejected`
- Idempotent: Can re-approve already approved suggestions

**Permissions:**
- Requires `IMAGE_TAG_ADD` permission (uploaders + moderators + taggers)
- Users can only review suggestions for their own images (unless moderator)

### 3. Batch Operations

```
POST /api/v1/tag-suggestions/batch-review
```

**Request Body:**
```json
{
    "filters": {
        "status": "pending",
        "min_confidence": 0.8,
        "tag_ids": [46, 161, 25],
        "image_ids": [1, 2, 3]
    },
    "action": "approve"
}
```

**Response:**
```json
{
    "processed": 150
}
```

**Permissions:** MODERATOR only
**Use Case:** Bulk approve high-confidence suggestions

### 4. Statistics Endpoint

```
GET /api/v1/tag-suggestions/stats
```

**Response:**
```json
{
    "total_suggestions": 50000,
    "pending": 12000,
    "approved": 35000,
    "rejected": 3000,
    "approval_rate": 0.92,
    "by_model": {
        "custom_theme": {"pending": 5000, "approved": 20000},
        "danbooru": {"pending": 7000, "approved": 15000}
    },
    "top_suggested_tags": [
        {"tag_id": 46, "title": "long hair", "count": 15000}
    ]
}
```

**Permissions:** MODERATOR

### 5. Suggestion Status (Stretch Goal)

```
GET /api/v1/images/{image_id}/tag-suggestions/status
```

**Response:**
```json
{
    "status": "completed",
    "pending_count": 8,
    "job_id": "arq-job-uuid"
}
```

**Use Case:** Real-time polling for status banner ("Analyzing image...")

### Integration with Existing Endpoints

**Extend `GET /api/v1/images/{image_id}` response:**
```json
{
    "image_id": 12345,
    "pending_suggestions_count": 8,
    "has_pending_suggestions": true
}
```

**Extend `POST /api/v1/images` (upload) response:**
```json
{
    "image_id": 12345,
    "suggestion_job_id": "arq-job-uuid",
    "suggestion_status": "queued"
}
```

---

## Background Processing

### New Arq Job: `generate_tag_suggestions`

**Purpose:** Generate ML tag suggestions for an uploaded image.

**Trigger:** Automatically enqueued after image upload and processing completes.

**Implementation:**

```python
# app/tasks/ml_jobs.py

async def generate_tag_suggestions(
    ctx: dict,
    image_id: int,
    force_regenerate: bool = False
) -> dict:
    """
    Generate ML tag suggestions for an image.

    Workflow:
    1. Fetch image from database
    2. Check if suggestions already exist (skip if not force_regenerate)
    3. Run ML inference (both models in parallel)
    4. Resolve tag aliases and hierarchies
    5. Filter by confidence thresholds
    6. Store in database
    """
    async with get_async_session() as db:
        # 1. Fetch image
        image = await db.get(Images, image_id)
        if not image:
            raise ValueError(f"Image {image_id} not found")

        # 2. Check if suggestions already exist
        if not force_regenerate:
            existing = await db.execute(
                select(TagSuggestion)
                .where(TagSuggestion.image_id == image_id)
            )
            if existing.scalars().first():
                return {"status": "skipped", "reason": "suggestions_exist"}

        # 3. Get image file path
        image_path = get_image_path(image.filename, image.ext)

        # 4. Run ML inference
        ml_service = ctx["ml_service"]  # Injected at worker startup
        suggestions = await ml_service.generate_suggestions(image_path)

        # 5. Resolve aliases and hierarchy
        suggestions = await resolve_tag_relationships(db, suggestions)

        # 6. Filter by confidence thresholds
        filtered = filter_by_confidence(
            suggestions,
            min_theme=0.6,
            min_source=0.7,
            min_character=0.75
        )

        # 7. Store in database
        created_count = 0
        for sugg in filtered:
            tag_suggestion = TagSuggestion(
                image_id=image_id,
                tag_id=sugg["tag_id"],
                confidence=sugg["confidence"],
                model_source=sugg["model_source"],
                model_version=sugg["model_version"],
                status="pending"
            )
            db.add(tag_suggestion)
            created_count += 1

        await db.commit()

        return {
            "status": "completed",
            "suggestions_created": created_count,
            "image_id": image_id
        }
```

### Integration with Upload Flow

```python
# app/api/v1/images.py (upload endpoint)

@router.post("/", response_model=ImageResponse)
async def upload_image(
    # ... existing params ...
    queue: ArqRedis = Depends(get_arq_queue)
):
    # ... existing upload logic ...

    # After image saved:
    # 1. Generate variants (existing)
    await queue.enqueue_job("generate_image_variants", image_id=image.image_id)

    # 2. Add to IQDB (existing)
    await queue.enqueue_job("add_to_iqdb", image_id=image.image_id)

    # 3. Generate tag suggestions (NEW)
    suggestion_job = await queue.enqueue_job(
        "generate_tag_suggestions",
        image_id=image.image_id,
        _defer_by=30  # Wait 30s for image processing to finish
    )

    return ImageResponse(
        # ... existing fields ...
        suggestion_job_id=suggestion_job.job_id,
        suggestion_status="queued"
    )
```

### Worker Configuration

```python
# app/tasks/worker.py

from app.services.ml_service import MLTagSuggestionService

async def startup(ctx: dict):
    """Load ML models at worker startup"""
    ctx["ml_service"] = MLTagSuggestionService()
    await ctx["ml_service"].load_models()
    logger.info("ML models loaded into memory")


async def shutdown(ctx: dict):
    """Cleanup resources"""
    if "ml_service" in ctx:
        await ctx["ml_service"].cleanup()


class WorkerSettings:
    functions = [
        # Existing jobs
        generate_image_variants,
        add_to_iqdb,
        recalculate_rating,
        # New job
        generate_tag_suggestions,
    ]

    on_startup = startup
    on_shutdown = shutdown

    # Adjust concurrency for ML workload
    max_jobs = 4  # Limit concurrent ML jobs (memory-intensive)
    job_timeout = 300  # 5 minutes (ML inference can be slow)
```

### Job Configuration

```python
# Tag suggestions are lower priority than critical jobs
await queue.enqueue_job(
    "generate_tag_suggestions",
    image_id=image_id,
    _job_try=3,  # Retry up to 3 times on failure
    _defer_by=30,  # Wait 30s to let image processing finish
)
```

---

## Review UI & Workflow

### User Workflows

#### Workflow 1: Uploader Reviews Their Own Image

1. User uploads image → receives `image_id` and `suggestion_job_id`
2. Background job processes image (~30-60 seconds)
3. **Status Banner (Stretch Goal):** "🤖 Analyzing image for tag suggestions..."
4. User navigates to image detail page
5. Sees "Tag Suggestions" section with pending suggestions
6. Reviews suggestions: clicks checkboxes to select, clicks "Approve Selected" or "Reject Selected"
7. Approved suggestions → create `TagLinks` immediately
8. User can still manually add additional tags via existing tag interface

#### Workflow 2: Moderator Batch Review

1. Moderator navigates to "Pending Tag Suggestions" dashboard
2. Sees list of images with pending suggestions, sorted by upload date
3. Can filter by:
   - Confidence threshold (e.g., show only >0.8)
   - Tag type (Theme, Source, Character)
   - Model source (Custom, Danbooru)
   - Specific tags
4. Reviews suggestions for multiple images
5. Can "Approve All High Confidence" (batch operation via API)

### UI Components (Frontend Integration)

#### Image Detail Page - Tag Suggestions Section

```
┌─────────────────────────────────────────────────┐
│ Image #12345                                    │
│ [Image Preview]                                 │
│                                                 │
│ Current Tags: long hair, blue eyes, smile      │
│                                                 │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│ 📌 Suggested Tags (8 pending)                   │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                                 │
│ Theme Tags:                                     │
│ ☑ blonde hair       [92%] [Custom Model]       │
│ ☑ red eyes          [88%] [Custom Model]       │
│ ☐ school uniform    [75%] [Custom Model]       │
│ ☐ happy             [68%] [Danbooru]           │
│                                                 │
│ Source Tags:                                    │
│ ☐ Fate/Stay Night   [82%] [Danbooru]           │
│                                                 │
│ Character Tags:                                 │
│ ☐ Saber             [79%] [Danbooru]           │
│                                                 │
│ [Approve Selected (2)] [Reject Selected]       │
│ [Approve All] [Reject All]                     │
└─────────────────────────────────────────────────┘
```

#### Moderator Dashboard - Batch Review

```
┌─────────────────────────────────────────────────┐
│ Tag Suggestions Dashboard                       │
│                                                 │
│ Filters: [Min Conf: 0.7▼] [Type: All▼]        │
│          [Model: All▼] [Status: Pending▼]     │
│                                                 │
│ [Approve All >90% Confidence] [Export CSV]     │
│                                                 │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                                 │
│ Image #12345 | 8 suggestions | User: alice     │
│   ☑ blonde hair (92%) ☑ red eyes (88%) ...     │
│   [Review →]                                    │
│                                                 │
│ Image #12346 | 5 suggestions | User: bob       │
│   ☑ long hair (95%) ☑ smile (87%) ...          │
│   [Review →]                                    │
│                                                 │
│ ... (paginated)                                 │
│                                                 │
│ Showing 1-20 of 1,250 images with pending       │
│ suggestions                                     │
└─────────────────────────────────────────────────┘
```

### Real-Time Status Notification (Stretch Goal)

**Implementation:**

**Option A: Polling (Simple)**
```javascript
// On image detail page, poll for suggestion status
const checkSuggestionStatus = async (imageId) => {
  const response = await fetch(
    `/api/v1/images/${imageId}/tag-suggestions/status`
  );
  const { status, pending_count } = await response.json();

  if (status === 'processing') {
    showBanner('🤖 Analyzing image for tag suggestions...');
  } else if (status === 'completed' && pending_count > 0) {
    showBanner(`✨ ${pending_count} tag suggestions ready!`, 'success');
  }
};

// Poll every 5 seconds for first 2 minutes after upload
```

**Option B: WebSocket (Better, if infrastructure exists)**
```python
# Arq job sends WebSocket message when complete
async def generate_tag_suggestions(ctx, image_id):
    # ... ML processing ...

    # Notify user via WebSocket
    await ctx["websocket"].send_to_user(
        user_id=image.user_id,
        message={
            "type": "tag_suggestions_ready",
            "image_id": image_id,
            "count": created_count
        }
    )
```

**UI Banner:**
```html
<!-- Initially after upload -->
┌─────────────────────────────────────────────────┐
│ ℹ️ Analyzing image for tag suggestions...       │
│    [Progress indicator]                         │
└─────────────────────────────────────────────────┘

<!-- After completion -->
┌─────────────────────────────────────────────────┐
│ ✨ 8 tag suggestions ready! [Review Now →]      │
└─────────────────────────────────────────────────┘
```

---

## Deployment & Operations

### Model Deployment Strategy

#### Phase 1: Danbooru Model Only (Weeks 1-2)

- Deploy WD14 Tagger with pre-trained weights
- Test end-to-end pipeline with real uploads
- Gather baseline metrics (precision, user approval rate)
- Build tag mapping table manually for common tags

#### Phase 2: Custom Theme Model (Weeks 3-6)

- Train custom theme classifier on your 359 tags
- Deploy alongside Danbooru model
- A/B test: compare suggestions from both models
- Tune confidence thresholds based on approval rates

#### Phase 3: Optimization (Weeks 7+)

- Fine-tune Danbooru model on your full dataset (optional)
- Optimize inference speed (ONNX, quantization)
- Expand to more user roles (taggers)
- Consider GPU deployment if budget allows

### Infrastructure Requirements

**Dedicated ML Server:**

```yaml
CPU: 8+ cores (for parallel inference)
RAM: 16-32 GB (models + concurrent jobs)
Storage: 50 GB (models, cache, logs)
OS: Ubuntu 22.04 LTS

Software Stack:
- Python 3.12+
- PyTorch 2.0+ or ONNX Runtime
- Redis (shared with main app)
- Docker (optional containerization)
```

### Model Storage & Versioning

```
/shuushuu/ml_models/
├── custom_theme/
│   ├── v1/
│   │   ├── model.onnx
│   │   ├── config.json
│   │   └── metrics.json
│   └── v2/
│       └── ...
├── danbooru/
│   ├── wd14_v2/
│   │   ├── model.onnx
│   │   └── tags.txt
│   └── ...
└── tag_mappings.json
```

### Monitoring & Metrics

#### Key Metrics to Track

**1. Suggestion Quality**
- Approval rate by model (target: >70%)
- Approval rate by tag type (Theme, Source, Character)
- Precision @ K (top 5, top 10 suggestions)
- False positive rate (rejected suggestions)

**2. Performance**
- Inference latency (target: <2s per image)
- Queue wait time (time from upload to suggestion generation)
- Job failure rate (target: <5%)
- CPU/memory usage

**3. Usage**
- Suggestions generated per day
- Suggestions reviewed per day
- Time from upload to first review
- % of images with pending suggestions

#### Logging

```python
# Log every suggestion generation
logger.info(
    "tag_suggestions_generated",
    image_id=image_id,
    suggestions_count=count,
    inference_time_ms=elapsed,
    model_versions={
        "custom_theme": "v1",
        "danbooru": "wd14_v2"
    }
)

# Log every review action
logger.info(
    "tag_suggestion_reviewed",
    suggestion_id=suggestion_id,
    action="approved|rejected",
    user_id=user_id,
    confidence=confidence,
    model_source=model_source
)
```

#### Alerts

- Inference latency > 5s for 5 minutes
- Job failure rate > 10%
- Approval rate drops below 50% (model degradation)
- Queue depth > 100 images (capacity issue)

### Model Updates

**Hot-Swap Models Without Downtime:**

```python
async def deploy_new_model(model_name: str, version: str):
    """
    1. Upload new model files to /shuushuu/ml_models/
    2. Update ml_model_versions table
    3. Set is_active=True for new version
    4. Workers pick up new version on next job
    5. Old model stays loaded until in-flight jobs complete
    """
    pass
```

**Rollback Strategy:**

```sql
-- If new model performs poorly, rollback
UPDATE ml_model_versions
SET is_active = FALSE
WHERE model_name = 'custom_theme' AND version = 'v2';

UPDATE ml_model_versions
SET is_active = TRUE
WHERE model_name = 'custom_theme' AND version = 'v1';

-- Workers reload models within 1 minute
```

---

## Testing Strategy

### Unit Tests

**1. ML Service Tests**

```python
# tests/services/test_ml_service.py

async def test_custom_model_inference():
    """Test custom theme model generates predictions"""
    service = MLTagSuggestionService()
    await service.load_models()

    predictions = await service.custom_model.predict(
        "tests/fixtures/sample_image.jpg"
    )

    assert len(predictions) > 0
    assert all(0 <= p["confidence"] <= 1 for p in predictions)
    assert all(p["tag_id"] in VALID_THEME_TAG_IDS for p in predictions)

async def test_resolve_tag_alias():
    """Test that aliases are resolved to canonical tags"""
    suggestions = [{"tag_id": 100, "confidence": 0.8}]

    resolved = await resolve_tag_relationships(db, suggestions)

    assert resolved[0]["tag_id"] == 46  # Canonical tag
    assert resolved[0]["resolved_from_alias"] is True
```

**2. API Endpoint Tests**

```python
# tests/api/test_tag_suggestions.py

async def test_approve_suggestion_creates_tag_link(client, db):
    """Test that approving a suggestion creates a TagLink"""
    image = create_test_image(db)
    suggestion = create_test_suggestion(db, image.image_id, tag_id=46)

    response = await client.post(
        f"/api/v1/images/{image.image_id}/tag-suggestions/review",
        json={"suggestions": [{"suggestion_id": suggestion.suggestion_id, "action": "approve"}]},
        headers=auth_headers(image.user_id)
    )

    assert response.status_code == 200

    # Verify TagLink created
    tag_link = await db.execute(
        select(TagLinks).where(
            TagLinks.image_id == image.image_id,
            TagLinks.tag_id == 46
        )
    )
    assert tag_link.scalar_one_or_none() is not None
```

**3. Background Job Tests**

```python
# tests/tasks/test_ml_jobs.py

async def test_generate_suggestions_job(arq_redis, db):
    """Test tag suggestion generation job"""
    image = create_test_image(db)

    result = await generate_tag_suggestions(
        {"ml_service": mock_ml_service},
        image_id=image.image_id
    )

    assert result["status"] == "completed"
    assert result["suggestions_created"] > 0
```

### Integration Tests

```python
# tests/integration/test_full_workflow.py

async def test_end_to_end_suggestion_workflow(client, db, arq_worker):
    """Test complete workflow from upload to approval"""
    # 1. Upload image
    response = await client.post("/api/v1/images", ...)
    image_id = response.json()["image_id"]

    # 2. Wait for suggestion job
    await arq_worker.run_check()

    # 3. Fetch suggestions
    response = await client.get(f"/api/v1/images/{image_id}/tag-suggestions")
    suggestions = response.json()["suggestions"]

    # 4. Approve suggestions
    response = await client.post(f"/api/v1/images/{image_id}/tag-suggestions/review", ...)
    assert response.json()["approved"] == 3
```

### Performance Tests

```python
# tests/performance/test_inference_speed.py

async def test_inference_latency():
    """Test that ML inference completes within acceptable time"""
    service = MLTagSuggestionService()
    await service.load_models()

    start = time.time()
    await service.generate_suggestions("tests/fixtures/sample_image.jpg")
    elapsed = time.time() - start

    assert elapsed < 3.0, f"Inference took {elapsed}s, target is <3s"
```

### Test Data

- **Fixture images**: 50-100 sample anime images with known tags
- **Test database**: Subset of tags (top 100 most common)
- **Mock models**: Fast dummy models for unit tests
- **Real models**: For integration tests only

---

## Rollout Plan

### Phase 1: Internal Testing (Week 1-2)

- Deploy to staging environment
- Test with admin/moderator accounts only
- Upload 100 test images, review all suggestions
- Tune confidence thresholds based on results
- Fix bugs, iterate on UI

**Exit Criteria:**
- Zero critical bugs
- Approval rate >50% on test images
- Inference latency <3s

### Phase 2: Beta Release - Moderators Only (Week 3-4)

- Enable suggestions for all new uploads
- Only moderators can see/review suggestions
- Monitor metrics: approval rate, latency, job failures
- Gather feedback on UI/UX
- **Target: >60% approval rate**

**Exit Criteria:**
- Approval rate >60%
- Job failure rate <5%
- Positive moderator feedback

### Phase 3: Expand to Uploaders (Week 5-6)

- Enable suggestion review for image uploaders
- Each uploader sees suggestions only for their own images
- Email notification: "Your image has tag suggestions ready"
- Continue monitoring approval rates
- **Target: >70% approval rate**

**Exit Criteria:**
- Approval rate >70%
- <5 support tickets per week
- Users actively reviewing suggestions

### Phase 4: Expand to Taggers (Week 7-8)

- Enable review for users with TAGGER permission level
- Taggers can review any image's suggestions
- Add "Pending Suggestions" dashboard for batch review
- Consider gamification (leaderboard for most reviews)

**Exit Criteria:**
- Active engagement from taggers
- >80% of new images have suggestions reviewed within 48 hours

### Phase 5: Optimization & Scale (Week 9+)

- Deploy custom theme model (if trained)
- A/B test: compare custom vs. Danbooru approval rates
- Consider auto-approving very high confidence suggestions (>95%)
- Analyze tag coverage: which tags are well-predicted, which aren't
- Retrain models based on approved/rejected feedback

---

## Success Metrics

### Must-Have (Launch Blockers)

- ✅ Inference completes within 3s per image
- ✅ Job failure rate < 5%
- ✅ Approval rate > 60% overall
- ✅ No data loss or corruption
- ✅ Permissions work correctly (users can't review others' images)

### Nice-to-Have (Post-Launch Improvements)

- ✅ Approval rate > 80%
- ✅ Real-time status notifications working
- ✅ Custom theme model deployed
- ✅ Dashboard analytics for moderators

### Long-Term Goals (3-6 months)

- 40-60% reduction in manual tagging time
- 80%+ approval rate on theme tags
- 70%+ approval rate on source/character tags
- 100K+ tags added via suggestions
- Users report improved tagging experience

---

## Future Enhancements

### Short-Term (3-6 months)

1. **Auto-approve high confidence** - Suggestions >95% confidence auto-applied, with notification
2. **Feedback loop** - Use approved/rejected suggestions to retrain models
3. **Tag similarity** - Suggest related tags based on co-occurrence patterns
4. **Batch upload** - Generate suggestions for multiple images at once

### Medium-Term (6-12 months)

1. **Active learning** - Prioritize uncertain suggestions for review (improves training data)
2. **User feedback integration** - "This suggestion is wrong because..." → improve model
3. **Multi-model voting** - Run 3+ models, use ensemble voting for higher accuracy
4. **GPU acceleration** - Deploy GPU server for faster inference if budget allows

### Long-Term (12+ months)

1. **Zero-shot tagging** - Add new tags without retraining via CLIP-style models
2. **Multi-image context** - Suggest tags based on user's upload history
3. **Interactive tagging** - "Add more blonde characters" → refine suggestions
4. **Video/GIF support** - Extend to animated content

---

## Appendix: Data Analysis

### Tag Distribution Analysis Results

**Dataset Overview:**
- Total images: 1,095,377
- Total unique tags: 227,568
- Total tag applications: 14,570,748
- Avg tags per image: 13.3

**Tag Type Distribution:**
| Type      | Unique Tags | % of Total | Applications | % of Apps |
|-----------|-------------|------------|--------------|-----------|
| Theme     | 359         | 0.16%      | 11,115,190   | 76.28%    |
| Source    | 11,674      | 5.13%      | 941,394      | 6.46%     |
| Artist    | 149,168     | 65.55%     | 952,886      | 6.54%     |
| Character | 66,367      | 29.16%     | 1,561,278    | 10.72%    |

**Key Insight:** Theme tags are only 0.16% of unique tags but represent 76% of all usage!

**Training Viability:**
- Tags with ≥50 examples: 9,847 (4.33%)
- Tags with ≥100 examples: 5,033 (2.21%) ← **Excellent for training**
- Tags with ≥500 examples: 1,084 (0.48%)
- Tags with ≥1,000 examples: 641 (0.28%)

**Top 10 Most Common Tags (All Theme):**
1. long hair - 723,812 applications
2. short hair - 471,938
3. blush - 400,360
4. ribbon - 360,061
5. smile - 350,411
6. dress - 308,549
7. blue eyes - 281,640
8. blonde hair - 277,540
9. brown hair - 266,378
10. happy - 249,036

---

**End of Design Document**

This design has been validated through collaborative brainstorming and is ready for implementation planning.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
