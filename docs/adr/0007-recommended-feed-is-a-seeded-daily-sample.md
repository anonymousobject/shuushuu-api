# The recommended feed is a seeded daily sample, not strict score order

`/images/recommended` serves a "day list": up to `TASTE_FEED_POOL` (500) images
drawn from two pools — the affinity scorer's top `TASTE_SCORE_POOL` (1500) and
live per-favorite recall from the user's profile favorite tags — by seeded
rank-weighted sampling (`compose_day_list`, `app/services/recommendations.py`).
The rng seeds on `(user_id, UTC date)`, so pagination is stable within a day
and the composition reshuffles across days. Page 1 is deliberately *not* the
highest-scored images in fixed order.

The problem this settled: the deterministic pipeline froze the feed. Only
favoriting or rating removed an image, score sums entrenched heavily-tagged
older images at the top, and the lifetime-pool profile barely moves day to
day — users reported seeing the same page every day, and at least one user
rated images at the extremes purely to coerce the feed, because extreme
ratings were the fastest-moving input that existed. Favorites (the explicit
knob) and rotation (the freshness fix) are the two halves of the answer; the
spec is `docs/plans/2026-Q3/2026-08-18-for-you-favorites-rotation-design.md`.

## Considered Options

- **Keep strict score order** preserves "page 1 = best matches" but is the
  frozen feed users complained about: a closed top-500 in a fixed order until
  the nightly profile drifts. Restoring it would reintroduce the coercion
  incentive the explicit favorites knob exists to remove.
- **Impression tracking** (rotate by excluding seen-not-acted images) costs a
  write per page view and a new table to answer a question sampling answers
  for free, and it only ever shrinks the pool — it cannot resurface an image
  the way a daily reshuffle can.
- **Time-decayed profiles** (weight events by age in the nightly build) attack
  profile inertia, not serving order — a frozen ordering over a fresher
  profile still freezes. Deliberately deferred, not rejected; it composes
  with sampling if it ever lands.
- **Uniform daily shuffle** rotates but destroys ranking entirely; the chosen
  rank-weighted keys (Gumbel-max, exactly Efraimidis–Spirakis: first-pick
  probability proportional to `decay**rank`) keep page 1 recognisably good
  while letting mid-pool images surface. The Gumbel form of the key exists
  because the plain `u**(1/w)` key (and its `ln(u)/w` log form) underflows at
  deep ranks and the clamp collapses deep-rank order into ties — the linear
  rank term cannot underflow.

## Consequences

- Page-1 quality is a knob, not a guarantee: `TASTE_FAV_SHARE` (0.33),
  `TASTE_SCORE_POOL` (1500), and `TASTE_SAMPLE_DECAY` (0.997) are config, and
  the spec flags their defaults as first guesses to be tuned on dev.
- Within-day stability requires total ordering of every input: favorite-pool
  queries carry `(position, id)` tie-breakers because duplicate positions are
  reachable through the add flow's accepted concurrency race — an unspecified
  tied order would let two same-day requests compose different day lists and
  break pagination.
- The feed changes at UTC midnight, not the user's midnight, and mid-day
  favorites edits reshape the list immediately (that responsiveness is the
  point; the pagination shift it causes is accepted).
- Endpoint tests must be seed-independent (membership and pagination
  invariants); order-sensitive properties live in unit tests that inject the
  rng (`tests/services/test_recommendations_compose.py`) or the `day`
  parameter.
- `profile_ready=false` can now accompany a non-empty list (favorites-only
  cold start); the frontend's state matrix and `client.ts` contract comment
  encode this — any new consumer of the field must not treat it as "list is
  empty".
