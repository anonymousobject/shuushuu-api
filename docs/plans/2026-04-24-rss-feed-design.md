# RSS / Atom feed design

**Date:** 2026-04-24
**Status:** Design approved, pending implementation
**Supersedes:** legacy PHP `/index.rss` (RSS 1.0 / RDF)

## Background

The legacy PHP site shipped a single feed at `/index.rss` — latest 100 active images, RSS 1.0 (RDF), no parameters, no auth, 5-minute memcache TTL for anonymous requests. Items linked directly to the raw image file, not to a detail page. See `shuu-php/index.rss`.

Modern booru convention is Atom 1.0 (verified: Danbooru serves `/posts.atom`, e621 serves `/posts.atom`, neither offers RSS 2.0). This design replaces the legacy feed with a spec-idiomatic Atom implementation on the FastAPI backend and adds per-tag feeds.

## Goals

- Serve an Atom 1.0 feed of the latest active images (parity with PHP).
- Serve an Atom 1.0 feed of the latest active images for a given tag ID.
- Present the same view of an image that the public JSON API does (reuse response schemas).
- Support conditional requests (`ETag` / `Last-Modified`) so well-behaved readers cost only a sentinel query on polls.

## Non-goals

- No per-user / authenticated feeds (e.g. `/me/favorites.atom`). If needed later, the token-in-URL pattern can be bolted on.
- No Redis-cached response bodies. HTTP caching is sufficient; if traffic forces it, escalating to Redis is a small future change.
- No pagination (`?page=`, `?limit=`, RFC 5005). Fixed window is the convention and is sufficient given expected reader polling behavior.
- No feed autodiscovery `<link>` tags in frontend HTML. Frontend's concern, not backend's.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/images.atom` | Latest 50 active images, newest first. |
| `GET` | `/api/v1/tags/{id}/images.atom` | Latest 50 active images tagged with `{id}`. `{id}` is `tags.tag_id`. 404 if the tag does not exist. |

- `{id}` is used instead of `{title}` to make URLs stable across tag renames.
- The per-tag endpoint resolves aliases (`tags.alias_of`) via `resolve_tag_alias()` and expands the tag hierarchy via `get_tag_hierarchy()`, matching `GET /api/v1/tags/{id}/images` (see `app/api/v1/tags.py:167,201,695,703`). A reader subscribing to a tag feed sees the same image set the frontend tag page renders.
- Content-Type: `application/atom+xml; charset=utf-8`.
- Public (no auth).
- Fixed window: 50 entries. No query parameters.

## Feed structure

### Feed-level elements

| Element | Value |
|---|---|
| `<id>` | `tag:e-shuushuu.net,2005:feed:images` (global) or `tag:e-shuushuu.net,2005:feed:tags:{id}` (per-tag). Tag URI (RFC 4151). |
| `<title>` | `"Shuushuu — latest images"` (global) or `"Shuushuu — tag: {title}"` (per-tag). |
| `<link rel="self">` | Absolute URL of the feed. |
| `<link rel="alternate">` | `https://e-shuushuu.net/` (global) or `https://e-shuushuu.net/tags/{id}` (per-tag). |
| `<updated>` | `date_added` of the newest entry, or current UTC time if feed is empty. |
| `<author><name>` | `Shuushuu` (feed-level fallback; entries always emit their own). |

### Entry-level elements

| Element | Source |
|---|---|
| `<id>` | `tag:e-shuushuu.net,2005:image:{image_id}`. Tag URI, stable, unique, independent of filename. |
| `<title>` | See "Title composition" below. |
| `<link rel="alternate">` | `https://e-shuushuu.net/images/{image_id}` (frontend detail page). |
| `<link rel="enclosure">` | Full image URL, with `length="{image.filesize}"` in bytes and `type` set via a small mapping (`jpg`/`jpeg` → `image/jpeg`, `png` → `image/png`, `gif` → `image/gif`, `webp` → `image/webp`). Naive `f"image/{ext}"` is wrong for `jpg`. |
| `<updated>` | `image.date_added`. |
| `<published>` | `image.date_added`. Same as `<updated>` until the model tracks edits separately. |
| `<author><name>` | `image.user.username`. `"[deleted user]"` if `user` is NULL (soft-deleted uploader). |
| `<category>` | One per tag linked to the image, via `tag_links`. `term="{tag.title}"`, `scheme="https://e-shuushuu.net/tag-type/{type_name}"` where `type_name` is the value from `TagSummary.type_name` (title-cased: `Theme`, `Source`, `Artist`, `Character`; `All` exists as a pseudo-type in `TagType` but is a filter value only, not present on actual tag rows). Reusing the existing schema value keeps feed and JSON-API representations aligned. No truncation. |
| `<content type="html">` | `image.caption` if present, else empty string. HTML-escaped. |

### Title composition

Format: `"{characters} ({sources}) drawn by {artists}"` using a single representative tag per category, chosen by `ORDER BY usage_count DESC`. Empty sections are skipped:

