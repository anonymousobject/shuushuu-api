# Linked characters and sources rank by shared images, counted on read

A source tag's page lists the characters linked to it, and a character's page
lists its sources. Both lists rank by how many images carry **both** tags —
the anchor tag and the linked one — computed per request in
`app/services/character_source_counts.py` and cached in Redis with a TTL,
rather than read from a stored counter. Settled in PR #368.

The problem this settled: the lists were ordered by the linked tag's global
`tags.usage_count`, which answers a different question. On the Genshin Impact
source page that put Charlotte (841 images, 8 of them Genshin) and Layla
(668 / 39) above Ganyu (290 / 290). The page truncates to 20 entries behind a
"Show all" button, so the characters the source is actually about were not
merely mis-ordered — they were off the visible list. Fixing the ranking forced
the question of where the shared count comes from, because no such number
existed anywhere in the schema.

## The count is a raw tag_links intersection

No image-status filter, no repost filter, no tag-hierarchy expansion. This
matches `tags.usage_count`, whose trigger is an unconditional
`usage_count = usage_count + 1` per `tag_links` row (ADR-0009), and therefore
matches the number it replaces for this purpose. It deliberately does **not**
match `total_image_count` on the same response, which is status-aware and
varies with the viewer's `show_all_images` / `hide_reposts` settings.

That divergence is the load-bearing part: a viewer-independent count is one
value for everyone, which is what makes it cacheable at all. A status-aware
count would need a cache entry per viewer-settings combination, or no cache.
The number is exposed as `shared_image_count` and used for ordering; it is not
presented to users as an image count, so the looser semantics do not surface.

## Considered Options

- **Keep ordering by `usage_count`** — free, and wrong. It answers "how big is
  this character overall", not "how much of this source is this character".
  This is the bug.
- **A denormalized `character_source_links.image_count`**, maintained by the
  `tag_links` triggers the way ADR-0009's counters are. Reads become free and
  sortable in SQL. Rejected for now on write-side cost and complexity: unlike
  every counter in ADR-0009, the target row is not identified by the inserted
  row's own columns. Adding tag T to image I must adjust the count for every
  `character_source_links` row pairing T with a tag already on I, which turns
  the single hottest write trigger in the system into a per-row query and
  fan-out update. Link creation and deletion would additionally need the new
  row's count backfilled. That is a real option if the read cost ever bites,
  and this ADR is the record of what it has to beat.
- **Count on read, TTL-cached (chosen)** — one query, no schema change, no
  write-path coupling, and the cache follows the existing
  `app/services/feed_count_cache.py` pattern (TTL-only, no invalidation).

## Why count-on-read does not contradict ADR-0009

ADR-0009 rejected count-on-read for the six scalar counters because they serve
hot paths: `usage_count` sorts a 235k-row tag list, `images.posts` filters
feeds at 1.1M-image scale. This count is read on exactly one endpoint, for one
tag at a time, and it is a map over a pair-relation rather than a scalar on the
row being written. The trade that decided ADR-0009 — enumerate every write path
forever, or let the database do it — is not the trade here; here it is a bounded
read cost against a fan-out write cost.

## Cost

Measured warm on the dev restore (prod-scale, Postgres 18), uncached:

| source | linked characters | images | query |
|---|---|---|---|
| Pokémon (290) | 1006 | 16.5k | ~140ms |
| Touhou (186) | 190 | 87k | ~135ms |
| Genshin Impact (216485) | 104 | 4.4k | ~5ms |

The join expands every tag on every image of the anchor tag before filtering to
linked tags, so it scales with the anchor's image count times average tags per
image. Three alternative query shapes were tried; ~140ms is the floor for the
worst case in the corpus. With the cache, warm request latency on those pages
is unchanged from before the fix.

## Consequences

- The TTL (`SHARED_COUNT_TTL`, 15 minutes) is not invalidated by tag edits. A
  tag added or removed moves one count by one, which almost never reorders the
  list, and the count is never shown as a figure — so staleness is invisible.
  Do not add write-path invalidation without a reason that survives that.
- Ordering lives in one place. The API returns both lists ranked; the frontend
  takes the order as given (shuushuu-frontend#403 deleted the client-side
  re-sort). A second sort on the client silently reintroduces the bug.
- Title remains the tiebreaker, and it stays in SQL: the query orders by title
  and the Python re-rank is a stable sort, so the collation is not reimplemented
  in Python.
- `GET /tags/{id}/characters` is a separate, paginated, alphabetical endpoint
  and is intentionally unaffected. It has no frontend consumer.
- If the read cost ever does bite, the denormalized column is the escape hatch,
  and its write-side fan-out is the thing to design — not the counter itself.
