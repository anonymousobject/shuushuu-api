# Implementation Example: Embed User in Image Response

## Quick Implementation Guide

This shows exactly what to change to embed user data in image responses.

---

## Step 1: Create UserSummary Schema

**File**: `shuushuu-api/app/schemas/image.py`

```python
# Add at the top of the file, after existing schemas
class UserSummary(BaseModel):
    """
    Minimal user information for embedding in other responses.

    Used to avoid N+1 queries when clients need basic user info
    without fetching the full user profile.
    """
    user_id: int
    username: str
```

---

## Step 2: Add to ImageResponse

**File**: `shuushuu-api/app/schemas/image.py`

```python
class ImageResponse(ImageBase):
    """
    Schema for image response - what API returns.

    Inherits public fields from ImageBase and adds additional public metadata.
    Does NOT include internal fields like IP, user agent, etc.
    """

    image_id: int
    user_id: int
    date_added: datetime | None = None
    status: int
    locked: int
    posts: int
    favorites: int
    bayesian_rating: float
    num_ratings: int

    # ADD THIS LINE:
    user: UserSummary  # Embedded user data to avoid N+1 queries

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        """Generate image URL"""
        return f"/storage/fullsize/{self.filename}.{self.ext}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def thumbnail_url(self) -> str:
        """Generate thumbnail URL"""
        return f"/storage/thumbs/{self.filename}.jpeg"
```

---

## Step 3: Update Database Model

**File**: `shuushuu-api/app/models/image.py`

Check if the relationship already exists:

```python
class Images(ImageBase, table=True):
    # ... existing fields ...

    # ADD THIS if it doesn't exist:
    user: "Users" = Relationship(back_populates="images")
```

---

## Step 4: Eager Load User in Queries

**File**: `shuushuu-api/app/api/v1/images.py`

### For list_images endpoint:

```python
from sqlalchemy.orm import selectinload

@router.get("/", response_model=ImageListResponse)
async def list_images(
    status: int = 1,
    page: int = 1,
    per_page: int = 20,
    sort_by: ImageSortBy = ImageSortBy.image_id,
    sort_order: Literal["ASC", "DESC"] = "DESC",
    db: AsyncSession = Depends(get_db),
) -> ImageListResponse:
    """
    List images with pagination and sorting.
    """
    # Build query
    query = select(Images).where(Images.status == status)

    # ADD THIS LINE - eager load user to avoid N+1:
    query = query.options(selectinload(Images.user))

    # Apply sorting
    column = sort_by.get_column(Images)
    query = query.order_by(
        column.desc() if sort_order == "DESC" else column.asc()
    )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    images = result.scalars().all()

    return ImageListResponse(
        total=total,
        page=page,
        per_page=per_page,
        images=images,
    )
```

### For get_image endpoint:

```python
@router.get("/{image_id}", response_model=ImageResponse)
async def get_image(
    image_id: int,
    db: AsyncSession = Depends(get_db)
) -> ImageResponse:
    """Get a single image by ID."""

    result = await db.execute(
        select(Images)
        .options(selectinload(Images.user))  # ADD THIS LINE
        .where(Images.image_id == image_id)
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    return ImageResponse.model_validate(image)
```

---

## Step 5: Regenerate Frontend Types

```bash
cd shuushuu-frontend
npm run generate:api
```

Types will automatically update to:

```typescript
export interface ImageResponse {
  image_id: number;
  user_id: number;
  user: {            // New field!
    user_id: number;
    username: string;
  };
  // ... other fields
}
```

---

## Step 6: Use in Frontend

**File**: `shuushuu-frontend/src/routes/images/+page.svelte`

```svelte
<script lang="ts">
  import type { PageData } from './$types';
  import type { ImageResponse } from '$lib/types/api';

  export let data: PageData;
  const images: ImageResponse[] = data.images;
</script>

{#each images as img (img.image_id)}
  <figure class="card">
    <a href="/images/{img.image_id}">
      <img src={img.thumbnail_url} alt={`Image ${img.image_id}`} />
    </a>
    <figcaption>
      <div><strong>Image ID:</strong> {img.image_id}</div>
      <div><strong>User:</strong> {img.user.username}</div>  <!-- NEW! -->
      <div><strong>User ID:</strong> {img.user_id}</div>
    </figcaption>
  </figure>
{/each}
```

---

## Testing

### Test the API

```bash
curl http://localhost:8000/api/v1/images/ | jq '.images[0]'
```

**Expected output**:
```json
{
  "image_id": 12345,
  "user_id": 82710,
  "user": {
    "user_id": 82710,
    "username": "alice"
  },
  "url": "/storage/fullsize/image.jpg",
  "thumbnail_url": "/storage/thumbs/image.jpg"
}
```

### Test Frontend Types

```bash
cd shuushuu-frontend
npm run check
```

Should pass with no errors.

---

## Performance Impact

### Database Query Change

**Before**:
```sql
SELECT * FROM images WHERE status = 1 LIMIT 20;
-- 1 query
```

**After**:
```sql
SELECT images.*, users.user_id, users.username
FROM images
LEFT JOIN users ON images.user_id = users.user_id
WHERE images.status = 1
LIMIT 20;
-- Still 1 query! (with selectinload, SQLAlchemy optimizes this)
```

### Payload Size Change

**Before**: ~250 bytes per image
**After**: ~270 bytes per image (+8%)

For 20 images: +400 bytes total

---

## Alternative: Flexible Includes (Advanced)

If you want optional embedding:

```python
@router.get("/", response_model=ImageListResponse)
async def list_images(
    include: str | None = None,  # e.g., "user"
    db: AsyncSession = Depends(get_db),
):
    query = select(Images)

    # Only load user if requested
    if include and "user" in include.split(","):
        query = query.options(selectinload(Images.user))

    # ... rest of query
```

**Usage**:
```bash
# Without user
GET /api/v1/images/

# With user
GET /api/v1/images/?include=user
```

---

## Summary

**Changes Required**:
1. Add `UserSummary` schema (3 lines)
2. Add `user: UserSummary` to `ImageResponse` (1 line)
3. Add `.options(selectinload(Images.user))` to queries (1 line per endpoint)
4. Regenerate frontend types (`npm run generate:api`)
5. Use `img.user.username` in components

**Total**: ~5 lines of backend code

**Benefit**: Eliminates N+1 queries, makes frontend development much easier.
