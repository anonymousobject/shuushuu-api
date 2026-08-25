# Tag External Links Design

**Date:** 2025-12-25
**Status:** Approved

## Overview

Add support for external links (websites, social media profiles, etc.) to tags, primarily for source and artist tag types. Links are stored in a separate table and managed through dedicated API endpoints.

## Requirements

- Tags can have multiple external URLs (0 to many)
- URLs should be validated to require http:// or https:// protocol
- Prevent duplicate URLs within a single tag
- Links appear in tag detail view (`GET /tags/{tag_id}`) but not in tag lists
- Managing links requires TAG_UPDATE permission
- All tag types can have links (not restricted to source/artist)

## Database Schema

### New Table: `tag_external_links`

```sql
CREATE TABLE tag_external_links (
    link_id INT AUTO_INCREMENT PRIMARY KEY,
    tag_id INT NOT NULL,
    url VARCHAR(2000) NOT NULL,
    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE,
    UNIQUE KEY unique_tag_url (tag_id, url),
    INDEX idx_tag_id (tag_id)
);
```

**Design Decisions:**
- `link_id`: Auto-increment primary key for addressing individual links
- `tag_id`: Foreign key with CASCADE delete (if tag is deleted, links are removed)
- `url`: VARCHAR(2000) supports most URLs
- `date_added`: Timestamp for ordering and audit trail
- **UNIQUE constraint on (tag_id, url)**: Prevents duplicate URLs within a tag
- **Index on tag_id**: Fast lookups when fetching links for a tag

**Future Extensibility:**
Schema is minimal but can be extended later with:
- `label` VARCHAR(100) for categorizing links (Twitter, Pixiv, Official Site, etc.)
- `display_order` INT for explicit ordering control
- `verified` BOOLEAN for marking verified/official links

## SQLModel Models

**File:** `app/models/tag_external_link.py`

```python
"""
SQLModel-based TagExternalLink models with inheritance for security

TagExternalLinkBase (shared public fields)
    ├─> TagExternalLinks (database table, adds internal fields)
    └─> API schemas (defined in app/schemas)
"""

from datetime import UTC, datetime
from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


class TagExternalLinkBase(SQLModel):
    """
    Base model with shared public fields for tag external links.

    These fields are safe to expose via the API.
    """
    url: str = Field(max_length=2000)


class TagExternalLinks(TagExternalLinkBase, table=True):
    """
    Database table for tag external links.

    Stores URLs associated with tags (artist sites, social media, etc.)
    """

    __tablename__ = "tag_external_links"

    __table_args__ = (
        Index("idx_tag_id", "tag_id"),
        Index("unique_tag_url", "tag_id", "url", unique=True),
    )

    # Primary key
    link_id: int | None = Field(default=None, primary_key=True)

    # Foreign key
    tag_id: int = Field(foreign_key="tags.tag_id", index=True)

    # Timestamp
    date_added: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column_kwargs={"server_default": text("current_timestamp()")},
    )
```

**Pattern Notes:**
- Follows existing model inheritance pattern (TagBase → Tags)
- No relationship objects (matches Tags model philosophy)
- Unique constraint enforced via `__table_args__`

## API Schemas

**File:** `app/schemas/tag.py`

### New Schemas

```python
class TagExternalLinkCreate(BaseModel):
    """Schema for adding a new external link to a tag"""

    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL has http/https protocol and trim whitespace."""
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if len(v) > 2000:
            raise ValueError("URL exceeds maximum length of 2000 characters")
        return v


class TagExternalLinkResponse(BaseModel):
    """Schema for tag external link response"""

    link_id: int
    url: str
    date_added: datetime

    model_config = {"from_attributes": True}
```

### Updated Schema

```python
class TagWithStats(TagResponse):
    """Schema for tag response with usage statistics"""

    image_count: int
    is_alias: bool = False
    aliased_tag_id: int | None = None
    parent_tag_id: int | None = None
    child_count: int = 0
    created_by: TagCreator | None = None
    date_added: datetime
    links: list[str] = []  # NEW: List of external URLs
```

**Validation Strategy:**
- URL protocol validation at Pydantic schema level
- Length validation at schema level (2000 chars)
- Uniqueness validation at database level (UNIQUE constraint)

## API Endpoints

**File:** `app/api/v1/tags.py`

### New Endpoints

#### `POST /tags/{tag_id}/links`

Add an external link to a tag.

**Request:**
```json
{
  "url": "https://twitter.com/artist_name"
}
```

**Response:** 201 Created
```json
{
  "link_id": 123,
  "url": "https://twitter.com/artist_name",
  "date_added": "2025-12-25T10:30:00Z"
}
```