- No character tags: `"({sources}) drawn by {artists}"`.
- No source tags: `"{characters} drawn by {artists}"`.
- No artist tags: `"{characters} ({sources})"`.
- No tags of any used category: `"Image #{image_id}"` (final fallback).

Rationale: titles are the prominent field in feed-reader list views; keeping them short and human-readable is worth more than enumerating every tag. Full tag listing is available on each entry via `<category>` elements — the spec-canonical location.

## Caching

### Response headers

- `Cache-Control: public, max-age=300` (5 minutes; matches PHP memcache TTL).
- `Last-Modified: <HTTP-date>` — newest `date_added` in the feed window, floored to whole seconds (RFC 7232 §2.2 HTTP-date has 1-second resolution) and formatted as e.g. `Wed, 24 Apr 2026 12:34:56 GMT`.
- `ETag: W/"<hash>"` — weak ETag, content-derived (see below).

### ETag generation

Stateless. Server derives it on every request from current DB state; no storage of handed-out ETags.

```
# Per-tag: resolve alias + expand hierarchy FIRST, then filter.
# The sentinel's effective tag set MUST match the hydration query's effective tag set.
effective_ids = get_tag_hierarchy(resolve_tag_alias(tag_id))  # per-tag feed only

sentinel = SELECT image_id, date_added
           FROM images
           WHERE status = 1
             [AND image_id IN (SELECT image_id FROM tag_links WHERE tag_id IN :effective_ids)]
           ORDER BY image_id DESC
           LIMIT 50;
etag = 'W/"' + sha1(','.join(f"{id}:{ts.isoformat()}" for id, ts in sentinel)) + '"'
```

Properties:
- New image uploaded → top-50 set changes → hash changes → cache busts.
- Image in the window hidden / status-changed → window shifts → hash changes.
- Image in the window *retagged* (tags added or removed without changing `date_added`) → hash unchanged, so the body may go stale. This is an accepted limitation bounded by the 5-minute `max-age`. Capturing tag mutations in the ETag would require hashing the tag set per entry, which is more joins per sentinel than it's worth for a 5-minute window.
- Older image outside the top 50 edited → hash unchanged, cache stays valid. Correct.

### Conditional request handling

1. Run sentinel query, derive ETag and `Last-Modified` (from the newest `date_added`).
2. If the request has `If-None-Match` matching our current ETag, or `If-Modified-Since` at or after our `Last-Modified`: return `304 Not Modified` with no body and the ETag / Last-Modified headers.
3. Otherwise: load full entry data (user + tag joins), render, return `200` with body + headers.

Cost profile: repeat polls when nothing changed cost one indexed `SELECT ... LIMIT 50` and a SHA-1. Polls with new content add the full render path.

The feed handler does not set a `Vary` header itself (no per-request variation in the rendered body). If a fronting proxy or FastAPI's `GZipMiddleware` applies `Content-Encoding: gzip`, the compression layer is responsible for setting `Vary: Accept-Encoding` so shared caches key the compressed and uncompressed responses correctly. This is standard proxy/middleware behavior and out of scope for the feed handler.

## Implementation

### File layout

```
app/
├── api/v1/
│   └── feeds.py           # New. Two thin route handlers.
└── services/
    └── feeds.py           # New. Query helpers + Atom rendering.
```

- `api/v1/feeds.py`: handlers under 30 lines each. Dependencies (db session), call service, set headers, return `Response`.
- `services/feeds.py`: pure helpers — query builders returning `list[ImageDetailedResponse]`, an ETag deriver over the sentinel query, and `build_atom_feed(feed_meta, entries) -> str`.
- Router registered in `app/main.py` alongside existing routers.
- No Pydantic response schema (response is XML, not JSON).

### Library

- Add `feedgenerator` (the Django-extracted PyPI package, last release 2025-08) to `pyproject.toml`.
- Use `feedgenerator.Atom1Feed` to assemble the document. Pass rendered strings in; the library handles namespace registration, date normalization (RFC 3339), XML escaping, and `<content type="html">` wrapping.
- Rejected: `feedgen` (stale, last release 2023-12; pulls lxml); hand-rolled `ElementTree` (~7 validator footguns around RFC 3339 dates with tz, stable tag-URI `<id>`, `<link rel="self">`, `<link rel="enclosure">` vs RSS-style `<enclosure>`, per-entry `<author><name>`, namespace registration, content type).

### Data flow

1. Handler receives request.
2. Service runs the sentinel query and derives ETag + Last-Modified.
3. If-None-Match / If-Modified-Since check → maybe return 304.
4. Service runs the hydration query, eager-loading relationships via the chained `selectinload` idiom used elsewhere in the codebase: `selectinload(Images.user)`, `selectinload(Images.tag_links).selectinload(TagLinks.tag)`. Dotted-attribute traversal in a single `selectinload(Images.tag_links.tag)` does not work in SQLAlchemy.
5. Results converted to `ImageDetailedResponse` via `ImageDetailedResponse.from_db_model(image)` (defined in `app/schemas/image.py:167`). Using `from_db_model` (not `model_validate`) is required because the schema's `tags` field is populated by manually mapping from the `tag_links` relationship — Pydantic's `from_attributes` cannot cross the `tag_links` → `tags` name difference. The request-context kwargs (`is_favorited`, `user_rating`, `prev_image_id`, `next_image_id`) all have defaults and are left at their defaults for feed entries.
6. `build_atom_feed(feed_meta, entries)` produces the XML string.
7. Return `Response(content=..., media_type="application/atom+xml; charset=utf-8")` with headers.

