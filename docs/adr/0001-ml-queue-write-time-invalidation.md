# ML suggestion queue consistency via write-time invalidation, not read-time filtering

A pending ML suggestion whose tag is already applied to its image is stale and must not surface for review. We keep the data honest at write time — every path that applies a tag outside the ML review flow (manual add, batch tagging, report resolution, repost tag migration) resolves the matching pending suggestion via `approve_pending_suggestions_for_links()` — instead of filtering stale rows out of queries.

## Considered Options

Read-time filtering (`NOT EXISTS` anti-join against `tag_links`) was measured on production-sized data (2.06M pending suggestions, 14.7M tag links): **43ms** on the per-tag grid, but **7–11s vs 0.9s** on the worklist aggregate, which probes every pending row. The worklist therefore must never gain that anti-join; its counts are correct because writes keep them correct.

## Consequences

- The per-tag grid keeps its cheap anti-join as a safety net, so a future tag-write path that forgets the helper degrades worklist counts but never shows a mod an already-applied tag.
- Any new code path that creates `tag_links` must call `approve_pending_suggestions_for_links()` in the same transaction.
- Matching is by exact `tag_id` everywhere (generation, invalidation, grid filter); a suggestion whose tag later becomes an alias of the applied tag is deliberately not matched.
