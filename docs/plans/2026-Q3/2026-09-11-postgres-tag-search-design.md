# Postgres tag search replaces Meilisearch

Date: 2026-09-11
Status: design approved in discussion; implementation plan follows
Related: ADR-0014 (Postgres is the only supported database); throwaway POC on
the uncommitted branch `poc/pg-trigram-search`; follow-up design for
structural filters on the tag list page (spans both repos, not this document)

## Motivation

The tag list page needs structural filters: minimum image count, creation
date range, has children, is child, is alias, has alias, and whether a
character or source tag has a link on the other side. Today
`GET /api/v1/search` answers every tag list and typeahead query from
Meilisearch, so a filter exists only if the indexed document carries a field
for it. Fields derived from other rows (child count, alias count, source
links) would need a re-sync at every write that touches a neighbouring tag,
plus a scheduled reindex to catch what the write paths miss. Dev already holds
358 orphaned Meilisearch documents from out-of-band deletions, and nothing
reindexes on a schedule.

Postgres holds the truth for all of those relations. With MariaDB retired
(ADR-0014), the `pg_trgm`, `unaccent`, and `fuzzystrmatch` extensions are
available, and a one-day proof of concept showed Postgres matching
Meilisearch's quality on the corpus's known problem queries:

| Measure (38 queries, 10-row page, dev data) | Meilisearch | Postgres |
| --- | --- | --- |
| Top-1 agrees with Meilisearch | — | 34 of 38 |
| Faster engine | 5 queries | 33 queries |
| Typical latency | 48 ms flat | 15–30 ms |
| Worst latency | 49 ms | 92 ms (single character) |

The four top-1 differences favour Postgres: it returns the literal tags for
`C++`, `100%`, `deep-blue`, and `Sa`, where Meilisearch's tokenizer discards
the punctuation.

Moving tag search to Postgres removes a service, 23 sync call sites, a reindex
script, a restore step, and a class of drift bugs. Every future filter becomes
a `WHERE` clause.

## Decision

1. `GET /api/v1/search` keeps its contract and answers from Postgres. All five
   frontend callers (the tags page and four typeaheads) move at once with no
   frontend change.
2. A second PR deletes Meilisearch from the codebase and the compose stacks.
3. The structural filters follow as a third PR pair with their own design.

## Matching model

### Tokenizer

`fold(s)` is `lower(unaccent(s))`. `words(s)` splits `fold(s)` on runs of
characters that are not letters, digits, or `_`, dropping empties. One
function tokenizes both the query and the titles.

| input | `words` |
| --- | --- |
| `EB十` | `eb十` |
| `Märchen-noir` | `marchen`, `noir` |
| `C++` | `c` |
| `yano_0o0` | `yano_0o0` |
| `Pixiv 21412050` | `pixiv`, `21412050` |

Unicode letters count, so CJK survives; verified under the database's
`en_US.utf8` collation. A token is *numeric* when it is all digits.

### Candidate set (recall)

For a query that contains a run of three or more alphanumeric characters, the
candidate set is the `UNION` of four branches. Each branch uses its own index.
An `OR` that mixes them defeats the planner's bitmap and scans the whole table
(measured 852 ms against 34 ms for the `UNION`).

- **A. Title contains every token.** `fold(title) ILIKE '%tok%'` for each
  token.
- **B. Trigram word similarity.** `fold(q) <% fold(title)` with
  `pg_trgm.word_similarity_threshold = 0.5`. Runs only when some token has
  five or more letters, Meilisearch's minimum word length for one typo. The
  threshold is pinned by `sakrua kinomto`, which scores exactly 0.500 against
  Kinomoto Sakura.
- **C. Description contains every token**, folded. Runs only when every token
  has two or more characters; a one-character token matches nearly every
  description.
- **D. An external URL contains the whole query**, folded. Same gate as C.

Over the candidates: the type filter, `alias_of IS NULL` when aliases are
excluded, and one literal-containment requirement per numeric token across
title, description, and URLs. Numeric tokens never match fuzzily, so
`21412051` cannot find the artist whose URL holds `21412050`.

A query shorter than three characters, or with no three-character
alphanumeric run (`sa`, `C++`, `C.C.`), takes a prefix-only path:
`fold(title) LIKE fold(q) || '%'` on a btree `text_pattern_ops` index and
nothing else.

### Ranking (precision)

With no explicit sort, results order by:

1. **Tier.**
   - 0: `fold(title) = fold(q)`.
   - 1: `fold(title)` starts with `fold(q)`.
   - 2: word match. Every token but the last equals a title word; the last
     token is a prefix of a title word. `sakura kino` gives Kinomoto Sakura,
     then Kinoshita Sakura.
   - 3: every token is a substring of `fold(title)`.
   - 4: everything else (fuzzy, description, URL).