### Settings

- `settings.FRONTEND_BASE_URL` (new) — used to construct detail-page links. Default to `"https://e-shuushuu.net"` in production, `"http://localhost:3000"` or similar in dev.

## Edge cases

- **Empty feed** (no matching active images): valid feed, zero `<entry>` elements, `<updated>` = current UTC. `Last-Modified` header omitted (nothing to date); ETag falls out of the hash of an empty list, stable until content appears. 200, not 404.
- **Tag ID not found**: 404 with the repo's standard error shape.
- **Tag is an alias** (`alias_of IS NOT NULL`): `resolve_tag_alias()` follows the alias to the canonical tag, then `get_tag_hierarchy()` expands to include child tags. The feed serves images in the full effective set. Matches `GET /api/v1/tags/{id}/images`. No redirect; the request URL remains the alias's ID, but the content is the canonical set. Readers that subscribe via alias continue to receive the correct content after the alias is established.
- **Image with NULL `user_id`** (soft-deleted uploader): `<author><name>[deleted user]</name></author>`.
- **NULL `date_added`**: `Images.date_added` is nullable in the model but has `server_default=current_timestamp()` at the DB level, so in practice it's always populated. Defensive handling: if a sentinel row has NULL `date_added`, exclude it from the ETag tuple and from `Last-Modified` derivation (fall back to current UTC for feed-level `<updated>` and omit `Last-Modified`). For entry-level `<updated>` / `<published>` on such a row, fall back to current UTC — Atom requires `<updated>` on every entry, so dropping the entry from the feed would be more surprising than a slightly-wrong timestamp.
- **Image with no tags**: title falls back to `"Image #{id}"`. Feed is valid.
- **Caption contains HTML or XML specials**: library escapes as literal text inside `<content type="html">`. If we later want to render formatted captions, revisit the content type.
- **Concurrent insert between sentinel and render**: feed's `<updated>` may lag the freshest entry by one request. Bounded by the 5-minute max-age; next poll resolves.
- **DB error**: bubble up to 500 via standard FastAPI error handling.

## Testing

### Unit tests (`tests/unit/services/test_feeds.py`)

- `build_atom_feed` produces well-formed Atom; feed-level `<id>`, `<title>`, `<link rel="self">`, `<updated>` present.
- Entry `<id>` uses tag URI, is unique per image.
- Entry `<title>` follows composition rules: all four sections populated, any one missing, none populated (falls back to `"Image #{id}"`).
- `<category>` elements emitted once per tag, with the correct `scheme` per `TagType`.
- `<link rel="enclosure">` has the correct MIME type per file extension.
- Image with zero tags doesn't crash the builder.
- ETag is deterministic for identical input; changes when any `image_id` or `date_added` in the input changes.

### API tests (`tests/api/v1/test_feeds.py`)

- `GET /api/v1/images.atom` returns 200, `application/atom+xml; charset=utf-8`.
- Only `status=1` images appear; non-active images with other statuses excluded.
- Ordering is newest first.
- Capped at 50 entries when more than 50 eligible images exist.
- `GET /api/v1/tags/{id}/images.atom` returns only images tagged with that tag.
- `GET /api/v1/tags/{nonexistent_id}/images.atom` returns 404.
- Conditional requests:
  - First request returns 200 + ETag + Last-Modified.
  - Same ETag in `If-None-Match` → 304 with empty body.
  - After inserting a new image, repeat conditional request → 200 + new ETag.
  - `If-Modified-Since` with a timestamp before newest → 200.
  - `If-Modified-Since` with a timestamp at or after newest → 304.
- Soft-deleted uploader (user NULL) → `<author>` shows `"[deleted user]"`, no crash.
- `Cache-Control: public, max-age=300` present on 200 responses.
- Atom XML parses under `xml.etree.ElementTree.fromstring`.

### Out of scope

- W3C feed validator in CI (would require network); acceptable as a one-time manual check during initial rollout.
- Load or cache-hit-rate tests.

## Open issues / future work

- **Frontend `/tags/{id}` page**: feed-level `<link rel="alternate">` assumes this exists on the frontend. If it doesn't, either point to the global `/` or skip the alternate link.
- **Autodiscovery `<link>` tags in HTML**: frontend concern. Once feeds ship, the frontend should emit `<link rel="alternate" type="application/atom+xml" ...>` in the `<head>` of pages where a feed applies.
- **Per-user feeds**: not in this spec. If demand materializes, add `/api/v1/me/favorites.atom` etc. with a token-in-URL auth pattern (feed readers don't do cookies).
- **Retagging affects `<updated>`?**: currently `<updated>` = `date_added`. If we want retags to bust caches and trigger reader updates, we'd need to track per-image "last touched" time. Deferred.
