# ML suggestion rows follow the image-status lifecycle

Suggestion rows exist only on suggestion-eligible images (`ACTIVE`, `SPOILER` — see CONTEXT.md). On any eligible→ineligible transition in `change_image_status()`, the image's pending rows are deleted (marking as REPOST deletes all its rows, matching how repost-marking already wipes favorites/ratings/tags); on any ineligible→eligible transition, pending rows are re-seeded from the raw-prediction store via `remap_image_from_store()` — no inference or image file needed. Both hooks run atomically with the status change. Suggestion creation (pipeline and remap) skips ineligible images so nothing resurrects between transitions.

## Considered Options

- **Auto-rejecting instead of deleting** would be remap-proof without creation guards, but "rejected" would be a lie (the tag wasn't judged wrong — the image left review scope) and would fill the per-image suggestion history with junk attributed to whichever mod changed the status.
- **Hiding at query time** hits the measured worklist-aggregate wall recorded in ADR-0001.

## Consequences

- Deletion looks like data loss but isn't: raw predictions are retained, so re-seed on restore is free and complete. An un-reposted image (tagless today by existing design) immediately regains a full pending set.
- `ml_remap` and the generation pipeline must check eligibility; widening eligibility later (e.g. to `REVIEW`) is a one-set change.
