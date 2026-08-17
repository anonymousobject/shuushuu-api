# Out-of-band suggestion resolution is scoped to the links the caller created

ADR-0001 requires every path that applies a tag outside the ML review flow to resolve the matching pending suggestion via `approve_pending_suggestions_for_links()`. This ADR fixes the scope of that call: a caller passes only the `(image_id, tag_id)` pairs it just created, never the image's whole tag set. Repost migration was the one caller passing everything on the original; it now diffs the two images' tag ids up front and passes the difference, which is also what `INSERT IGNORE` adds and therefore the migration's `tags_moved` count.

## Considered Options

- **Passing the original's whole tag set** is what shipped. `EXPLAIN` on production-sized data (2.4M suggestion rows) shows the resulting UPDATE range-scanning `unique_ml_suggestion_image_tag`, so it takes a next-key lock per tag on the original *plus gap locks for tags with no suggestion row* — the gaps the ML pipeline inserts into for neighbouring images. The lock set grew with the original's tag count, and the resulting deadlock (#335) 500'd the moderator mid-migration.
- **Keeping the wide list and relying on the retry** (ADR-0004) removes the 500 but leaves the collision rate where it is, so every busy repost pays retry latency for bookkeeping nobody waits on.
- **Forcing a different index** addresses the lock shape without addressing the semantic over-reach: resolving suggestions for tags this operation did not touch is not the operation's business either way.
- **Moving resolution to the job queue** shortens the request's transaction the most, but splits an atomic migration across two units of work — the tags land on the original while its suggestion rows briefly disagree.

## Consequences

- A pending suggestion for a tag the original **already carried** survives the repost. This state should not be reachable: generation excludes already-applied tags, and every tag-add path resolves its own links per ADR-0001. If one is ever observed, the bug is in whichever path applied that tag, and widening this call would hide it rather than fix it.
- The moved-tag set is derived once and used for both the count and the resolution scope, so the two cannot drift apart.
- Tag ids are passed sorted, giving concurrent repost migrations a consistent lock acquisition order.
- The rule is now uniform across all four resolver callers (manual add, batch tagging, report resolution, repost migration), so "pass your own new links" is the pattern to copy for any future path that creates `tag_links`.