2. **Typo count**, computed only for tier 4 through a lazy `CASE`: the sum
   over tokens of the minimum Levenshtein distance to any title word. For the
   last token, also measure against each title word truncated to the token's
   length and keep the smaller. `kinomt` gives Kinomoto Sakura.
3. **Position** of the first matched title word, ascending.
4. **Effective usage count**, descending. Alias rows use the parent's count,
   as the Meilisearch documents did.
5. `tag_id`, ascending.

An explicit sort (`usage_count`, `title`, `type`, `date_added`, `tag_id`)
replaces the relevance order, with the effective count standing in for
`usage_count` and `tag_id` as the tie-break. An empty query lists every tag by
effective count descending unless sorted.

Each request sets, for its transaction only,
`SET LOCAL pg_trgm.word_similarity_threshold = 0.5` and `SET LOCAL jit = off`.
JIT compilation was 96 of the scored query's 102 ms.

`total` is an exact `COUNT(*)` over the candidate set and filters.

### Measured shape

Prototype timings on dev with 235k tags: head queries 15–30 ms, `sakura`
(2,030 candidates) 40 ms, single characters 63–92 ms. Every multi-character
keystroke beat Meilisearch's flat 48 ms round trip.

## Schema

One Alembic migration on the Postgres chain, in this order:

1. `CREATE EXTENSION IF NOT EXISTS` for `pg_trgm`, `unaccent`, and
   `fuzzystrmatch`. All three are trusted, so the database owner creates them
   without superuser rights.
2. `CREATE FUNCTION public.fold_search_text(text) RETURNS text` declared
   `IMMUTABLE PARALLEL SAFE STRICT`, defined as
   `lower(public.unaccent('public.unaccent'::regdictionary, $1))`.
   Schema-qualified on both sides so the migration and the app inline the same
   function whatever `search_path` says. `IMMUTABLE` is a promise: changing the
   unaccent dictionary later means reindexing.
3. GIN trigram indexes `ix_tags_title_fold_trgm` on
   `tags (fold_search_text(title::text))`, `ix_tags_desc_fold_trgm` on
   `tags (fold_search_text("desc"))`, and `ix_tag_external_links_url_fold_trgm`
   on `tag_external_links (fold_search_text(url))`.
4. Btree `ix_tags_title_fold_prefix` on
   `tags (fold_search_text(title::text) text_pattern_ops)`.

Indexes build with `CREATE INDEX CONCURRENTLY` inside an autocommit block; the
POC builds took seconds. Description and URL are folded here even though the
POC left them unfolded; the POC only lacked the indexes.

The dev database carries hand-made `poc_*` indexes, a `poc_unaccent` function,
and the three extensions. Drop the indexes and the function by hand before the
migration runs there; the implementation plan lists them.

## API contract

Unchanged: `q`, `type`, `exclude_aliases`, `limit` (1–100), `offset`,
`sort_by`, `sort_order`, and every `SearchResponse` field. What changes:

- `total` is exact. Meilisearch's was an estimate, and stale on dev.
- The 503 "search service unavailable" path goes away. A database failure
  surfaces as it does on every other route.
- Hydration, alias parent title and count, and the exact artist-identity
  prepend keep their code; only the engine call changes, including the
  first-page check the identity layer makes on later pages.
- A created or edited tag is searchable in the same transaction. Frontend e2e
  helpers that poll for Meilisearch indexing become no-ops and can go later.

## Deletion (PR 2)

Inventory from `git grep -il meili`:

- **Code.** `app/services/search.py`; the 23 sync call sites in
  `app/api/v1/tags.py`, `app/api/v1/images.py`, `app/services/batch_tag.py`,
  `app/services/ml_suggestion_review.py`, and
  `scripts/repair_alias_chains.py`; the lifespan in `app/main.py`; the worker
  init in `app/tasks/worker.py`; `MEILISEARCH_URL` and `MEILISEARCH_API_KEY`
  in `app/config.py`; docstrings in `app/schemas/search.py`.
- **Ops.** `scripts/reindex_search.py`; `reindex_search` in
  `scripts/db_utils.py` and the restore step plus `--skip-reindex` in
  `scripts/restore_prod_db.py`; the service, volume, and `depends_on` entries
  in `docker-compose.yml` and `docker-compose.prod.yml`; `.env.example`.
- **Dependency.** `meilisearch-python-sdk` in `pyproject.toml` and the lock.
- **Tests.** `tests/unit/test_search_service.py`,
  `tests/unit/test_search_sync.py`,
  `tests/integration/test_search_integration.py`,
  `tests/unit/test_db_utils_reindex.py`, and the Meilisearch fixtures in
  `tests/api/v1/test_search.py`, `tests/api/v1/test_ml_tag_suggestions.py`,
  and `tests/test_worker.py`.
- **Docs.** `docs/ml-tag-suggestions.md`. ADR-0008 and ADR-0014 mention
  Meilisearch as history and stay as written. A new ADR records that tag
  search runs in Postgres.