**Errors:**
- 404: Tag not found
- 409: URL already exists for this tag
- 403: Missing TAG_UPDATE permission

**Implementation:**
```python
@router.post("/{tag_id}/links", response_model=TagExternalLinkResponse, status_code=201)
async def add_tag_link(
    tag_id: Annotated[int, Path(description="Tag ID")],
    link_data: TagExternalLinkCreate,
    _: Annotated[None, Depends(require_permission(Permission.TAG_UPDATE))],
    db: AsyncSession = Depends(get_db),
) -> TagExternalLinkResponse:
    """Add an external link to a tag."""
    # Verify tag exists
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))
    if not tag_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Tag not found")

    # Create new link
    new_link = TagExternalLinks(tag_id=tag_id, url=link_data.url)
    db.add(new_link)

    try:
        await db.commit()
        await db.refresh(new_link)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="URL already exists for this tag")

    return TagExternalLinkResponse.model_validate(new_link)
```

#### `DELETE /tags/{tag_id}/links/{link_id}`

Remove an external link from a tag.

**Response:** 204 No Content

**Errors:**
- 404: Link not found or doesn't belong to this tag
- 403: Missing TAG_UPDATE permission

**Implementation:**
```python
@router.delete("/{tag_id}/links/{link_id}", status_code=204)
async def delete_tag_link(
    tag_id: Annotated[int, Path(description="Tag ID")],
    link_id: Annotated[int, Path(description="Link ID")],
    _: Annotated[None, Depends(require_permission(Permission.TAG_UPDATE))],
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove an external link from a tag."""
    # Fetch and verify link belongs to this tag
    link_result = await db.execute(
        select(TagExternalLinks).where(
            TagExternalLinks.link_id == link_id,
            TagExternalLinks.tag_id == tag_id,
        )
    )
    link = link_result.scalar_one_or_none()

    if not link:
        raise HTTPException(
            status_code=404,
            detail="Link not found or does not belong to this tag"
        )

    await db.delete(link)
    await db.commit()
```

### Updated Endpoint

#### `GET /tags/{tag_id}`

Modified to include external links in response.

**Changes:**
```python
@router.get("/{tag_id}", response_model=TagWithStats)
async def get_tag(...):
    # ... existing code ...

    # NEW: Fetch external links for this tag
    links_result = await db.execute(
        select(TagExternalLinks.url)
        .where(TagExternalLinks.tag_id == tag_id)
        .order_by(TagExternalLinks.date_added)
    )
    links = links_result.scalars().all()

    return TagWithStats(
        # ... existing fields ...
        links=list(links),
    )
```

**Performance:**
- Single additional query to fetch links (acceptable for detail view)
- Links ordered by `date_added` (most recent additions last)
- No impact on `list_tags` endpoint (links not included)

## Permission Model

**Required Permission:** `Permission.TAG_UPDATE`

- Adding links: `POST /tags/{tag_id}/links` requires TAG_UPDATE
- Removing links: `DELETE /tags/{tag_id}/links/{link_id}` requires TAG_UPDATE
- Viewing links: No permission required (public data in `GET /tags/{tag_id}`)

**Rationale:**
- Reuses existing permission (no new permission needed)
- Consistent with tag editing workflows
- Prevents abuse through existing elevated access controls

## Migration Strategy

1. Create Alembic migration to add `tag_external_links` table
2. Import new model in `alembic/env.py` for future autogenerate support
3. No data migration needed (new feature, no existing data)

## Testing Requirements

### Unit Tests
- URL validation (protocol check, length limits)
- Schema validation for create/response models

### API Tests (`tests/api/v1/test_tags.py`)
- Add link to tag (success case)
- Add duplicate link (409 error)
- Add link to non-existent tag (404 error)
- Add link without permission (403 error)
- Delete link from tag (success case)
- Delete non-existent link (404 error)
- Delete link from wrong tag (404 error)
- Get tag with links (verify links included)
- Get tag without links (verify empty array)
- List tags (verify links NOT included)

### Edge Cases
- Very long URLs (near 2000 char limit)
- URLs with special characters
- Multiple links on same tag
- Deleting tag cascades to links

## Future Enhancements

Potential features that can be added later without schema changes:

1. **Link Labels/Types** - Add `label` column to categorize links
2. **Display Order** - Add `display_order` column for explicit ordering
3. **Link Verification** - Add `verified` boolean for marking official links
4. **Bulk Operations** - `POST /tags/{tag_id}/links/bulk` endpoint
5. **Link Statistics** - Track click counts or last verified date
6. **GET /tags/{tag_id}/links** - Dedicated endpoint for listing links with full metadata
