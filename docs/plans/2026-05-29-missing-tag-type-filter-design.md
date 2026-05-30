# Filter Images by Missing Tag Type(s)

**Date:** 2026-05-29

## Problem

The image search endpoint (`GET /api/v1/images`) can filter by tags present on an image (`tags`, `exclude_tags`), but it cannot filter by the *absence* of a tag type. There is no way to ask "which images have no artist tag yet?" or "which images lack a source?"

This is useful both as a general search filter and as the basis for a moderation/cleanup queue surfacing under-tagged images.

Tag types are defined in `app/config.py`:

```python
class TagType:
    ALL = 0
    THEME = 1
    SOURCE = 2
    ARTIST = 3
    CHARACTER = 4
```

Each tag carries a `type` column; `tag_links` joins images to tags. There is a `type_alias` index on `tags(type, alias_of)`, so filtering tags by `type` is indexed.

## API surface

Two new optional query params on `list_images` (`app/api/v1/images.py`):

| Param | Type | Description |
|---|---|---|
| `missing_tag_types` | `str \| None` | Comma-separated tag type IDs the image must be *missing*. Valid: `1`=Theme, `2`=Source, `3`=Artist, `4`=Character. |
| `missing_tag_types_mode` | `str` | `"any"` (default) or `"all"`. Mirrors the existing `tags_mode` param. |

Type IDs (not names) are used, consistent with the existing `tags`/`exclude_tags` params. The frontend constructs the API call.

## Semantics

"Image lacks a tag of type X" is expressed as:

```sql
Images.image_id NOT IN (
    SELECT tl.image_id
    FROM tag_links tl
    JOIN tags t ON tl.tag_id = t.tag_id
    WHERE t.type = X
)
```

- **`any` (default):** image is missing *at least one* of the specified types → OR of the per-type `NOT IN` clauses. Example: `missing_tag_types=2,3` returns images lacking a source **or** an artist. This is the "needs work" queue behavior.

- **`all`:** image is missing *all* the specified types → a single `NOT IN` against `t.type IN (...)`. If an image has zero tags whose type is in the set, it is missing all of them. This is simpler and cheaper than AND-ing separate per-type clauses.

These clauses compose with `AND` against all existing filters (`tags`, `exclude_tags`, date filters, etc.), the same as every other where-clause in the handler.

## Validation & errors

- Parse `missing_tag_types` like `exclude_tags`: split on comma, keep digit tokens, dedupe.
- Reject any value outside `{1, 2, 3, 4}` (type `0`/"All" is not a real per-image tag type) → `400 Bad Request` with a message listing the valid IDs.
- Invalid `missing_tag_types_mode` (not `any`/`all`) → `400`. Match however `tags_mode` is validated (`Query(pattern=...)` vs. manual check) — confirm during implementation.
- Empty/whitespace `missing_tag_types` → treated as absent (no filter applied), matching how `tags`/`exclude_tags` behave.

## Implementation

Approach: inline block in `list_images`, immediately after the existing `exclude_tags` block (~line 448), matching that block's style and `# type: ignore` annotations.

```python
# Missing tag-type filtering (images lacking a tag of the given type[s])
if missing_tag_types:
    missing_type_ids = sorted({
        int(t.strip())
        for t in missing_tag_types.split(",")
        if t.strip().isdigit()
    })
    if missing_type_ids:
        invalid = [t for t in missing_type_ids if t not in {1, 2, 3, 4}]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid tag type(s): {invalid}. "
                    "Valid types are 1=Theme, 2=Source, 3=Artist, 4=Character."
                ),
            )

        def lacks_type(type_id: int):
            return Images.image_id.notin_(
                select(TagLinks.image_id)
                .join(Tags, TagLinks.tag_id == Tags.tag_id)
                .where(Tags.type == type_id)
            )

        if missing_tag_types_mode == "all":
            # Missing ALL listed types: no tag of any listed type
            query = query.where(
                Images.image_id.notin_(
                    select(TagLinks.image_id)
                    .join(Tags, TagLinks.tag_id == Tags.tag_id)
                    .where(Tags.type.in_(missing_type_ids))
                )
            )
        else:
            # Missing ANY listed type
            query = query.where(or_(*(lacks_type(t) for t in missing_type_ids)))
```

(Final form will match exact imports and `# type: ignore` comments already used in the file.)

Requires `Tags` and `or_` to be imported in `app/api/v1/images.py` — confirm and add if missing.

### Note on handler size

`list_images` is already ~360 lines, well over the 50-line guideline, with all tag filtering inline. Extracting the tag-filter building into a helper is worthwhile but is a pre-existing concern best handled in its own dedicated refactor, not bundled into this feature. This change follows the file's established inline pattern.

## Tests

API tests (TDD) in the images search test module. Seed a small fixture set:

- An image tagged with all four types.
- An image missing only an artist tag.
- An image missing only a source tag.
- An image with no tags at all.

Cases:

1. `missing_tag_types=3` → returns images lacking an artist tag; excludes the fully-tagged image.
2. `missing_tag_types=2,3&missing_tag_types_mode=any` → images lacking a source OR an artist.
3. `missing_tag_types=2,3&missing_tag_types_mode=all` → only images lacking both.
4. Combined with a `tags=` include filter → both filters apply (AND).
5. Validation: `missing_tag_types=0` → 400; `missing_tag_types=99` → 400; invalid mode → 400.
6. Omitted param → unchanged behavior (no filtering).
