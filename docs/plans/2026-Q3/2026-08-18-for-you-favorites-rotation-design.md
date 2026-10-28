# For You v2: favorites as a live input + daily rotation

**Status:** Approved design, 2026-08-18. Builds on the taste profile
(2026-07-09-user-taste-profile-design.md) and on profile favorite tags
(api#345 / frontend#384, plan in
`<shuushuu-frontend-repo>/docs/plans/2026-Q3/2026-08-18-profile-favorite-tags-design.md`).
No schema changes; API-first deploy, frontend follows with regenerated types.

## Problem

The For You page is near-static, and users notice. The pipeline is
deterministic end to end: top-30 profile tags → candidate recall → score sum →
top 500 served in fixed order. Only favoriting or rating an image removes it;
merely seeing it changes nothing. New uploads must out-score images entrenched
at the top (score sums over all matching tags, so heavily-tagged older images
dominate). The profile itself has inertia — lift is computed over the user's
lifetime pool, so a new interest this month is noise against years of history —
and the nightly rebuild adds a day of lag on top. Reported outcome: the same
page every day. At least one user now rates images at the extremes purely to
coerce the feed — rational behaviour, because extreme ratings are the
fastest-moving input that exists. There is no legitimate control knob.

Meanwhile profile favorite tags (shipped 2026-08-18) are exactly the missing
signal: explicit, high-intent (capped 20/category), live, and able to express
conjunctions the per-tag profile cannot ("C.C. *from Code Geass*" as a
character+source combo).

## Goals

1. **Control:** a user's favorite tags visibly shape their For You feed, with
   effect on the next page load — no nightly wait.
2. **Freshness:** the page composition rotates daily instead of freezing.
3. **Cold start:** a user with no taste profile but with favorites gets a feed.

## Non-goals

- No favorites rails/sections on the page. Blended-feed-first was the explicit
  decision (2026-08-18); rails are re-evaluated on dev once this ships, and the
  favorites-matching machinery built here is what rails would reuse.
- No time-decay in the nightly affinity build (profile-inertia fix) — a
  candidate follow-up, deliberately out of scope.
- No impression/"seen" tracking. Seeded sampling gets rotation without a write
  per page view.