- **Frontend.** Nothing required. The `fetchSearch` comment that says
  "Meilisearch-powered" goes with the next touch of that file.

After PR 2 deploys, stop and remove the `meilisearch` container and the
`meilisearch_data` volume on production.

## Testing

- **Integration**, on the real Postgres test database: seed the fixture tags
  the corpus needs, then assert top-1 for every query in the appendix corpus, including
  `sakura kino` and `kinomt`; assert `21412051` returns nothing while
  `21412050` finds the tag whose URL holds it; assert alias rows rank by the
  parent's count; assert the type and alias filters; assert exact `total`.
- **Unit.** The tokenizer table above; the gates (which branches run for
  `the f`, `100%`, `sa`, `EB十`); parameterisation of the SQL builder, so no
  user text is ever interpolated.
- **Route.** Rewrite `tests/api/v1/test_search.py` against the Postgres
  engine, keeping every identity-layer case.
- **Frontend.** No code change. Run `tags-list.spec.ts` and the typeahead specs
  against dev after deploy.

## Rollout

1. Merge PR 1. Run prod-migrate. Verify `/api/v1/search?q=kinomto` returns
   Kinomoto Sakura first, and watch latency.
2. Merge PR 2. Deploy. Remove the container and volume.
3. Rollback for step 1 is a revert of PR 1 while Meilisearch still runs. After
   step 2 there is no rollback short of reverting both PRs and reindexing.

## Trade-offs accepted

- No proximity rule. Within a tier, word adjacency does not rank, so the tail
  of `long hair` orders by popularity.
- Totals differ from Meilisearch's estimates, so pagination counts change.
- Single-character queries cost 60–90 ms, dominated by the exact count over
  about 20k rows.
- The POC code is not promoted. The implementation is rebuilt under TDD from
  this design, with the POC as a reference for the measured pitfalls: the
  `UNION`, the lazy `CASE`, `SET LOCAL jit = off`, a bind parameter followed by
  `::` (SQLAlchemy will not substitute it), and asyncpg's one-command-per-
  statement rule.

## Open question

Confirm before prod-migrate that the production database role owns the
database, which trusted extensions require for `CREATE EXTENSION`.

## Appendix: acceptance corpus

Thirty-seven text queries from the API and frontend search test suites plus the
POC probes; the 38th measured query was the empty list-all sorted by usage. The
expected top-1 is what the Postgres POC returned at its final revision, which
matches Meilisearch except for the four punctuation cases noted above. Ids and
counts are from the dev database on 2026-09-11; the integration test seeds
its own fixtures with these titles.

| query | expected top-1 (id, effective usage) |
| --- | --- |
| `sakura` | sakura (78, 21476) |
| `cat` | cat (6209, 18416) |
| `maid` | maid (1, 23093) |
| `neko` | neko (9, 18416) |
| `school` | school uniform (16, 154188) |
| `swimsuit` | swimsuit (4, 8829) |
| `long hair` | long hair (46, 729274) |
| `sakura kinomoto` | Kinomoto Sakura (82250, 4594) |
| `kinomoto sakura` | Kinomoto Sakura (82250, 4594) |
| `sa` | Sa (8526, 1) |
| `co` | co (31050, 4) |
| `long` | long (59146, 1) |
| `kinomto` | Kinomoto Sakura (82250, 4594) |
| `sakrua kinomto` | Kinomoto Sakura (82250, 4594) |
| `hatsune mikuu` | Hatsune Miku (74925, 35622) |
| `swimsiut` | swimsuit (4, 8829) |
| `thig` | thigh highs (54, 162274) |
| `C.C.` | C.C. (65499, 2788) |
| `C++` | C++ (65484, 5) |
| `100%` | 100% Perfect Girl (35057, 3) |
| `deep-blue` | Deep-Blue Series (44396, 50) |
| `yano_0o0` | Yano (yano_0o0) (243367, 1) |
| `The Forgotten` | The Forgotten Field (240094, 1) |
| `The F` | The Familiar of Zero (129550, 607) |
| `21412050` | TKennshou (246438, 1) |
| `21412051` | (no results) |
| `pixiv.net/users/21412050` | Pixiv 21412050 (246439, 1) |
| `tsunekichi` | Tsunekichi (177611, 14) |
| `pokemon` | Pokémon (290, 16529) |
| `Pokémon` | Pokémon (290, 16529) |
| `marchen` | Märchen von Friedhof (93114, 1047) |
| `EB十` | EB十 (163460, 152) |
| `magical girl` | magical girl (79, 25205) |
| `Louise Francoise` | Louise Françoise le Blanc de la Vallière (86777, 447) |
| `marchan` | Märchen von Friedhof (93114, 1047) |
| `kinomoto` | Kinomoto Sakura (82250, 4594) |
| `hatsune` | Hatsune (74911, 10) |
| `sakura kino` | Kinomoto Sakura (82250, 4594) |
| `kinomt` | Kinomoto Sakura (82250, 4594) |
