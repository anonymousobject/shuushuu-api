# Schema-sync: signed/unsigned drift in report_id/review_id family

**Date:** 2026-06-10
**Status:** IMPLEMENTED (2026-06-10) — family unsigned in models via `UnsignedInt`
(`app/models/types.py`), `test_column_types_match` added to
`tests/integration/test_schema_sync.py`. The broader full-schema type audit
(see "Audit findings" below) remains open.

## The drift

Models declare the `report_id`/`review_id` family as signed `int`, but the DB has them as `int(10) unsigned`:

- PKs: `image_reports.report_id` (`app/models/image_report.py:74`), `image_reviews.review_id` (`app/models/image_review.py:91`)
- FKs to them:
  - `image_status_history.report_id` / `review_id` (`app/models/image_status_history.py:94-95`)
  - `image_reviews.source_report_id` (`app/models/image_review.py:103`)
  - `image_report_tag_suggestions.report_id` (`app/models/image_report_tag_suggestion.py:47`)
  - `admin_actions.report_id` / `review_id` (`app/models/admin_action.py:85-86`)
  - `review_votes.review_id` (`app/models/review_vote.py:93`)

Harmless at runtime (small positive auto-increment IDs, far below the signed 32-bit ceiling) and the migrations build the DB correctly — but it's real model↔DB drift.

## How it was exposed

The timeline FK work surfaced it: making one FK column unsigned broke `create_all` (errno 150 — signed PK ← unsigned FK type mismatch). Those 2 columns were reverted to signed for now so the family stays internally consistent.

## Permanent fix

1. **Unsign the whole family at once** — switch every column listed above to `mysql.INTEGER(unsigned=True)` in the models to match the DB. Partial changes break `create_all` (errno 150); it's all-or-nothing per FK family.
2. **Extend `tests/integration/test_schema_sync.py`** — it currently only diffs FK CASCADE behavior (`get_foreign_keys`), NOT column types, which is why this drift was never caught. Add a `get_columns` diff covering type and signedness.

## WARNING

The `get_columns` diff will likely surface more pre-existing signed/unsigned (and possibly length/charset) mismatches across the legacy schema. Triage those as their own audit — don't block the report/review fix on them.

## Audit findings (2026-06-10, from the whole-table diff of the 6 family tables)

Confirmed: the drift extends well beyond the report/review family. The column
test is therefore scoped to the 9 family columns; everything below is the open
audit's work-list. Note fixing any unsigned FK column requires unsigning its
parent PK first (errno 150), so `images.image_id` / `users.user_id` /
`tags.tag_id` cascade schema-wide.

**Signed in models, `INT UNSIGNED` in DB (beyond the fixed family):**
- PKs: `image_status_history.id`, `review_votes.vote_id`, `admin_actions.action_id`, `image_report_tag_suggestions.suggestion_id`
- FKs to parent tables: `image_id`, `user_id`, `tag_id`, `reviewed_by`, `initiated_by`, `closed_by` in all 6 tables — implies `images.image_id`, `users.user_id`, `tags.tag_id` PKs are unsigned in the DB too

**`INTEGER` in models, `TINYINT` in DB:**
- `image_reports.category` (unsigned), `image_reports.status`
- `review_votes.vote`
- `image_report_tag_suggestions.suggestion_type` (unsigned)

**`VARCHAR(255)` in models, `MEDIUMTEXT` in DB:**
- `image_reports.reason_text`, `image_reports.admin_notes`
- `review_votes.comment`

Tables outside the family (users, images, tags, posts, …) have not been
diffed yet — extend `test_column_types_match`'s scope (or run a one-off
whole-schema diff) as the audit proceeds.

## Related

- Postgres feasibility analysis (`docs/plans/2026-06-10-postgres-migration-feasibility.md`) notes Postgres has no unsigned ints; this family is among the columns that would need a signed-range check before any such migration.
