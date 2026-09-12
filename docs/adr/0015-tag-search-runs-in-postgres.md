# Tag search runs in Postgres

Tag search (`GET /api/v1/search`, the tag list page, and every typeahead)
is answered by Postgres with `pg_trgm`, `unaccent`, and `fuzzystrmatch`
since September 2026 (PRs for `docs/plans/2026-Q3/2026-09-11-postgres-tag-search-design.md`).
Meilisearch was removed from the codebase and the compose stacks. There is
no search index to sync or rebuild: a tag is searchable the moment its row
commits.

## Considered Options

- **Keeping Meilisearch and adding derived fields** for the tag list
  filters was rejected. Fields derived from other rows (child count, alias
  count, character↔source links) needed a re-sync at every neighbouring
  write plus a scheduled reindex, and dev already carried orphaned documents
  from out-of-band deletions.
- **Splitting the tag list page across engines** (Meilisearch for text,
  Postgres for structural filters) was rejected: filters and pagination do
  not compose across two result sets.
- **Postgres for everything** won on a measured proof of concept: 34 of 38
  problem queries returned the same top hit as Meilisearch, the other four
  favoured Postgres, and latency was lower on 33 of 38.

## Consequences

- Every future search filter is a `WHERE` clause; nothing to index.
- `total` is exact. Pagination counts no longer drift.
- Relevance is the tier/typo/position/usage order in
  `app/services/tag_search.py`. Changing it means changing SQL, not index
  settings; the acceptance corpus in
  `tests/integration/test_tag_search_corpus.py` guards it.
- `public.fold_search_text` is `IMMUTABLE` and sits inside four indexes;
  changing the unaccent dictionary means rebuilding them.
- Removing Meilisearch from a host is an operator step: stop and remove the
  `meilisearch` container and the `meilisearch_data` volume, and drop
  `MEILI_MASTER_KEY` / `MEILISEARCH_API_KEY` from the host `.env` (leftover
  keys are inert; settings ignore unknown variables).
- A restored database is searchable immediately; only the IQDB index still
  needs a rebuild after a restore.
