# Design: Tag Exclusion for Image Search

**Date:** 2026-02-19
**Status:** Approved

## Summary

Add an `exclude_tags` query parameter to the `list_images` endpoint, allowing users to filter out images that contain specific tags. This enables searches like "mizugi but not school mizugi."

## API

New query parameter on `GET /api/v1/images`:

```
exclude_tags: comma-separated tag IDs (e.g. "150,160")
```

Example:
```
GET /api/v1/images?tags=100,200&exclude_tags=150&tags_mode=all
```

- Can be used alone or combined with `tags`
- Works with any `tags_mode` (any/all) — exclusions always apply as AND NOT

## Processing Pipeline

1. Parse comma-separated string into integer IDs (same as `tags`)
2. Resolve aliases via `resolve_tag_alias()` — same as included tags
3. Collect resolved IDs into a single set (deduplicates if two aliases resolve to the same tag)
4. Apply as a single `NOT IN` subquery — no hierarchy expansion

## SQL

All excluded tag IDs (after alias resolution) are applied as one clause:

```sql
AND image_id NOT IN (
    SELECT image_id FROM tag_links WHERE tag_id IN (excluded_ids...)
)
```

This runs after any inclusion filters, regardless of `tags_mode`.

## Design Decisions

- **No hierarchy expansion for excludes.** Excludes target exact tags only. The use case is to include a parent tag (which pulls in children via hierarchy) and then surgically remove specific children.
- **Alias resolution: yes.** Excluding "neko mimi" (alias of "cat ears") should work the same as excluding "cat ears" directly.
- **Shared MAX_SEARCH_TAGS limit.** Total of included + excluded tags must not exceed `MAX_SEARCH_TAGS`. Returns 400 if exceeded.
- **Overlap between include and exclude.** If the same tag appears in both, exclusion wins (image won't appear). Not worth special-casing — this is a user error.

## Testing

- Exclude-only search (no `tags` param)
- Include + exclude combo
- Alias resolution on excluded tags
- Overlap between include and exclude
- `MAX_SEARCH_TAGS` enforcement across both params
- `tags_mode=any` and `tags_mode=all` with excludes
