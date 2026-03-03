# Search Service Design: Meilisearch Integration

## Problem

Tag searching has grown complex. The `list_tags` endpoint contains ~220 lines of query
building, including ~100 lines of MySQL fulltext preprocessing — tokenization, stopword
filtering, special character sanitization, and multi-tier fallback logic. This complexity
lives in the route handler, making it hard to extend and test.

We need:
- **Better code organization**: search logic extracted from route handlers
- **Better relevance and ranking**: MySQL fulltext's scoring is basic, and prefix matching
  requires manual two-tier fallback logic
- **Cross-entity search**: a unified search across tags, images, users, and comments

## Approach

Add Meilisearch as a dedicated search backend alongside MySQL. MySQL remains the source of
truth for all data. Meilisearch serves as a read-only search index optimized for text
search, prefix matching, and relevance ranking.

Existing search endpoints stay untouched. A new `/api/v1/search` endpoint uses Meilisearch,
allowing side-by-side comparison before any migration.

## Architecture

```
Existing endpoints (unchanged)
  GET /api/v1/tags?search=...     → MySQL fulltext (as today)
  GET /api/v1/images?tags=...     → MySQL relational joins (as today)

New endpoint
  GET /api/v1/search?q=...        → SearchService → Meilisearch

Write paths (tag create/update/delete)
  Route handler → MySQL commit → SearchService.index_tag() → Meilisearch
```

### Components

```
app/services/search.py
├── SearchService
│   ├── __init__(client)                # Meilisearch client injection
│   ├── search_tags(query, filters, limit) -> list[TagSearchResult]
│   ├── search_images(query, filters, limit) -> list[ImageSearchResult]
│   ├── index_tag(tag)                  # Upsert single document
│   ├── index_tags(tags)                # Bulk upsert
│   ├── delete_tag(tag_id)              # Remove from index
│   └── reindex_all_tags(db)            # Full reindex from MySQL

app/schemas/search.py
├── TagSearchResult
├── ImageSearchResult
└── SearchResponse

app/api/v1/search.py
└── GET /api/v1/search                  # New endpoint
```

Route handlers never touch the Meilisearch client directly.
The `SearchService` is initialized during the FastAPI lifespan alongside DB and Redis,
and made available via `Depends(get_search_service)`.

## Index Design

### Tags Index (primary, day one)

| Field         | Type       | Role                  |
|---------------|------------|-----------------------|
| `tag_id`      | int        | Primary key           |
| `title`       | string     | Searchable (primary)  |
| `desc`        | string     | Searchable (secondary)|
| `type`        | int        | Filterable            |
| `usage_count` | int        | Custom ranking signal |
| `alias_of`    | int\|null  | Filterable            |

**Ranking rules** (priority order):
1. `words` — how many search terms matched
2. `typo` — fewer typos ranked higher
3. `proximity` — words closer together ranked higher
4. `attribute` — matches in `title` ranked above `desc`
5. `exactness` — exact word matches over prefix matches
6. `usage_count:desc` — popular tags break ties

**Synonyms**: configured for common romanization variants (e.g., "Touhou" ↔ "Toho").

### Images Index (second priority)

Metadata only: `image_id`, `rating`, `width`, `height`, `source`. Image search by tags
stays in MySQL — that's a relational join, not a text search problem.

### Users and Comments (later)

Small indexes. Users searchable by `username`, comments by `body`.

## Data Sync

### Real-time sync

Tag create/update/delete paths push changes to Meilisearch after MySQL commit:

```python
await db.commit()
await search_service.index_tag(tag)   # upsert to Meilisearch
```

Fire-and-forget: if Meilisearch is temporarily down, the MySQL write still succeeds.
Search results will be stale until the next full reindex. Acceptable for a tag database
that changes infrequently.

Tag link operations (tagging/untagging images) update `usage_count` via the existing
database trigger. These paths also call `search_service.index_tag()` to keep the ranking
signal current.

### Full reindex

A management script (`scripts/reindex_search.py`) reads all tags from MySQL and bulk-pushes
to Meilisearch. Used for:
- Initial setup
- Recovery after Meilisearch data loss
- Adding new fields to the index
- Correcting sync drift

Idempotent and safe to run anytime. 500K tags reindex in seconds to low minutes.

## New Endpoint

```
GET /api/v1/search?q=sakura&entity=tags&limit=20
GET /api/v1/search?q=sakura                          # defaults to tags
GET /api/v1/search?q=sakura&entity=tags,images        # cross-entity (later)
```

Returns results in Meilisearch's relevance order using existing `TagPublic` / `ImagePublic`
response schemas. The search endpoint fetches full records from MySQL by ID after
Meilisearch returns ranked results.

## Changes to Existing Code

### Untouched
- `GET /api/v1/tags` — keeps MySQL fulltext search
- `GET /api/v1/images` — keeps relational tag filtering
- Tag alias resolution and hierarchy expansion — relational operations, stay in MySQL
- Authentication, pagination format, all existing API contracts

### Modified (sync only)
- Tag create/update/delete endpoints — add one `search_service.index_tag()` call after
  MySQL commit
- Tag link create/delete endpoints — add one `search_service.index_tag()` call to update
  `usage_count` in the index

### New
- `app/services/search.py` — SearchService class
- `app/schemas/search.py` — search-specific schemas
- `app/api/v1/search.py` — new search endpoint
- `scripts/reindex_search.py` — bulk reindex script

## Infrastructure

### Docker Compose

```yaml
meilisearch:
  image: getmeili/meilisearch:v1
  ports:
    - "7700:7700"
  volumes:
    - meilisearch_data:/meili_data
  environment:
    - MEILI_MASTER_KEY=${MEILI_MASTER_KEY}
    - MEILI_ENV=development
```

Production uses `MEILI_ENV=production` and a strong master key.

### App Configuration

New settings in `app/config.py`:

```python
MEILISEARCH_URL: str = "http://meilisearch:7700"
MEILISEARCH_API_KEY: str | None = None
```

### Dependencies

One new package: `meilisearch-python-sdk` (async-native client).

### Index Setup

During FastAPI lifespan, the search service configures index settings — ranking rules,
filterable attributes, searchable attributes, sortable attributes. Meilisearch handles
this idempotently, so it runs safely on every startup.

## Testing

### Unit tests (`tests/unit/`)
- Document shape sent to Meilisearch on index/delete
- Filter expression construction
- Result mapping from Meilisearch hits to `TagSearchResult`
- Edge cases: empty query, no results, client errors

### Integration tests (`tests/integration/`)
Against a real Meilisearch instance (added to test Docker Compose):
- Index known tags, query, verify relevance ordering
- Prefix matching ("saku" → "Sakura Kinomoto")
- Type filtering combined with text search
- Alias exclusion
- `usage_count` ranking influence

### API tests (`tests/api/v1/`)
- `GET /api/v1/search` end-to-end
- Authenticated and unauthenticated access
- Pagination, query parameter validation
- Response schema matches existing public schemas

### Sync tests
- Create tag via API → search finds it
- Update tag title → search reflects new title
- Delete tag → search no longer returns it

### Out of scope
Meilisearch's internal relevance algorithm. We test that our integration sends correct
data and interprets results correctly.

## Migration Path

Once side-by-side comparison confirms Meilisearch results are satisfactory:
- Point the frontend's typeahead at `/api/v1/search` instead of `/api/v1/tags?search=`
- Or swap `list_tags`'s search path to use the service internally
- Or both

This is a future decision backed by comparison data. No premature commitment.
