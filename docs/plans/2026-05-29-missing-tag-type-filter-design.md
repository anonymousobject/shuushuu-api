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

Added to the `list_images` signature mirroring the existing `tags_mode` style:

```python
missing_tag_types: Annotated[
    str | None,
    Query(description="Comma-separated tag type IDs the image must be missing (1=Theme,2=Source,3=Artist,4=Character)"),
] = None,
missing_tag_types_mode: Annotated[
    str, Query(pattern="^(any|all)$", description="Match ANY or ALL missing types")
] = "any",
```

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

### Aliases are intentionally not resolved

Unlike `tags`/`exclude_tags`, this filter does **not** resolve tag aliases. The presence of a tag type on an image is determined by the `type` of the tag actually applied (`tag_links.tag_id → tags.type`). An alias tag carries its own `type` column, and the API enforces that an alias and its canonical share a type. So an image whose only artist tag is itself an *alias* tag correctly counts as "has an artist tag" and is excluded by `missing_tag_types=3`. Resolving aliases here would be wrong: the applied tag's own type is authoritative.

## Validation & errors

- Parse `missing_tag_types` like `exclude_tags`: split on comma, keep digit tokens, dedupe.
- Reject any value outside `{1, 2, 3, 4}` (type `0`/"All" is not a real per-image tag type) → `400 Bad Request` with a message listing the valid IDs.
- Invalid `missing_tag_types_mode` (not `any`/`all`) → handled by `Query(pattern="^(any|all)$")`, which mirrors the existing `tags_mode` param and yields a **`422 Unprocessable Entity`** automatically — no manual check or `400` branch.
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
                    f"Invalid tag type(s): {', '.join(map(str, invalid))}. "
                    "Valid types are 1=Theme, 2=Source, 3=Artist, 4=Character."
                ),
            )

        if missing_tag_types_mode == "all":
            # Missing ALL listed types: no tag of any listed type
            query = query.where(
                Images.image_id.notin_(  # type: ignore[union-attr]
                    select(TagLinks.image_id)
                    .join(Tags, TagLinks.tag_id == Tags.tag_id)  # type: ignore[arg-type]
                    .where(Tags.type.in_(missing_type_ids))  # type: ignore[attr-defined]
                )
            )
        else:
            # Missing ANY listed type
            def lacks_type(type_id: int):
                return Images.image_id.notin_(  # type: ignore[union-attr]
                    select(TagLinks.image_id)
                    .join(Tags, TagLinks.tag_id == Tags.tag_id)  # type: ignore[arg-type]
                    .where(Tags.type == type_id)  # type: ignore[arg-type]
                )

            query = query.where(or_(*(lacks_type(t) for t in missing_type_ids)))
```

`# type: ignore` annotations follow the existing tag-subquery blocks in this file (the `exclude_tags` block uses `[union-attr]` on `Images.image_id.notin_` and `[call-overload,attr-defined]` on the inner select); the exact codes will be reconciled against `mypy` during implementation.

Both `Tags` (`images.py:67`) and `or_` (`images.py:28`) are already imported — no new imports needed.

### Count and pagination

The filter is added to the shared `query` object before the count query (`select(func.count()).select_from(query.subquery())`) and the pagination subquery are derived from it, so the `total` and the returned page both reflect the filter. This matches placement of the existing `exclude_tags` clause.

### Note on handler size

`list_images` is already ~360 lines, well over the 50-line guideline, with all tag filtering inline. Extracting the tag-filter building into a helper is worthwhile but is a pre-existing concern best handled in its own dedicated refactor, not bundled into this feature. This change follows the file's established inline pattern.

### Performance

The `tags → tag_links` subquery is index-friendly: `WHERE tags.type = X` uses the `type_alias` index (leading column `type`), and the join is covered by the `tag_links` composite PK `(tag_id, image_id)`. The cost is the anti-join (`NOT IN`) against the images set — the same shape as the existing `exclude_tags` clause. In `all` mode this is a single anti-join; in `any` mode it is up to four OR'd anti-joins. This is within established patterns, but during implementation run `EXPLAIN` on one `any`-mode query to confirm the planner uses the indexes rather than a full anti-join scan.

## Tests

API tests (TDD) in the images search test module. Seed a small fixture set, all with a public `status` (the `sample_image_data` fixture uses `status=1`, so tests run unauthenticated — do not seed non-public statuses):

- An image tagged with all four types.
- An image missing only an artist tag.
- An image missing only a source tag.
- An image with no tags at all.
- An image whose only artist tag is an **alias** tag (link points at the alias row, not the canonical).

Cases:

1. `missing_tag_types=3` → returns images lacking an artist tag; excludes the fully-tagged image.
2. `missing_tag_types=2,3&missing_tag_types_mode=any` → images lacking a source OR an artist.
3. `missing_tag_types=2,3&missing_tag_types_mode=all` → only images lacking both.
4. Combined with a `tags=` include filter → both filters apply (AND).
5. Alias: the image whose only artist tag is an alias is **not** returned by `missing_tag_types=3` (alias resolution is intentionally skipped; the applied tag's `type` is authoritative).
6. The no-tags image appears for every `missing_tag_types` value in both modes.
7. Validation: `missing_tag_types=0` → 400; `missing_tag_types=99` → 400; invalid mode (e.g. `missing_tag_types_mode=foo`) → **422** (from the `Query` pattern).
8. Omitted param → unchanged behavior (no filtering).