- No changes to favorites management UX, and no affinity-driven favorite
  *suggestions* (parked, per the favorites spec's non-goals).

## Design

`get_recommended_images` composes the feed from two pools and serves a
deterministic **day list** of up to `TASTE_FEED_POOL` (500) images.

### Favorites pool (new, read live per request)

- Load the caller's favorites: `user_favorite_links` joined to
  `character_source_links` for (character_tag_id, source_tag_id) pairs, plus
  `user_favorite_tags` for source/artist tag ids. Stored ids are canonical
  (the add flow refuses aliases); matching still alias-expands, same pattern
  as the existing top-tag expansion, so alias-tagged images match.
- Per favorite, fetch the most recent `TASTE_FAV_PER_FAVORITE_CAP` (default
  100) matching images. A combo matches images carrying **both** its character
  and source tags; a tag favorite matches its tag. Same exclusions as the main
  query: own uploads, favorited/rated images, status visibility,
  `hide_reposts`.
- Recall keeps a separate capped list per favorite (however the impl batches
  the fetch) rather than one merged set: the lists feed the round-robin draw
  below, so one prolific source cannot monopolise the favorites share.

### Affinity pool (existing scorer, deeper keep)

Scoring is unchanged. The scored keep-depth rises from `TASTE_FEED_POOL` to a
new `TASTE_SCORE_POOL` (default 1500): the day list samples 500 from the top
1500, so pool *membership* varies day to day instead of being a frozen top-500,
and recent uploads that score mid-pack get real appearances.

### Day-list composition (seeded, rotates daily)

- Seed: `(user_id, UTC date)` feeding `random.Random`. Same user, same day →
  identical list, so pagination is stable within a day; next day reshuffles.
- Draw 500 slots. Each slot draws from the favorites pool with probability
  `TASTE_FAV_SHARE` (default 0.33) while it has entries, else from the
  affinity pool — so each *page* carries roughly a one-third favorites share,
  not just the day list in aggregate.
- Favorites draws round-robin across the user's favorites in seeded order,
  taking recency-weighted picks within each favorite. Affinity draws are
  rank-weighted (higher score → more likely earlier) without replacement;
  exact weighting scheme is an implementation-plan detail, chosen so the top
  of today's feed is still recognisably "best matches", not uniform noise.
- All draws are without replacement across the whole day list: an image
  matching several favorites, or both pools, is served once. Cross-pool
  overlap is attributed to the favorite and counts toward the favorites share.
- `total` = day-list length (≤ 500), same pagination contract as today.
- Favorites edits mid-day change the pool and thus the day list on next load.
  Accepted and desired — "add a favorite, the feed responds now" is the point;
  a mid-session pagination shift is the same class of drift the live-scored
  feed already has.

### Cold start

No positive-affinity rows but favorites exist → the feed is the favorites pool
alone (same seeded draw). `profile_ready` keeps its exact meaning ("an
affinity profile exists"), but a non-ready response can now carry images. No
profile *and* no favorites behaves as today.

### Attribution

`RecommendedImageResponse` gains an optional field:

```python
class FavoriteAttribution(BaseModel):
    tag: TagSummary | None = None        # source/artist favorite
    character: TagSummary | None = None  # combo favorite (with source)
    source: TagSummary | None = None

because_favorite: FavoriteAttribution | None = None
```

Exactly one of `tag` / (`character`+`source`) is populated. When several
favorites match one image, the lowest-position favorite wins (the user's own
ranking). `because_tags` is unchanged and may coexist on the same image.

### Config

| Setting | Default | Meaning |
|---|---|---|
| `TASTE_SCORE_POOL` | 1500 | scored depth the daily sample draws from (≥ `TASTE_FEED_POOL`) |
| `TASTE_FAV_SHARE` | 0.33 | target per-page share of favorites-matched images |
| `TASTE_FAV_PER_FAVORITE_CAP` | 100 | recent matches recalled per favorite |

## Frontend

- Regenerate API types.
- `ImageTooltip` (the existing because-surface): add a line when
  `because_favorite` is present — "From your favorites: C.C. (Code Geass)" /
  "From your favorites: ask".
- A small ★ overlay on gallery/list items when `because_favorite` is present,
  so favorites-driven picks are identifiable without hovering. Only
  /recommended data carries the field, so other pages render unchanged.
- Copy: subtitle and "How does this work?" describe favorites influence and
  the daily reshuffle ("your profile refreshes nightly; the page reshuffles
  daily; favorite tags take effect immediately"). Empty/cold states gain a CTA
  to `/users/{id}/favorite-tags`; the favorites-only feed (images without
  `profile_ready`) shows a note that fuller recommendations unlock as the user
  favorites and rates images.

## Testing

- Composition is pure given its inputs: the service accepts an injectable
  sample date so pytest pins the seed. Covers: determinism (same day → same
  list, next day → different order), favorites share floor, both-pool dedupe
  with favorites attribution, combo both-tags matching, alias-tagged image
  matching, exclusions (own/seen/hidden) applied to the favorites pool,
  favorites-only cold start, empty-favorites fallback to pure affinity,
  lowest-position attribution.
- e2e (frontend): seed the test user with a favorite matching fixture images;
  assert a ★-attributed image appears in the feed and the tooltip names the
  favorite. Rotation itself is not e2e-tested (date-bound) — pytest owns it.

## Performance

The scoring path is untouched (measured ≈49ms worst case). New work per
request: favorite-match recall (up to 60 favorites × 100 recent matches each,
small indexed lookups whether batched or per-favorite) and a Python draw over
at most ~7,500 candidate ids — negligible. No new tables, no migration.

## Risks

- **Tuning knobs are guesses.** 0.33 share / 1500 pool / weight decay need
  eyes on a real profile on dev before deploy; they are config, not code.
- **Feed quality dip at the top:** sampling means page 1 is no longer strictly
  the highest-scored images. That trade *is* the feature (rotation), but the
  weighting must keep page 1 obviously good — validated on dev.
- **`profile_ready=false` with images** is a semantic loosening; the frontend
  is updated in lockstep, and no other consumer reads the field.
