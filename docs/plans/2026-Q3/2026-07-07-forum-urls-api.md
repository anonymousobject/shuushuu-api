# Forum URLs (API + nginx) — Redirect Resolvers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redirect retired phpBB / old-PHP-site URLs to their new equivalents, and resolve a stable `/forum/posts/{id}` permalink to the right thread page + anchor.

**Architecture:** Split by whether the redirect needs a DB lookup. URL-computable cases (`image.php`, `profile`) are pure nginx rewrites. DB-lookup cases (`viewforum`, `viewtopic`, post permalink) are three small `301` resolver endpoints in `app/api/v1/forum.py`, keyed on the import's `legacy_*` columns / `post_id`; nginx maps the legacy/permalink paths onto them. All resolvers are permission-aware (`404`, never `301`, when the destination category isn't visible).

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, MariaDB, nginx (Docker), pytest.

**Spec:** `../../../shuushuu-frontend/docs/plans/2026-07-07-forum-urls-design.md` (frontend repo) — the shared design.

## Global Constraints

- **Branch:** `feat/forum-urls`, created off `feat/forum-import` (this worktree). It needs the import's `legacy_forum_id` / `legacy_topic_id` columns. Do not target `main`.
- **Status code:** every redirect returns `301` (`status.HTTP_301_MOVED_PERMANENTLY`).
- **Permission-aware:** each resolver resolves the target, then checks the destination category's visibility with the existing `_effective_perms` + `_visible_category` / `can_access` helpers, and raises `404` (not `403`, not `301`) when the caller can't see it — so gated Mod/Tagging categories stay hidden.
- **`FORUM_POSTS_PER_PAGE = 20`** must equal the frontend thread loader's `perPage` (`shuushuu-frontend: src/routes/forum/threads/[thread_id]/+page.server.ts`). Add a cross-reference comment.
- **`include_in_schema=False`** on all three endpoints — they are nginx-internal redirect utilities, not part of the public JSON API (keeps generated frontend types clean).
- **Test client does not follow redirects** (httpx default in `tests/conftest.py`), so assert `response.status_code == 301` and `response.headers["location"]` directly. Starlette 1.3.1 preserves `?`, `#`, `=` in the `Location` header (safe set `:/%#?=@[]!$&'()*+,;`), so exact-string `location` assertions are valid.
- **Types:** mypy must stay clean. Mirror the existing `# type: ignore[arg-type]` comments on SQLModel column comparisons in `where(...)` (see `get_thread`).
- **Tests:** real DB + real API, no mocks. Forum test fixtures live in `tests/api/v1/conftest.py` (`public_category`, `staff_category`, `public_thread`, `user_token`, `staff_token`, `make_thread`).

---

### Task 1: Legacy forum + topic resolvers

Two near-identical `301` resolvers for the retired phpBB URLs, resolved via `legacy_forum_id` / `legacy_topic_id`.

**Files:**
- Modify: `app/api/v1/forum.py` (imports at lines 6–7; append two endpoints at end of file, currently ~line 720)
- Test: `tests/api/v1/test_forum_legacy_redirects.py` (create)

**Interfaces:**
- Consumes: `_effective_perms(db, redis_client, user) -> set[str]`, `_visible_category(db, category_id, perms) -> ForumCategories` (raises 404 if gated/absent), `can_access(perms, required_perm) -> bool`, and `DbDep` / `RedisDep` / `OptionalCurrentUser` — all already defined in `forum.py`.
- Produces:
  - `GET /api/v1/forum/legacy/viewforum?f={int}` → `301` `Location: /forum/{category_id}`
  - `GET /api/v1/forum/legacy/viewtopic?t={int}&f={int?}` → `301` `Location: /forum/threads/{thread_id}`

- [ ] **Step 1: Write the failing tests**

Create `tests/api/v1/test_forum_legacy_redirects.py`:

