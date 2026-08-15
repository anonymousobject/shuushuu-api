# User Taste Profile & Recommendations — Design

**Date:** 2026-07-09
**Status:** Approved (brainstorm complete; implementation plan pending)
**Repos:** shuushuu-api (primary) + shuushuu-frontend — API-first PR pair on `feat/user-taste-profile`

## Goal

Build a per-user, data-based taste profile from favorites, ratings, and uploads:
identify the tags a user gravitates toward and the tags they rate high or low,
show that profile to the user, and use it to recommend images they may like.

## Decisions (from brainstorm)

| Question | Decision |
|---|---|
| V1 scope | Profile analytics **and** a recommendation feed, together |
| Signals | Favorites + ratings + uploads (comments excluded — ambiguous sentiment) |
| Recommendation surface | Dedicated login-gated feed page (`/recommended`, "For You") |
| Profile visibility | Private to the owner |
| Algorithm | Content-based tag affinity (lift + per-user-centered rating delta) |
| Feed computation | Live-scored at request time from the precomputed profile |

Collaborative filtering ("users who favorited what you favorited") and
embedding-based approaches were considered and deferred: the pairwise
precompute is a poor fit for MariaDB, it cold-starts badly, and neither
produces the tag-level analytics this feature exists to provide. Both can
layer onto the tag-affinity foundation later without rework.

## Data validation (dev DB, 2026-07-09)

- Scale: 5.7M favorites, 911k ratings, 1.1M images, 14.7M tag links.
  ~5.8k users have ≥10 favorites; ~2.1k have ≥10 ratings.
- Ratings are top-skewed (50% are a 10; global mean 8.53) but per-user
  stddev averages 1.16, so **centering each user's ratings on their own
  mean** yields usable signal. ~61k ratings ≤4 provide real negative evidence.
- Profile demo on a high-activity user (whitekitten): the favorites/uploads
  lift axis and the rating-delta axis independently agreed (Code Geass, C.C.,
  *couple* top both lists), and negative signal was specific and strong
  (one artist rated 1.17 over 127 ratings; Touhou −2.7 vs her mean).
- Raw lift saturates for tags where the user is essentially the sole
  contributor, and sheer-count tags like *long hair* (lift 1.3) crowd the
  top of a count-weighted list. Hence: smoothing, a display floor, and
  log-damping in the blend (below).

## Data model

New table `user_tag_affinity` (analytics table: no FKs, rebuilt wholesale,
same rationale as `tag_cooccurrence`):

| column | type | meaning |
|---|---|---|
| `user_id` | int unsigned, PK part | |
| `tag_id` | int unsigned, PK part | canonical (alias-resolved) tag |
| `fav_count` | int | user's favorited images carrying this tag |
| `upload_count` | int | user's uploads carrying this tag |
| `rated_count` | int | user's rated images carrying this tag |
| `rating_avg` | float | mean rating over those images |
| `lift` | float | smoothed positive-pool share vs global share |
| `rating_delta` | float | `rating_avg` − user's overall mean rating |
| `affinity` | float | blended score used for recommendation scoring |
| `updated_at` | datetime | refresh timestamp |

Index: PK `(user_id, tag_id)`; secondary `(user_id, affinity)` for top-K reads.

Row admission (min support): ≥5 supporting images in the positive pool
(favorites ∪ uploads) **or** ≥5 rated images with the tag. Users with <10
total signal events get no rows (cold start). Expected size ≈ 0.5–3M rows.

## Scoring

- **Positive pool** P(u) = favorites ∪ uploads, deduped, restricted to
  publicly-visible image statuses, alias-resolved tags.
- **Lift:** `lift(u,t) = (cnt(u,t) / |P(u)|) / ((usage(t) + K) / total_images)`
  with smoothing constant K (config, default 200). K damps the
  sole-contributor saturation seen in the demo.
- **Rating delta:** `rating_delta(u,t) = mean(rating over u's rated images
  with t) − mean(rating over all of u's ratings)`. Per-user centering makes
  the top-skewed rating scale comparable across users.
- **Blend:** `affinity = ln(max(lift, ε)) + β · rating_delta` with β a config
  default (tuned during implementation against real profiles). Components are
  stored separately, so retuning β or display thresholds requires no rebuild.
- Negative rows are kept: a strongly negative `rating_delta` produces a
  negative affinity that *suppresses* candidate images during feed scoring.

