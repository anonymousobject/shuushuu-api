# Bundled feed comments (`include_comments` on the images list)

**Status:** Approved design, 2026-08-17. Part 2 of shuushuu-frontend#357; part 1
(news ∥ images) shipped as frontend PR #363. Builds on the profiling in api#319
but does not depend on its fixes landing first.

## Problem

Every feed page the frontend serves (home, browse, search, top) loads in two
serial stages: fetch the image page from `GET /api/v1/images/`, then fetch that
page's comments from `GET /api/v1/comments/?image_ids=…`. The second request
adds a measured 6–16ms of serial time to every home and back navigation. It
cannot be parallelized — it needs the image ids the first request returns — and
it cannot be streamed, because comments are primary content in list view and
the frontend deliberately renders SSR-complete (the same no-pop-in decision was
applied to home in 2026-01 and `/search` in 2026-05).

The only way to remove the serial hop without pop-in is to return the comments
with the images.

## Evidence that shaped the design

fe#357 originally sketched a "latest N comments per image" parameter. Two
findings killed the window:

- **The list row needs the full comment set.** `ImageListRow` threads
  everything it receives (`buildThreadStructure`), sorts threads oldest-first,
  shows the first three, and offers "Show N more comments" — an expander over
  data already in hand. `buildThreadStructure` silently drops any reply whose
  parent is missing from its input, so a server-side window would both break
  the expander and make replies vanish on active images.
- **There is no payload to defend against.** In the production-copy dev DB
  (2026-08-17): 234,360 images have comments; the maximum on a single image is
  63; exactly one image exceeds 50; none exceed 100. The worst case a page can
  assemble is small, and the current fetch already carries the same data.

So the bundle returns **all comments for the page's images** — the same rows
the second request carries today, minus the round trip, minus the accidental
truncation of today's global `per_page=100` cap.

**Recorded for the future:** if a per-image cap ever becomes necessary, the
chosen semantic is *keep the most recently active threads* (rank by max
comment date across root and replies), thread-complete. Nothing is built for
this now.

## API design

- `GET /api/v1/images/` gains `include_comments: bool = False`.
- When true, the endpoint runs one extra batched query for the returned page's
  ids — `Comments` where `deleted == False` and `image_id IN (page ids)`,
  ordered by date ascending — and returns it as a new optional field on the
  list response:

  ```
  comments: dict[int, list[CommentResponse]] | None
  ```

  The field is `None` (serialized as `"comments": null`) when the param is
  off, so every existing consumer is untouched. Images without comments do not
  appear in the map.
- The query lives in a new service function,
  `app/services/comments.py::comments_for_images(db, image_ids)`, so it has one
  owner and one place to optimize if api#319's serialization work lands a
  leaner path.
- The user eager-load chain (`Comments.user → user_groups → group`), currently
  copied four times in `app/api/v1/comments.py`, moves into one shared helper
  used by the service and the existing endpoints.
- `CommentResponse` is reused as-is. A trimmed comment model is api#319's
  measured concern; nothing here precludes it.
- The existing `/comments` endpoints do not change and do not route through the
  new service — their semantics (flat pagination, counts, search, timeouts)
  are different, and forcing shared code across different behavior earns a
  function full of mode flags.

## Frontend design

- The four loads that call `GET /api/v1/images/` — `/` (home), `/browse`,
  `/search`, `/images/top` — pass `include_comments=true`, read the map off the
  images response (`imagesData.comments ?? {}`), and drop their second fetch.
  `/browse`'s inline copy of the batch-fetch logic disappears with it.
- Out of scope: `/recommended` and `/users/[id]/ratings` use different image
  endpoints and keep `fetchCommentsForImages`. The image-detail page and
  `/images/top`'s client-side view-toggle refresh keep using `/comments`.
- Types come from regenerating `api-generated.ts` after the API change lands;
  the frontend's existing `Record<number, CommentResponse[]>` plumbing consumes
  the map unchanged.

## Rollout

API ships first; the param defaults to off, so the deploy is invisible until
the frontend follows. No coordinated deploy.

## Testing

- API: param off → no `comments` field; param on → map matches the page's
  comments exactly, excludes deleted comments, omits comment-less images;
  empty page → empty map.
- Frontend (Playwright, real API): home list view renders comments with zero
  requests to `/api/v1/comments/` during load; existing comment-rendering and
  view-toggle specs stay green.

## Non-goals

- Trimmed comment serialization (api#319).
- Per-image caps or windowing (semantic recorded above, unbuilt).
- Comment counts in the UI.
- Bundling on `/recommended`, ratings, or the detail page.