```python
"""Tests for the legacy phpBB URL redirect resolvers."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.api.v1.conftest import make_thread


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestViewforumRedirect:
    """GET /api/v1/forum/legacy/viewforum"""

    async def test_redirects_to_category(
        self, client: AsyncClient, db_session: AsyncSession, public_category
    ):
        public_category.legacy_forum_id = 7
        await db_session.commit()
        r = await client.get("/api/v1/forum/legacy/viewforum?f=7")
        assert r.status_code == 301
        assert r.headers["location"] == f"/forum/{public_category.category_id}"

    async def test_unknown_forum_404(self, client: AsyncClient):
        r = await client.get("/api/v1/forum/legacy/viewforum?f=999999")
        assert r.status_code == 404

    async def test_gated_forum_hidden_from_anon_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category
    ):
        staff_category.legacy_forum_id = 3
        await db_session.commit()
        r = await client.get("/api/v1/forum/legacy/viewforum?f=3")
        assert r.status_code == 404

    async def test_gated_forum_visible_to_staff_301(
        self, client: AsyncClient, db_session: AsyncSession, staff_category, staff_token
    ):
        staff_category.legacy_forum_id = 3
        await db_session.commit()
        r = await client.get(
            "/api/v1/forum/legacy/viewforum?f=3", headers=_auth(staff_token)
        )
        assert r.status_code == 301
        assert r.headers["location"] == f"/forum/{staff_category.category_id}"


class TestViewtopicRedirect:
    """GET /api/v1/forum/legacy/viewtopic"""

    async def test_redirects_to_thread(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        public_thread.legacy_topic_id = 2096
        await db_session.commit()
        r = await client.get("/api/v1/forum/legacy/viewtopic?f=10&t=2096")
        assert r.status_code == 301
        assert r.headers["location"] == f"/forum/threads/{public_thread.thread_id}"

    async def test_topic_id_alone_is_enough(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        public_thread.legacy_topic_id = 55
        await db_session.commit()
        r = await client.get("/api/v1/forum/legacy/viewtopic?t=55")
        assert r.status_code == 301
        assert r.headers["location"] == f"/forum/threads/{public_thread.thread_id}"

    async def test_unknown_topic_404(self, client: AsyncClient):
        r = await client.get("/api/v1/forum/legacy/viewtopic?t=999999")
        assert r.status_code == 404

    async def test_gated_topic_hidden_from_anon_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category
    ):
        thread = await make_thread(db_session, staff_category, title="Secret")
        thread.legacy_topic_id = 42
        await db_session.commit()
        r = await client.get("/api/v1/forum/legacy/viewtopic?t=42")
        assert r.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_forum_legacy_redirects.py -v`
