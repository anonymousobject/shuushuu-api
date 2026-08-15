# Tag Alias/Parent Validation Constraints

**Date:** 2026-02-25

## Problem

Alias tags can currently be set as parent tags for other tags. This creates confusing hierarchies where a non-canonical tag appears as a parent. The reverse is also possible — a tag with children can be made into an alias, orphaning its hierarchy.

## Rules

1. **Cannot use an alias tag as a parent.** When setting `inheritedfrom_id`, reject if the target tag has `alias_of` set. Error message includes the canonical tag as a suggestion.

2. **Cannot make a parent tag into an alias.** When setting `alias_of`, reject if the tag currently has children (other tags with `inheritedfrom_id` pointing to it). Error message lists the child tags that must be reassigned first.

3. Both rules apply to both `create_tag` and `update_tag`.

## Implementation

### Shared validation function

Extract existing parent/alias existence checks and new constraint checks into a shared function in `app/api/v1/tags.py`:

```python
async def validate_tag_relationships(
    db: AsyncSession,
    *,
    tag_id: int | None,           # None for create, set for update
    inheritedfrom_id: int | None,  # proposed parent
    alias_of: int | None,          # proposed alias target
) -> None:
```

This function:
- Validates parent tag exists and is not an alias (rule 1)
- Validates alias tag exists (existing check)
- Validates tag has no children when being made an alias (rule 2, update only)

### Error messages

- Rule 1: `"Cannot use alias tag as parent. Tag '{title}' (id: {id}) is an alias of '{canonical_title}' (id: {canonical_id}). Use the canonical tag as parent instead."`
- Rule 2: `"Cannot make a tag with children into an alias. {n} tag(s) inherit from this tag: {child_list}. Reassign or remove child tags first."`

### Data audit

A one-off query script to find existing violations in prod data. No auto-fix — report only for manual review.

## Tests

- Create tag with alias parent → 400
- Update tag to set alias parent → 400
- Create alias tag and try to use as parent → 400
- Make a parent tag into an alias → 400
- Verify error messages include canonical tag suggestion / child list
- Happy paths: canonical tag as parent works, tag without children can become alias