## Refresh job

`app/services/user_tag_affinity.py::refresh_user_tag_affinity()`, mirroring
`tag_cooccurrence`:

- Materialized regular helper tables (not TEMPORARY, not CTEs — MariaDB
  self-join and re-evaluation limits already established there).
- Database-scoped advisory lock (`GET_LOCK("user_tag_affinity_refresh:<db>")`)
  so cron and manual runs cannot collide, and pytest-xdist workers stay
  isolated.
- Build into a staging table, atomic `RENAME TABLE` swap.
- **Batched by user_id ranges (~500 users per INSERT…SELECT).** The unbatched
  join is ~75M intermediate rows (5.7M favorites × ~13 tags) — the exact shape
  that OOM-crashed MariaDB during the co-occurrence work.
- Nightly arq cron task (`app/tasks/`), default-off in tests, plus a manual
  runner `scripts/refresh_user_tag_affinity.py`.
- Config: min-support, K, β, eligibility threshold, cron toggle/cadence.

## API

### `GET /users/me/taste-profile` (auth required, owner-only)

Analytics payload from straight indexed reads:

- `top_tags`: highest-affinity tags with `lift ≥ 1.5` (display floor keeps
  *long hair*-class popularity noise out), grouped by tag type
  (artist/source/character/theme), each with counts and lift.
- `rated_high` / `rated_low`: largest positive/negative `rating_delta` tags
  meeting the rated-count support threshold, with `rating_avg`, `n`, delta.
- `summary`: pool size, ratings count, user mean rating, profile `updated_at`.
- No profile rows → `200` with `profile_ready: false`.

### `GET /images/recommended` (auth required)

Live-scored feed returning the **standard image-list response shape** (the
existing grid/list frontend components consume it unchanged), plus per-image
`because_tags` (top 2–3 contributing tags). Pipeline per request:

1. Read the user's top ~30 affinity tags (one indexed read).
2. Candidate images via `tag_links` on those tags, capped (~a few thousand),
   biased toward recent images. Caps are config values.
3. Score each candidate: Σ affinity over the image's profile-covered tags
   (negative affinities subtract).
4. Anti-join out images the user favorited, rated, or uploaded.
5. Apply standard visibility rules (public statuses + the user's
   hide-reposts setting), reusing existing feed filtering.
6. Order by score; paginate within the top ~500.

Cold start → `200`, empty list, `profile_ready: false`.

Known caveat: ranking may drift between page requests as new images arrive
(the same drift every time-ordered feed has); the top-500 cap bounds cost.

## Frontend

- **`/recommended` ("For You")**: nav link rendered only when logged in;
  page login-gated via the existing `returnTo` pattern. Thin route + load
  function over the standard grid/list components and pagination. The
  existing thumbnail hover tooltip gains a "because you like X, Y" line
  from `because_tags`. Cold start renders an explainer: favorite/rate images
  to build the profile; profiles refresh nightly.
- **Taste analytics section on the user's own profile page** (fits the
  page's existing recent-faves/uploads grammar), rendered only for self:
  "Your taste profile — only visible to you." Top tags by type, tags rated
  above/below the user's average; every tag links to its search. The
  `/recommended` page links here ("why these?").

## Testing (TDD, both repos)

- **API service tests** (seeded fixtures): lift math incl. smoothing, per-user
  centered rating delta, min-support admission, negative suppression,
  eligibility threshold; refresh lock-skip, idempotency, swap atomicity.
- **API endpoint tests**: auth gating, owner-only access, cold-start shape,
  seen-image exclusion, visibility rules, `because_tags` presence, response
  schema.
- **Frontend e2e** (authenticated-e2e pattern): page renders the grid for a
  profiled user; cold-start state for a fresh user; profile section visible
  on own profile and absent on others'.

## Rollout

- API PR first, then frontend PR (`feat/user-taste-profile` in both).
- Alembic migration for `user_tag_affinity`.
- All thresholds (min support, K, β, candidate/pagination caps, cron cadence)
  as config settings with the defaults above.
- Until the first refresh runs in an environment, all users see cold-start —
  no flag day.

## Out of scope (deferred)

- Collaborative filtering as a second signal layered onto tag affinity.
- Public/opt-in taste profiles (rating behavior is private today).
- Embedding-based similarity; ML-predicted-tag profiles.
- Recommendation sort option on the main feed / search.