Expected: the `301` tests FAIL (the routes don't exist yet → FastAPI returns `404`, so `assert status_code == 301` fails). The `*_404` tests pass trivially for now (unknown route → 404); they become meaningful once the routes exist.

- [ ] **Step 3: Add the imports**

In `app/api/v1/forum.py`, change the FastAPI import line (line 6) and add the response import right after it:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
```

- [ ] **Step 4: Append the two resolver endpoints**

At the **end** of `app/api/v1/forum.py`, add:

```python
# ===== Legacy phpBB URL redirects & post permalinks =====
# Resolve retired forum URLs (and the /forum/posts/{id} permalink) to their new
# equivalents. Permission-aware: 404 (never 301) when the destination category
# isn't visible to the caller, so gated categories stay hidden. nginx maps the
# legacy/permalink paths onto these endpoints; they are not part of the public
# JSON API (include_in_schema=False).


@router.get("/legacy/viewforum", include_in_schema=False)
async def legacy_viewforum(
    db: DbDep,
    redis_client: RedisDep,
    current_user: OptionalCurrentUser,
    f: Annotated[int, Query(description="Legacy phpBB forum id")],
) -> RedirectResponse:
    """301 an old forums/viewforum.php?f=… to the new category page."""
    perms = await _effective_perms(db, redis_client, current_user)
    result = await db.execute(
        select(ForumCategories).where(ForumCategories.legacy_forum_id == f)  # type: ignore[arg-type]
    )
    category = result.scalars().first()
    if category is None or not can_access(perms, category.view_perm):
        raise HTTPException(status_code=404, detail="Forum not found")
    return RedirectResponse(
        url=f"/forum/{category.category_id}",
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )


@router.get("/legacy/viewtopic", include_in_schema=False)
async def legacy_viewtopic(
    db: DbDep,
    redis_client: RedisDep,
    current_user: OptionalCurrentUser,
    t: Annotated[int, Query(description="Legacy phpBB topic id")],
    f: Annotated[int | None, Query(description="Legacy forum id (ignored)")] = None,
) -> RedirectResponse:
    """301 an old forums/viewtopic.php?f=…&t=… to the new thread page."""
    perms = await _effective_perms(db, redis_client, current_user)
    result = await db.execute(
        select(ForumThreads).where(ForumThreads.legacy_topic_id == t)  # type: ignore[arg-type]
    )
    thread = result.scalars().first()
    if thread is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    # 404 if the destination category is gated and not visible to the caller.
    await _visible_category(db, thread.category_id, perms)
    return RedirectResponse(
        url=f"/forum/threads/{thread.thread_id}",
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_forum_legacy_redirects.py -v`
Expected: PASS (all 8 tests).

- [ ] **Step 6: Typecheck and commit**

Run: `uv run mypy app/api/v1/forum.py`
Expected: no new errors.

```bash
git add app/api/v1/forum.py tests/api/v1/test_forum_legacy_redirects.py
git commit -m "feat(forum): legacy viewforum/viewtopic redirect resolvers"
```

---

### Task 2: Post permalink resolver

Resolve `/forum/posts/{id}` to the post's thread, computed page, and `#post-{id}` anchor.

**Files:**
- Modify: `app/api/v1/forum.py` (add the `FORUM_POSTS_PER_PAGE` constant after `RedisDep` at ~line 46; append the `post_permalink` endpoint at end of file)
- Test: `tests/api/v1/test_forum_permalink.py` (create)

**Interfaces:**
- Consumes: `FORUM_POSTS_PER_PAGE` (this task), `_effective_perms`, `_visible_category`, `DbDep` / `RedisDep` / `OptionalCurrentUser`, and `ForumPosts` / `ForumThreads` models.
- Produces: `GET /api/v1/forum/posts/{post_id}/redirect` → `301` `Location: /forum/threads/{thread_id}?page={n}#post-{post_id}`. Page math mirrors `get_thread`: posts ordered by `post_id`, `offset = (page-1)*FORUM_POSTS_PER_PAGE`, deleted posts counted (they render inline as tombstones).

- [ ] **Step 1: Write the failing tests**

Create `tests/api/v1/test_forum_permalink.py`:

```python
"""Tests for the forum post permalink resolver."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forum import ForumPosts, ForumThreads
from tests.api.v1.conftest import make_thread


async def _opening_post(db_session: AsyncSession, thread: ForumThreads) -> ForumPosts:
    result = await db_session.execute(
        select(ForumPosts)
        .where(ForumPosts.thread_id == thread.thread_id)  # type: ignore[arg-type]
        .order_by(ForumPosts.post_id)  # type: ignore[arg-type]
    )
    return result.scalars().first()


async def _add_reply(
    db_session: AsyncSession, thread: ForumThreads, text: str
) -> ForumPosts:
    post = ForumPosts(thread_id=thread.thread_id, user_id=1, post_text=text)
    db_session.add(post)
    await db_session.flush()
    await db_session.refresh(post)
    return post


class TestPostPermalink:
    """GET /api/v1/forum/posts/{post_id}/redirect"""

    async def test_opening_post_page_1(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        opening = await _opening_post(db_session, public_thread)
        r = await client.get(f"/api/v1/forum/posts/{opening.post_id}/redirect")
        assert r.status_code == 301
        assert (
            r.headers["location"]
            == f"/forum/threads/{public_thread.thread_id}?page=1#post-{opening.post_id}"
        )

    async def test_page_boundary(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        # Opening post is #1 (rank 0). Add 20 replies -> 21 posts total.
        replies = [await _add_reply(db_session, public_thread, f"r{i}") for i in range(20)]
        await db_session.commit()
        # replies[18] is the 20th post overall (rank 19) -> page 1.
        # replies[19] is the 21st post overall (rank 20) -> page 2.
        r20 = await client.get(f"/api/v1/forum/posts/{replies[18].post_id}/redirect")
        assert r20.headers["location"].endswith(f"?page=1#post-{replies[18].post_id}")
        r21 = await client.get(f"/api/v1/forum/posts/{replies[19].post_id}/redirect")
        assert r21.headers["location"].endswith(f"?page=2#post-{replies[19].post_id}")

    async def test_unknown_post_404(self, client: AsyncClient):
        r = await client.get("/api/v1/forum/posts/999999/redirect")
        assert r.status_code == 404

    async def test_deleted_post_still_resolves(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        reply = await _add_reply(db_session, public_thread, "doomed")
        reply.deleted = True
        await db_session.commit()
        r = await client.get(f"/api/v1/forum/posts/{reply.post_id}/redirect")
        assert r.status_code == 301
        assert f"#post-{reply.post_id}" in r.headers["location"]

    async def test_gated_thread_post_hidden_from_anon_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category
    ):
        thread = await make_thread(db_session, staff_category, title="Secret")
        opening = await _opening_post(db_session, thread)
        r = await client.get(f"/api/v1/forum/posts/{opening.post_id}/redirect")
        assert r.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_forum_permalink.py -v`
Expected: the `301` tests FAIL (route missing → `404`).

- [ ] **Step 3: Add the `FORUM_POSTS_PER_PAGE` constant**

In `app/api/v1/forum.py`, right after the `RedisDep = Annotated[...]` line (~line 46) and before the `# ===== Shared helpers =====` comment, add:

```python
# The permalink resolver computes a post's page with this; it MUST equal the
# frontend thread page's per-page size (shuushuu-frontend:
# src/routes/forum/threads/[thread_id]/+page.server.ts, `perPage`). If that
# page size changes, change this too.
FORUM_POSTS_PER_PAGE = 20
```

- [ ] **Step 4: Append the permalink endpoint**

At the end of `app/api/v1/forum.py` (after the `legacy_viewtopic` endpoint from Task 1), add:

```python
@router.get("/posts/{post_id}/redirect", include_in_schema=False)
async def post_permalink(
    post_id: int,
    db: DbDep,
    redis_client: RedisDep,
    current_user: OptionalCurrentUser,
) -> RedirectResponse:
    """301 a stable /forum/posts/{post_id} permalink to the post's thread, page,
    and anchor. Page math matches get_thread's pagination: posts ordered by
    post_id, FORUM_POSTS_PER_PAGE per page, deleted posts counted (they render
    inline as tombstones)."""
    perms = await _effective_perms(db, redis_client, current_user)
    post = await db.get(ForumPosts, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    thread = await db.get(ForumThreads, post.thread_id)
    if thread is None:  # FK guarantees a parent thread; guard defensively anyway.
        raise HTTPException(status_code=404, detail="Post not found")
    # 404 if the destination category is gated and not visible to the caller.
    await _visible_category(db, thread.category_id, perms)
    rank = (
        await db.execute(
            select(func.count())
            .select_from(ForumPosts)
            .where(ForumPosts.thread_id == post.thread_id)  # type: ignore[arg-type]
            .where(ForumPosts.post_id < post_id)  # type: ignore[arg-type]
        )
    ).scalar() or 0
    page = rank // FORUM_POSTS_PER_PAGE + 1
    return RedirectResponse(
        url=f"/forum/threads/{thread.thread_id}?page={page}#post-{post_id}",
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_forum_permalink.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 6: Typecheck and commit**

Run: `uv run mypy app/api/v1/forum.py`
Expected: no new errors.

```bash
git add app/api/v1/forum.py tests/api/v1/test_forum_permalink.py
git commit -m "feat(forum): post permalink resolver (/forum/posts/{id})"
```

---

### Task 3: nginx redirect config

Add the pure rewrites (`image.php`, `profile`) and the proxied resolver locations (`viewforum`, `viewtopic`, `/forum/posts/{id}`) to both the production template and the dev config.

**Files:**
- Modify: `docker/nginx/frontend-production.conf.template` (insert after the existing `location ~ "^/image/([0-9]+)/?$"` block, ~line 193)
- Modify: `docker/nginx/frontend.conf.dev` (insert after the `location /images/forum-archive/` block, ~line 92, before `location /`)

**Interfaces:**
- Consumes: the Task 1/2 endpoints at `/api/v1/forum/legacy/viewforum`, `/api/v1/forum/legacy/viewtopic`, `/api/v1/forum/posts/{id}/redirect`.
- Produces: `301`s at the legacy/permalink URLs. `image.php`/`profile` are answered directly by nginx; the forum ones proxy to the API. **Note the permalink location captures the id in the `rewrite` regex, not the `location` regex** — a bare `rewrite ^ …/$1/… break` inside a regex `location` would read the rewrite's (empty) captures, not the location's, and proxy `/api/v1/forum/posts//redirect`.

- [ ] **Step 1: Edit the production template**

In `docker/nginx/frontend-production.conf.template`, immediately after the existing block:

```nginx
    # Legacy PHP URL redirects (e-shuushuu.net/image/827934/ → /images/827934)
    location ~ "^/image/([0-9]+)/?$" {
        return 301 /images/$1;
    }
```

add:

```nginx
    # image.php?mode=view&image_id=N → /images/N
    # (the /image/N/ path form is handled by the rule above)
    location = /image.php {
        if ($arg_image_id) { return 301 /images/$arg_image_id; }
        return 404;
    }

    # Old profile links → /users/N (legacy profile ids == current user ids)
    location ~ "^/profile/([0-9]+)/?$" { return 301 /users/$1; }
    location = /profile.php {
        # Imported links use ?mode=view_profile&user_id=N (verified against dev data).
        if ($arg_user_id) { return 301 /users/$arg_user_id; }
        return 404;
    }

    # Legacy phpBB forum URLs → new forum, resolved by the API via legacy_* columns.
    location = /forums/viewforum.php {
        set $api_upstream api:8000;
        rewrite ^ /api/v1/forum/legacy/viewforum break;
        proxy_pass http://$api_upstream;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location = /forums/viewtopic.php {
        set $api_upstream api:8000;
        rewrite ^ /api/v1/forum/legacy/viewtopic break;
        proxy_pass http://$api_upstream;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Forum post permalink: /forum/posts/{id} → thread + page + anchor.
    location ~ "^/forum/posts/[0-9]+/?$" {
        set $api_upstream api:8000;
        rewrite "^/forum/posts/([0-9]+)/?$" /api/v1/forum/posts/$1/redirect break;
        proxy_pass http://$api_upstream;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
```

- [ ] **Step 2: Edit the dev config**

In `docker/nginx/frontend.conf.dev`, after the `location /images/forum-archive/ { … }` block and before `# Frontend - proxy to Vite dev server`, add the same rules with dev's direct upstream (`api:8000`, no `$api_upstream` variable):

```nginx
    # --- Legacy URL redirects (forum URLs feature) ---
    location = /image.php {
        if ($arg_image_id) { return 301 /images/$arg_image_id; }
        return 404;
    }
    location ~ "^/profile/([0-9]+)/?$" { return 301 /users/$1; }
    location = /profile.php {
        if ($arg_user_id) { return 301 /users/$arg_user_id; }
        return 404;
    }
    location = /forums/viewforum.php {
        rewrite ^ /api/v1/forum/legacy/viewforum break;
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    location = /forums/viewtopic.php {
        rewrite ^ /api/v1/forum/legacy/viewtopic break;
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    location ~ "^/forum/posts/[0-9]+/?$" {
        rewrite "^/forum/posts/([0-9]+)/?$" /api/v1/forum/posts/$1/redirect break;
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
```

- [ ] **Step 3: Restart dev nginx and validate the config**

The dev conf is mounted as a template and env-substituted at container start, so a config change needs a container restart (not just `nginx -s reload`).

Run:
```bash
docker restart shuushuu-nginx
docker exec shuushuu-nginx nginx -t
```
Expected: `nginx: configuration file /etc/nginx/nginx.conf test is successful`, and `docker ps` shows `shuushuu-nginx` up (not restarting).

- [ ] **Step 4: Verify the pure rewrites (hard gate — no API needed)**

Run:
```bash
curl -sI "http://localhost:3000/image.php?mode=view&image_id=71000" | grep -iE "^HTTP|^location"
curl -sI "http://localhost:3000/profile/813167"                     | grep -iE "^HTTP|^location"
```
Expected:
```
HTTP/1.1 301 Moved Permanently
location: /images/71000
HTTP/1.1 301 Moved Permanently
location: /users/813167
```

- [ ] **Step 5: Smoke-test the proxied resolvers (needs the API serving this branch)**

Precondition: the `shuushuu-api` container must be serving this branch's code (the Task 1/2 endpoints). If the api container mounts a different checkout, point it at this worktree or merge the branch into the served checkout first, then `docker restart shuushuu-api`.

Look up real ids from dev and curl through nginx (DB creds are in the repo `.env` — `DATABASE_URL` / `MARIADB_*`):
```bash
docker exec shuushuu-mariadb-dev mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" shuushuu_dev -N -e \
  "SELECT legacy_topic_id, thread_id FROM forum_threads WHERE legacy_topic_id IS NOT NULL LIMIT 1;"
docker exec shuushuu-mariadb-dev mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" shuushuu_dev -N -e \
  "SELECT post_id, thread_id FROM forum_posts LIMIT 1;"

curl -sI "http://localhost:3000/forums/viewtopic.php?f=10&t=<legacy_topic_id>" | grep -iE "^HTTP|^location"
# expect: 301 ; location: /forum/threads/<thread_id>
curl -sI "http://localhost:3000/forum/posts/<post_id>" | grep -iE "^HTTP|^location"
# expect: 301 ; location: /forum/threads/<thread_id>?page=N#post-<post_id>
```
Expected: both return `301` with the mapped `Location`. (This is a manual smoke check, not an automated gate — the resolver logic itself is covered by the Task 1/2 pytest suites.)

- [ ] **Step 6: Commit**

```bash
git add docker/nginx/frontend-production.conf.template docker/nginx/frontend.conf.dev
git commit -m "feat(forum): nginx legacy URL redirects + post permalink routing"
```

---

## Deploy

Deploy this API branch (endpoints + nginx) **before** the frontend copy-link button, so the button's `/forum/posts/{id}` links resolve. Per environment the `viewforum`/`viewtopic` redirects only return real targets once the import has populated `legacy_*` there (the permalink resolver works as soon as the forum exists — it uses no legacy columns). Rebase onto `main` after the forum (#263) + import (#264) land.

## Self-verify before finishing

Run the full forum suite plus the two new files and mypy:
```bash
uv run pytest tests/api/v1/test_forum_legacy_redirects.py tests/api/v1/test_forum_permalink.py tests/api/v1/ -q
uv run mypy app/api/v1/forum.py
```
