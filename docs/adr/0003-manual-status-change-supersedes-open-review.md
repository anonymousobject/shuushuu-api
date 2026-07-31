# A manual status change supersedes the image's open review

A review is a vote on what should happen to an image. When a moderator answers that question by hand, `change_image_status()` closes any open review on the image with outcome `SUPERSEDED` and writes a `REVIEW_CLOSE` audit row. Votes already cast stay on record. The hook is skipped when the caller passes `review_id` — then the status change IS a review action (`REVIEW_START` setting the image to `REVIEW`, `REVIEW_CLOSE` applying an outcome it just recorded), and firing would close the review it just opened or overwrite the verdict it just reached.

## Considered Options

- **Leaving the review open** is what shipped, and it costs more than a stale queue entry: the review keeps collecting votes on a settled question, and `check_review_deadlines()` later closes it and re-applies its own outcome. With no quorum after one extension that outcome is `KEEP`, which calls `change_image_status(new_status=ACTIVE)` — silently reactivating an image a moderator deactivated, with no reason recorded (the un-hide reason guard exempts `REVIEW_CLOSE`).
- **Leaving it open but making the deadline close a no-op when the status moved** keeps the vote alive for its full term and avoids the reactivation, but the image sits in the review queue looking unresolved and moderators keep spending votes on it.
- **Filtering the queue by image status** hides the symptom and leaves the reactivation in place.
- **Recording the outcome as KEEP or REMOVE** to match the new status would put a verdict in the record that the voters never reached.

## Consequences

- `SUPERSEDED` is set only by this hook. `/reviews/{id}/close` still accepts `KEEP`/`REMOVE` only (`ge=1, le=2`), so a moderator cannot pick it by hand.
- Reviews closed this way have `closed_by` set to the acting moderator (NULL for system actors) and `outcome_label` "Superseded"; the frontend renders them as a neutral badge, not as a keep.
- The guard is `review_id is None`, not an `action_type` check: the session is `autoflush=False`, so `_close_review()`'s own unflushed status write is invisible to a `SELECT` here and ordering alone would not protect it.
- Rows that predate this hook keep their open status; closing them is a separate data cleanup.
