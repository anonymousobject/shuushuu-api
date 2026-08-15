# Repost Data Cleanup Design

**Date:** 2026-02-22

## Problem

When an image is marked as a repost, the current FastAPI code only sets `replacement_id` and changes the status. The legacy PHP code migrated favorites, ratings, and tags from the repost to the original image. The current implementation leaves user engagement stranded on the duplicate.

## Design

### Service function: `migrate_repost_data`

Extract a `migrate_repost_data(repost_id, original_id, db)` function in `app/services/repost.py`, called from the `change_image_status` endpoint when status=REPOST.

All operations run in the same transaction as the status change.

### Migration steps (in order)

1. **Favorites**: Copy to original (`INSERT IGNORE`), update original's `favorites` count, delete from repost, set repost `favorites=0`.
2. **Ratings**: Copy to original (`INSERT IGNORE`), schedule rating recalculation on original via background job, delete from repost, reset repost rating fields (`num_ratings=0, rating=0, bayesian_rating=0`).
3. **Tags**: Copy tag links to original (`INSERT IGNORE`), delete from repost. DB triggers handle `usage_count` updates on the tags table automatically.
4. **Comments**: No action. Comments may have discussion value and the PHP code didn't touch them either.
5. **IQDB**: No action. Repost images remain publicly visible.

`INSERT IGNORE` handles duplicates naturally — if a user already favorited/rated/tagged the original, the duplicate row is silently skipped.

### Return value

`migrate_repost_data` returns a summary dict (`favorites_moved`, `ratings_moved`, `tags_moved`) logged in the existing `AdminActions` details JSON for audit purposes.

### Tests

- Favorites migrate to original, deleted from repost
- Ratings migrate, repost ratings cleared, original rating recalculated
- Tags migrate, deleted from repost
- Duplicates handled gracefully (user already favorited/rated/tagged original)
- Existing validations still work (self-repost blocked, missing replacement blocked)
