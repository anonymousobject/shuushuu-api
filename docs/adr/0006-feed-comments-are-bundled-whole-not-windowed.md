# Feed comments are bundled whole, not windowed

`GET /api/v1/images/?include_comments=true` returns every non-deleted comment
on the returned page's images as a `comments` map on the list response, keyed
by image id, ordered by date with a post_id tie-break (comment dates have
second granularity; 178 same-second pairs exist in production data). The param
defaults off and the field serializes as `null`, so no existing consumer
changed. The comments depend on which image ids the page query returns, so a
separate comments request is inherently a second serial hop; bundling is the
only shape that removes the hop without rendering compromise. The query lives
in `comments_for_images()` (`app/services/comments.py`), the single owner for
any future serialization work (api#319).

## Considered Options

- **A latest-N-per-image window** (fe#357's original sketch) lost on both
  sides of the wire. The frontend list row threads everything it receives and
  offers a "show more" expander over data already in hand;
  `buildThreadStructure` silently drops any reply whose parent is missing, so
  a window either breaks threads on active images or grows thread-complete
  windowing machinery. And there is no payload to defend against per image:
  across 234,360 commented images the maximum is 63 comments, one image
  exceeds 50, none exceed 100. If a cap ever becomes necessary, the recorded
  choice is to keep the most recently *active* threads, thread-complete.
- **A SvelteKit streamed promise** completes navigation one stage earlier but
  pops comments in after render. Comments are primary content in list view,
  and SSR-completeness is a decision the frontend has re-affirmed repeatedly
  (home 2026-01, /search 2026-05).
- **Keeping the separate `/comments?image_ids=` round trip** preserved a live
  bug, not just latency: that fetch hard-capped at 100 comments per *page*, so
  a favorites-sorted per_page=100 page silently rendered zero comments for 21
  of its 89 commented images and could orphan replies mid-thread. The bundle
  is a strict superset of what the old path delivered.

## Consequences

- The cost is per-page, not per-image: the worst measured page (favorites
  sort, per_page=100) bundles 682 comments, ~+54 KB gzipped over the old
  truncated fetch; typical newest-sorted pages are unchanged. Any future cap
  debate starts from that page-level number, not the 63-per-image figure.
- The bundle inherits the endpoint's visibility filtering by construction —
  it only ever fetches comments for ids the caller's own page query returned.
- `/users/{id}/images` and `/users/{id}/favorites` share
  `ImageDetailedListResponse` and now emit `"comments": null`; the param
  exists only on the images list endpoint.
- Deploy order is API first, and it is mandatory, not advisory: the API
  ignores unknown query params, so a frontend-first deploy does not error —
  it silently renders every feed's list view with zero comments.
- The frontend proves the round trip stays dead in a Vitest load test, not
  Playwright: server-load fetches never touch the browser network stack, so
  an e2e network capture passes vacuously.
