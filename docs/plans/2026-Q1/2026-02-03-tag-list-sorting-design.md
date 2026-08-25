# Tag List: Add usage_count and Sorting Support

## Summary

Add `usage_count` to the tag list response and support `sort_by`/`sort_order` query parameters for the tag listing endpoint.

## Changes

### 1. Schema: Expose `usage_count` in `TagResponse`

Add `usage_count: int = 0` to `TagResponse` in `app/schemas/tag.py`. The field already exists on the `Tags` model (maintained by a DB trigger on tag_links changes), so no query changes are needed -- `model_validate` picks it up automatically.

### 2. Dependencies: Add `TagSortParams`

Add to `app/api/dependencies.py`:

```python
class TagSortParams(BaseModel):
    sort_by: Literal["usage_count", "title", "date_added", "tag_id", "type"] = Field(
        default="usage_count", description="Sort field"
    )
    sort_order: SortOrder = Field(default="DESC", description="Sort order")
```

`date_added` maps to `tag_id` internally (PK is auto-increment chronological and indexed; `date_added` has no index).

### 3. Endpoint: Use `TagSortParams` in `list_tags`

In `app/api/v1/tags.py`, add `sorting: Annotated[TagSortParams, Depends()]` parameter.

**With search:** Ignore `sort_by`/`sort_order`. Keep existing relevance-based ranking (exact match, fulltext relevance, usage_count, alphabetical).

**Without search:** Replace hardcoded `usage_count DESC, date_added DESC` with:

```python
sort_column_map = {
    "usage_count": Tags.usage_count,
    "title": Tags.title,
    "date_added": Tags.tag_id,
    "tag_id": Tags.tag_id,
    "type": Tags.type,
}
sort_column = sort_column_map[sorting.sort_by]
sort_func = desc if sorting.sort_order == "DESC" else asc
query = query.order_by(sort_func(sort_column))
```

### 4. Migration: Add indexes for sort columns

The tags table has 229K rows. Without B-tree indexes on the sort columns, `ORDER BY + LIMIT + OFFSET` causes a full filesort on every paginated request.

Add an Alembic migration with two indexes:

- `idx_tags_usage_count` on `usage_count` (default sort field, currently unindexed)
- `idx_tags_title` on `title` (B-tree for sorting; the existing `ft_tags_title` FULLTEXT index is only for search)

`tag_id` already has the PK index. `type` can use the leading column of the existing `type_alias` composite index.

## Files

| File | Change |
|------|--------|
| `app/schemas/tag.py` | Add `usage_count` field to `TagResponse` |
| `app/api/dependencies.py` | Add `TagSortParams` class |
| `app/api/v1/tags.py` | Add `sorting` dependency, use in no-search branch |
| `app/models/tag.py` | Add index definitions for model consistency |
| `alembic/versions/xxx_add_tag_sort_indexes.py` | Migration to add `idx_tags_usage_count` and `idx_tags_title` |
| `tests/` | Tests for sorting params and usage_count in response |
