# Forum System (API) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backend for the in-site forum: categories with permission-gated access, flat threads with pinned/locked/soft-delete, posts with markdown + edit tracking, and per-user unread tracking.

**Architecture:** Four new tables (`forum_categories`, `forum_threads`, `forum_posts`, `forum_thread_reads`) whose post shape mirrors the proven `Comments` model. Category access is controlled by nullable permission-name columns validated against a `FORUM_ACCESS_*` whitelist. Thread list-ordering columns (`post_count`, `last_post_at`, `last_post_user_id`) are denormalized and recomputed (never incremented) inside each post mutation's transaction with the thread row locked.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy (async, MariaDB), Alembic, Redis perm cache, pytest (`tests/api/v1`, fixtures from `tests/conftest.py`).

**Spec:** `../shuushuu-frontend/docs/plans/2026-07-06-forum-design.md` (committed on that repo's `feat/forum` branch).

## Global Constraints

- TDD for every task: failing test → minimal code → pass → commit. Run tests with `uv run pytest <path> -v` from the repo root.
- Work on a `feat/forum` branch in a worktree under `.worktrees/` (repo convention; main checkout has unrelated WIP — NEVER `git add -A`, always add explicit paths).
- All new PK/FK integer columns in the migration MUST be `mysql.INTEGER(unsigned=True)` — `users.user_id` is unsigned and a signed FK referencing it fails with errno 150.
- All datetime columns use `app.models.types.UtcDateTime` in models and `sa.DateTime()` in the migration (matches existing tables).
- Response envelope for paginated lists: `{total, page, per_page, <items>}` via `PaginationParams` from `app/api/dependencies.py`.
- View-gated or missing content returns **404** (never 403) so gated categories don't leak existence. Permission failures on non-view actions return 403.
- Markdown is stored raw and rendered via `app.utils.markdown.parse_markdown` in a `post_text_html` computed field (same as `CommentResponse`).
- Group titles in migrations are exactly `'Admins'`, `'Mods'`, `'Taggers'`.
- Follow file style of the module you're editing; run `uv run ruff check app tests` and `uv run mypy app` before each commit.
- Commit messages: conventional commits, e.g. `feat(forum): ...`, ending with the Co-Authored-By/Claude-Session trailer used in this repo.

## File Structure

| File | Responsibility |
|---|---|
| `app/models/forum.py` (new) | The four SQLModel tables |
| `app/models/__init__.py` (modify) | Register new models |
| `app/core/permissions.py` (modify) | 4 new `Permission` entries + `FORUM_ACCESS_PERMISSIONS` whitelist |
| `alembic/versions/<hash>_add_forum_tables.py` (new) | Tables + indexes + perm grants |
| `app/core/user_loader.py` (modify) | Gain `build_user_summaries()` (moved from `app/api/v1/admin.py`) |
| `app/schemas/forum.py` (new) | Request/response schemas |
| `app/services/forum.py` (new) | `can_access`, `recompute_thread_stats`, `upsert_thread_read` |
| `app/api/v1/forum.py` (new) | All `/forum/*` routes (categories, threads, posts) |
| `app/api/v1/__init__.py` (modify) | Register router |
| `tests/api/v1/conftest.py` (new) | Shared forum fixtures (categories, thread factory, perm-grant helper) |
| `tests/schemas/test_forum.py` (new) | Schema validation tests |
| `tests/services/test_forum.py` (new) | Service helper tests |
| `tests/api/v1/test_forum_categories.py` (new) | Category endpoint tests |
| `tests/api/v1/test_forum_threads.py` (new) | Thread endpoint tests |
| `tests/api/v1/test_forum_posts.py` (new) | Post endpoint tests |

---

### Task 1: Models, permissions, and migration

**Files:**
- Create: `app/models/forum.py`
- Modify: `app/models/__init__.py`
- Modify: `app/core/permissions.py`
- Create: `alembic/versions/<generated>_add_forum_tables.py`
- Test: `tests/api/v1/test_forum_models.py` (round-trip smoke test; guards model↔migration agreement)

**Interfaces:**
- Produces: `ForumCategories` (PK `category_id`; `title`, `description`, `sort_order`, `view_perm`, `thread_create_perm`, `reply_perm`), `ForumThreads` (PK `thread_id`; `category_id`, `title`, `user_id`, `date`, `pinned`, `locked`, `deleted`, `post_count`, `last_post_at`, `last_post_user_id`), `ForumPosts` (PK `post_id`; `thread_id`, `user_id`, `post_text`, `date`, `ip`, `deleted`, `update_count`, `last_updated`, `last_updated_user_id`), `ForumThreadReads` (composite PK `user_id`+`thread_id`; `last_read_at`).
- Produces: `Permission.FORUM_ACCESS_STAFF/FORUM_ACCESS_TAGGER/FORUM_MODERATE/FORUM_CATEGORY_MANAGE`, `FORUM_ACCESS_PERMISSIONS: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/v1/test_forum_models.py`:

```python
"""Round-trip smoke tests: forum models insert/read against the migrated schema."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forum import ForumCategories, ForumPosts, ForumThreadReads, ForumThreads


async def test_forum_tables_round_trip(db_session: AsyncSession):
    """Insert one row per forum table through the ORM and read it back."""
    cat = ForumCategories(title="Smoke Category", description="desc", sort_order=1)
    db_session.add(cat)
    await db_session.flush()

    thread = ForumThreads(category_id=cat.category_id, title="Smoke thread", user_id=1)
    db_session.add(thread)
    await db_session.flush()

    post = ForumPosts(thread_id=thread.thread_id, user_id=1, post_text="hello")
    db_session.add(post)
    await db_session.flush()
    await db_session.refresh(post)

    read = ForumThreadReads(user_id=1, thread_id=thread.thread_id, last_read_at=post.date)
    db_session.add(read)
    await db_session.commit()

    fetched = await db_session.get(ForumThreads, thread.thread_id)
    assert fetched is not None
    assert fetched.pinned is False
    assert fetched.locked is False
    assert fetched.deleted is False
    assert fetched.post_count == 0  # denormalized fields start at defaults
    assert post.date is not None  # server_default filled


async def test_forum_permissions_in_enum():
    """The four forum permissions exist and the whitelist holds the two access tiers."""
    from app.core.permissions import FORUM_ACCESS_PERMISSIONS, Permission

    assert Permission.FORUM_ACCESS_STAFF.value == "forum_access_staff"
    assert Permission.FORUM_ACCESS_TAGGER.value == "forum_access_tagger"
    assert Permission.FORUM_MODERATE.value == "forum_moderate"
    assert Permission.FORUM_CATEGORY_MANAGE.value == "forum_category_manage"
    assert FORUM_ACCESS_PERMISSIONS == {"forum_access_staff", "forum_access_tagger"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_forum_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.forum'`

- [ ] **Step 3: Create the models**

Create `app/models/forum.py`:

```python
"""
SQLModel-based Forum models.

Four tables: forum_categories, forum_threads, forum_posts, forum_thread_reads.
ForumPosts mirrors the Comments model shape (soft-delete, edit tracking, raw
markdown in post_text) so rendering and moderation work identically.

forum_threads carries denormalized post_count/last_post_at/last_post_user_id
maintained by app.services.forum.recompute_thread_stats — always recomputed
from live posts inside the mutating transaction, never incremented.
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKeyConstraint, Index, Text, text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class ForumCategories(SQLModel, table=True):
    """Forum category. The *_perm columns hold a permission title required for
    that action (values restricted to FORUM_ACCESS_PERMISSIONS at the API layer);
    NULL means view=public (incl. logged-out) / create+reply=any logged-in user."""

    __tablename__ = "forum_categories"

    category_id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=100, unique=True)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int = Field(default=0)
    view_perm: str | None = Field(default=None, max_length=64)
    thread_create_perm: str | None = Field(default=None, max_length=64)
    reply_perm: str | None = Field(default=None, max_length=64)


class ForumThreads(SQLModel, table=True):
    """Forum thread. The opening post is the thread's first forum_posts row."""

    __tablename__ = "forum_threads"

    __table_args__ = (
        ForeignKeyConstraint(
            ["category_id"],
            ["forum_categories.category_id"],
            ondelete="RESTRICT",
            onupdate="CASCADE",
            name="fk_forum_threads_category_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_threads_user_id",
        ),
        ForeignKeyConstraint(
            ["last_post_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_forum_threads_last_post_user_id",
        ),
        Index("idx_forum_threads_list", "category_id", "pinned", "last_post_at"),
        Index("fk_forum_threads_user_id", "user_id"),
        Index("fk_forum_threads_last_post_user_id", "last_post_user_id"),
    )

    thread_id: int | None = Field(default=None, primary_key=True)
    category_id: int
    title: str = Field(max_length=255)
    user_id: int
    date: datetime = Field(
        sa_column=Column(UtcDateTime, nullable=False, server_default=text("current_timestamp()"))
    )
    pinned: bool = Field(default=False)
    locked: bool = Field(default=False)
    deleted: bool = Field(default=False, index=True)

    # Denormalized from forum_posts; see recompute_thread_stats
    post_count: int = Field(default=0)
    last_post_at: datetime | None = Field(
        default=None, sa_column=Column(UtcDateTime, nullable=True)
    )
    last_post_user_id: int | None = Field(default=None)


class ForumPosts(SQLModel, table=True):
    """Forum post; field shape mirrors Comments (table 'posts')."""

    __tablename__ = "forum_posts"

    __table_args__ = (
        ForeignKeyConstraint(
            ["thread_id"],
            ["forum_threads.thread_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_posts_thread_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_posts_user_id",
        ),
        ForeignKeyConstraint(
            ["last_updated_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_forum_posts_last_updated_user_id",
        ),
        Index("idx_forum_posts_thread_date", "thread_id", "date"),
        Index("fk_forum_posts_user_id", "user_id"),
        Index("fk_forum_posts_last_updated_user_id", "last_updated_user_id"),
    )

    post_id: int | None = Field(default=None, primary_key=True)
    thread_id: int
    user_id: int
    post_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    date: datetime = Field(
        sa_column=Column(UtcDateTime, nullable=False, server_default=text("current_timestamp()"))
    )

    # Soft-delete flag
    deleted: bool = Field(default=False, index=True)

    # Public update tracking
    update_count: int = Field(default=0)

    # Internal tracking fields (privacy-sensitive)
    ip: str = Field(default="", max_length=45)

    # Internal moderation fields
    last_updated: datetime | None = Field(
        default=None, sa_column=Column(UtcDateTime, nullable=True)
    )
    last_updated_user_id: int | None = Field(default=None)


class ForumThreadReads(SQLModel, table=True):
    """Per-user read position. A thread is unread when the user has no row or
    last_read_at < thread.last_post_at."""

    __tablename__ = "forum_thread_reads"

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_thread_reads_user_id",
        ),
        ForeignKeyConstraint(
            ["thread_id"],
            ["forum_threads.thread_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_thread_reads_thread_id",
        ),
        Index("fk_forum_thread_reads_thread_id", "thread_id"),
    )

    user_id: int | None = Field(default=None, primary_key=True)
    thread_id: int | None = Field(default=None, primary_key=True)
    last_read_at: datetime = Field(sa_column=Column(UtcDateTime, nullable=False))
```

- [ ] **Step 4: Register the models**

In `app/models/__init__.py`, add after the `from app.models.favorite import Favorites` block (keep alphabetical grouping style):

```python
# Forum models
from app.models.forum import ForumCategories, ForumPosts, ForumThreadReads, ForumThreads
```

and extend `__all__` (after `"Comments"`):

```python
    "ForumCategories",
    "ForumThreads",
    "ForumPosts",
    "ForumThreadReads",
```

- [ ] **Step 5: Add the permissions**

In `app/core/permissions.py`, inside `class Permission`, after the `REVIEW_CLOSE_EARLY` line add:

```python
    # Forum
    FORUM_ACCESS_STAFF = "forum_access_staff"
    FORUM_ACCESS_TAGGER = "forum_access_tagger"
    FORUM_MODERATE = "forum_moderate"
    FORUM_CATEGORY_MANAGE = "forum_category_manage"
```

In `_PERMISSION_DESCRIPTIONS`, after the `REVIEW_CLOSE_EARLY` entry add:

```python
    # Forum
    Permission.FORUM_ACCESS_STAFF: "Access staff-only forum categories",
    Permission.FORUM_ACCESS_TAGGER: "Access tagger forum categories",
    Permission.FORUM_MODERATE: "Pin, lock, move, delete, and restore forum threads and posts",
    Permission.FORUM_CATEGORY_MANAGE: "Create and edit forum categories",
```

After the `_PERMISSION_DESCRIPTIONS` dict (module level), add:

```python
# Permission titles allowed in forum_categories.view_perm / thread_create_perm /
# reply_perm. Only access-tier permissions belong here — moderation/management
# perms must not gate category access.
FORUM_ACCESS_PERMISSIONS: frozenset[str] = frozenset(
    {
        Permission.FORUM_ACCESS_STAFF.value,
        Permission.FORUM_ACCESS_TAGGER.value,
    }
)
```

- [ ] **Step 6: Author the migration**

Run: `uv run alembic revision -m "add_forum_tables"` (alembic sets `down_revision` to the current head automatically). Replace the generated file's `upgrade()`/`downgrade()` with:

```python
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# (keep the generated revision identifiers)

FORUM_PERMS = [
    ("forum_access_staff", "Access staff-only forum categories"),
    ("forum_access_tagger", "Access tagger forum categories"),
    ("forum_moderate", "Pin, lock, move, delete, and restore forum threads and posts"),
    ("forum_category_manage", "Create and edit forum categories"),
]

GROUP_GRANTS = {
    "forum_access_staff": ["Admins", "Mods"],
    "forum_access_tagger": ["Admins", "Mods", "Taggers"],
    "forum_moderate": ["Admins", "Mods"],
    "forum_category_manage": ["Admins"],
}


def upgrade() -> None:
    op.create_table(
        "forum_categories",
        sa.Column(
            "category_id", mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False
        ),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("view_perm", sa.String(64), nullable=True),
        sa.Column("thread_create_perm", sa.String(64), nullable=True),
        sa.Column("reply_perm", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("category_id"),
    )
    op.create_index("uq_forum_categories_title", "forum_categories", ["title"], unique=True)

    op.create_table(
        "forum_threads",
        sa.Column("thread_id", mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("category_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column(
            "date",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=False,
        ),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("post_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_post_at", sa.DateTime(), nullable=True),
        sa.Column("last_post_user_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.PrimaryKeyConstraint("thread_id"),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["forum_categories.category_id"],
            ondelete="RESTRICT",
            onupdate="CASCADE",
            name="fk_forum_threads_category_id",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_threads_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["last_post_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_forum_threads_last_post_user_id",
        ),
    )
    op.create_index(
        "idx_forum_threads_list", "forum_threads", ["category_id", "pinned", "last_post_at"]
    )
    op.create_index("fk_forum_threads_user_id", "forum_threads", ["user_id"])
    op.create_index(
        "fk_forum_threads_last_post_user_id", "forum_threads", ["last_post_user_id"]
    )
    op.create_index("ix_forum_threads_deleted", "forum_threads", ["deleted"])

    op.create_table(
        "forum_posts",
        sa.Column("post_id", mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("thread_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("post_text", sa.Text(), nullable=False),
        sa.Column(
            "date",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=False,
        ),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("update_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("last_updated", sa.DateTime(), nullable=True),
        sa.Column("last_updated_user_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.PrimaryKeyConstraint("post_id"),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["forum_threads.thread_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_posts_thread_id",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_posts_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["last_updated_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_forum_posts_last_updated_user_id",
        ),
    )
    op.create_index("idx_forum_posts_thread_date", "forum_posts", ["thread_id", "date"])
    op.create_index("fk_forum_posts_user_id", "forum_posts", ["user_id"])
    op.create_index(
        "fk_forum_posts_last_updated_user_id", "forum_posts", ["last_updated_user_id"]
    )
    op.create_index("ix_forum_posts_deleted", "forum_posts", ["deleted"])

    op.create_table(
        "forum_thread_reads",
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("thread_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("last_read_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "thread_id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_thread_reads_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["forum_threads.thread_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_thread_reads_thread_id",
        ),
    )
    op.create_index("fk_forum_thread_reads_thread_id", "forum_thread_reads", ["thread_id"])

    # Seed permissions (idempotent vs sync_permissions) and grant to groups.
    # Same pattern as c0cb8f931041_add_news_permissions.py.
    for title, desc in FORUM_PERMS:
        op.execute(
            f"INSERT INTO perms (title, `desc`) "
            f"SELECT '{title}', '{desc}' FROM DUAL "
            f"WHERE NOT EXISTS (SELECT 1 FROM perms WHERE title = '{title}')"
        )

    for title, groups in GROUP_GRANTS.items():
        group_list = ", ".join(f"'{g}'" for g in groups)
        op.execute(f"""
            INSERT IGNORE INTO group_perms (group_id, perm_id, permvalue)
            SELECT g.group_id, (SELECT MIN(perm_id) FROM perms WHERE title = '{title}'), 1
            FROM `groups` g
            WHERE g.title IN ({group_list})
        """)


def downgrade() -> None:
    for title, _ in FORUM_PERMS:
        op.execute(f"""
            DELETE gp FROM group_perms gp
            JOIN perms p ON gp.perm_id = p.perm_id
            WHERE p.title = '{title}'
        """)
        op.execute(f"""
            DELETE up FROM user_perms up
            JOIN perms p ON up.perm_id = p.perm_id
            WHERE p.title = '{title}'
        """)
        op.execute(f"DELETE FROM perms WHERE title = '{title}'")

    op.drop_table("forum_thread_reads")
    op.drop_table("forum_posts")
    op.drop_table("forum_threads")
    op.drop_table("forum_categories")
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `uv run pytest tests/api/v1/test_forum_models.py -v`
Expected: PASS (2 tests). The conftest session setup detects the new alembic head and rebuilds the test DB through the full migration chain — if the migration is broken, THIS is where it surfaces; read the error, fix the migration, rerun.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check app tests && uv run mypy app
git add app/models/forum.py app/models/__init__.py app/core/permissions.py alembic/versions/*add_forum_tables.py tests/api/v1/test_forum_models.py
git commit -m "feat(forum): add forum tables, permissions, and migration"
```

---

### Task 2: Move `build_user_summaries` into `app/core/user_loader.py`

The forum routes need batch `UserSummary` construction; `app/api/v1/admin.py` already has a private `_build_user_summaries`. Move it to `user_loader` so forum doesn't create a third copy (privmsgs' `get_user_groups_map` is a different shape — leave it).

**Files:**
- Modify: `app/core/user_loader.py`
- Modify: `app/api/v1/admin.py` (delete `_build_user_summaries` at ~line 117; update its call sites at ~lines 899, 990, 1135 — grep for `_build_user_summaries`)
- Test: existing admin tests

**Interfaces:**
- Produces: `async def build_user_summaries(db: AsyncSession, user_ids: set[int]) -> dict[int, UserSummary]` in `app.core.user_loader`.

- [ ] **Step 1: Add the function to `app/core/user_loader.py`**

Append (with the new imports merged into the header):

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import UserSummary


async def build_user_summaries(db: AsyncSession, user_ids: set[int]) -> dict[int, UserSummary]:
    """Batch-fetch Users with groups and build UserSummary objects by user_id."""
    if not user_ids:
        return {}
    result = await db.execute(
        select(Users).options(*USER_WITH_GROUPS_OPTIONS).where(Users.user_id.in_(user_ids))  # type: ignore[union-attr]
    )
    users = result.scalars().all()
    return {
        user.id: UserSummary(
            user_id=user.id,
            username=user.username,
            avatar=user.avatar,
            avatar_in_r2=user.avatar_in_r2,
            user_title=user.user_title,
            groups=user.groups,
        )
        for user in users
    }
```

- [ ] **Step 2: Replace admin.py's copy**

Delete `_build_user_summaries` from `app/api/v1/admin.py`, add `from app.core.user_loader import build_user_summaries` to its imports, and rename the three call sites from `_build_user_summaries(` to `build_user_summaries(`.

- [ ] **Step 3: Run the admin tests to verify no regression**

Run: `uv run pytest tests/api/v1/test_admin_actions.py tests/api/v1/test_reports.py tests/api/v1/test_reviews.py -v`
Expected: PASS (same results as before the change — these suites exercise the moved helper through the report/review endpoints).

- [ ] **Step 4: Lint and commit**

```bash
uv run ruff check app && uv run mypy app
git add app/core/user_loader.py app/api/v1/admin.py
git commit -m "refactor: move build_user_summaries to user_loader for reuse"
```

---

### Task 3: Schemas

**Files:**
- Create: `app/schemas/forum.py`
- Test: `tests/schemas/test_forum.py`

**Interfaces:**
- Consumes: `FORUM_ACCESS_PERMISSIONS` (Task 1), `UserSummary`, `parse_markdown`, `UTCDatetime`/`UTCDatetimeOptional` from `app.schemas.base`.
- Produces: `ForumCategoryCreate`, `ForumCategoryUpdate`, `ForumCategoryResponse`, `ForumCategoryListResponse`, `ForumThreadCreate`, `ForumThreadUpdate`, `ForumThreadSummary`, `ForumThreadListResponse`, `ForumThreadDetailResponse`, `ForumPostCreate`, `ForumPostUpdate`, `ForumPostResponse` — exact fields below.

- [ ] **Step 1: Write the failing test**

Create `tests/schemas/test_forum.py`:

```python
"""Forum schema validation tests."""

import pytest
from pydantic import ValidationError

from app.schemas.forum import (
    ForumCategoryCreate,
    ForumCategoryUpdate,
    ForumPostCreate,
    ForumThreadCreate,
)


class TestForumCategoryPermWhitelist:
    def test_valid_access_perm_accepted(self):
        cat = ForumCategoryCreate(title="Mod Board", view_perm="forum_access_staff")
        assert cat.view_perm == "forum_access_staff"

    def test_null_perm_accepted(self):
        cat = ForumCategoryCreate(title="Public")
        assert cat.view_perm is None

    @pytest.mark.parametrize("field", ["view_perm", "thread_create_perm", "reply_perm"])
    def test_non_access_perm_rejected(self, field):
        with pytest.raises(ValidationError):
            ForumCategoryCreate(**{"title": "Bad", field: "user_ban"})

    def test_update_schema_rejects_non_access_perm(self):
        with pytest.raises(ValidationError):
            ForumCategoryUpdate(reply_perm="forum_moderate")


class TestForumTextValidation:
    def test_thread_title_and_text_stripped(self):
        t = ForumThreadCreate(title="  Hello  ", post_text="  body  ")
        assert t.title == "Hello"
        assert t.post_text == "body"

    def test_empty_post_text_rejected(self):
        with pytest.raises(ValidationError):
            ForumPostCreate(post_text="")

    def test_title_max_length(self):
        with pytest.raises(ValidationError):
            ForumThreadCreate(title="x" * 256, post_text="body")


class TestForumPostHtml:
    def test_post_text_html_renders_markdown_and_quote(self):
        from app.schemas.common import UserSummary
        from app.schemas.forum import ForumPostResponse

        post = ForumPostResponse(
            post_id=1,
            thread_id=1,
            user_id=1,
            post_text='**bold** [quote="alice"]hi[/quote]',
            date="2026-07-06T00:00:00Z",
            deleted=False,
            update_count=0,
            user=UserSummary(user_id=1, username="testuser"),
        )
        assert "<strong>bold</strong>" in post.post_text_html
        assert "blockquote" in post.post_text_html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/schemas/test_forum.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.schemas.forum'`

- [ ] **Step 3: Create the schemas**

Create `app/schemas/forum.py`:

```python
"""
Pydantic schemas for Forum endpoints
"""

from pydantic import BaseModel, Field, computed_field, field_validator

from app.core.permissions import FORUM_ACCESS_PERMISSIONS
from app.schemas.base import UTCDatetime, UTCDatetimeOptional
from app.schemas.common import UserSummary
from app.utils.markdown import parse_markdown


def _validate_access_perm(v: str | None) -> str | None:
    """Restrict category gate columns to the FORUM_ACCESS_* tier permissions."""
    if v is not None and v not in FORUM_ACCESS_PERMISSIONS:
        raise ValueError(
            f"must be one of: {', '.join(sorted(FORUM_ACCESS_PERMISSIONS))} (or null)"
        )
    return v


# ===== Categories =====


class ForumCategoryCreate(BaseModel):
    """Schema for creating a category (FORUM_CATEGORY_MANAGE)."""

    title: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int = 0
    view_perm: str | None = None
    thread_create_perm: str | None = None
    reply_perm: str | None = None

    _check_perms = field_validator("view_perm", "thread_create_perm", "reply_perm")(
        _validate_access_perm
    )

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        return v.strip()


class ForumCategoryUpdate(BaseModel):
    """Schema for updating a category; only provided fields change (exclude_unset)."""

    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int | None = None
    view_perm: str | None = None
    thread_create_perm: str | None = None
    reply_perm: str | None = None

    _check_perms = field_validator("view_perm", "thread_create_perm", "reply_perm")(
        _validate_access_perm
    )


class ForumCategoryResponse(BaseModel):
    """Category with stats and caller capabilities."""

    category_id: int
    title: str
    description: str | None = None
    sort_order: int
    view_perm: str | None = None
    thread_create_perm: str | None = None
    reply_perm: str | None = None
    thread_count: int = 0
    post_count: int = 0
    last_post_at: UTCDatetimeOptional = None
    last_thread_id: int | None = None
    last_thread_title: str | None = None
    last_post_user: UserSummary | None = None
    can_create_thread: bool = False
    can_reply: bool = False


class ForumCategoryListResponse(BaseModel):
    categories: list[ForumCategoryResponse]


# ===== Threads =====


class ForumThreadCreate(BaseModel):
    """Create a thread with its opening post in one call."""

    title: str = Field(min_length=1, max_length=255)
    post_text: str = Field(min_length=1)

    @field_validator("title", "post_text")
    @classmethod
    def strip_text(cls, v: str) -> str:
        return v.strip()


class ForumThreadUpdate(BaseModel):
    """Partial thread update. title: author or FORUM_MODERATE;
    pinned/locked/category_id/deleted: FORUM_MODERATE only."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    pinned: bool | None = None
    locked: bool | None = None
    category_id: int | None = None
    deleted: bool | None = None


class ForumThreadSummary(BaseModel):
    """Thread row for lists and as the meta block of the detail view."""

    thread_id: int
    category_id: int
    title: str
    user: UserSummary
    date: UTCDatetime
    pinned: bool
    locked: bool
    deleted: bool
    post_count: int
    last_post_at: UTCDatetimeOptional = None
    last_post_user: UserSummary | None = None
    unread: bool = False


class ForumThreadListResponse(BaseModel):
    total: int
    page: int
    per_page: int
    threads: list[ForumThreadSummary]


# ===== Posts =====


class ForumPostCreate(BaseModel):
    post_text: str = Field(min_length=1, description="Post text (markdown supported)")

    @field_validator("post_text")
    @classmethod
    def strip_text(cls, v: str) -> str:
        return v.strip()


class ForumPostUpdate(BaseModel):
    """post_text: owner or FORUM_MODERATE; deleted: FORUM_MODERATE only."""

    post_text: str | None = Field(default=None, min_length=1)
    deleted: bool | None = None

    @field_validator("post_text")
    @classmethod
    def strip_text(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None


class ForumPostResponse(BaseModel):
    """Post as returned by the API. Tombstoned posts (deleted=True) have
    post_text blanked by the route for callers without FORUM_MODERATE."""

    post_id: int
    thread_id: int
    user_id: int
    post_text: str
    date: UTCDatetime
    deleted: bool
    update_count: int
    last_updated: UTCDatetimeOptional = None
    last_updated_user_id: int | None = None
    user: UserSummary

    @computed_field  # type: ignore[prop-decorator]
    @property
    def post_text_html(self) -> str:
        """Rendered HTML from markdown post_text"""
        return parse_markdown(self.post_text)


class ForumThreadDetailResponse(BaseModel):
    """Thread meta + one page of posts."""

    thread: ForumThreadSummary
    can_reply: bool = False
    can_moderate: bool = False
    total: int
    page: int
    per_page: int
    posts: list[ForumPostResponse]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/schemas/test_forum.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app tests && uv run mypy app
git add app/schemas/forum.py tests/schemas/test_forum.py
git commit -m "feat(forum): request/response schemas with access-perm whitelist"
```

---

### Task 4: Service helpers

**Files:**
- Create: `app/services/forum.py`
- Test: `tests/services/test_forum.py`

**Interfaces:**
- Consumes: `ForumPosts`, `ForumThreads`, `ForumThreadReads` (Task 1).
- Produces:
  - `def can_access(user_perms: set[str], required_perm: str | None) -> bool`
  - `async def recompute_thread_stats(db: AsyncSession, thread: ForumThreads) -> None` (mutates the thread object; caller holds the row lock and commits)
  - `async def upsert_thread_read(db: AsyncSession, user_id: int, thread_id: int, read_at: datetime) -> None` (caller commits)

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_forum.py`:

```python
"""Forum service helper tests."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forum import ForumCategories, ForumPosts, ForumThreadReads, ForumThreads
from app.services.forum import can_access, recompute_thread_stats, upsert_thread_read


class TestCanAccess:
    def test_null_perm_is_open(self):
        assert can_access(set(), None) is True

    def test_missing_perm_denied(self):
        assert can_access(set(), "forum_access_staff") is False

    def test_present_perm_allowed(self):
        assert can_access({"forum_access_staff"}, "forum_access_staff") is True


async def _make_thread_with_posts(db_session: AsyncSession) -> ForumThreads:
    cat = ForumCategories(title="Svc Category")
    db_session.add(cat)
    await db_session.flush()
    thread = ForumThreads(category_id=cat.category_id, title="t", user_id=1)
    db_session.add(thread)
    await db_session.flush()
    for uid in (1, 2, 3):
        db_session.add(ForumPosts(thread_id=thread.thread_id, user_id=uid, post_text=f"p{uid}"))
    await db_session.flush()
    return thread


class TestRecomputeThreadStats:
    async def test_counts_live_posts(self, db_session: AsyncSession):
        thread = await _make_thread_with_posts(db_session)
        await recompute_thread_stats(db_session, thread)
        assert thread.post_count == 3
        assert thread.last_post_user_id == 3  # newest post
        assert thread.last_post_at is not None

    async def test_ignores_soft_deleted_posts(self, db_session: AsyncSession):
        thread = await _make_thread_with_posts(db_session)
        # Soft-delete the newest post (user 3's)
        from sqlalchemy import select

        newest = (
            await db_session.execute(
                select(ForumPosts)
                .where(ForumPosts.thread_id == thread.thread_id)
                .order_by(ForumPosts.post_id.desc())
            )
        ).scalars().first()
        newest.deleted = True
        await db_session.flush()

        await recompute_thread_stats(db_session, thread)
        assert thread.post_count == 2
        assert thread.last_post_user_id == 2


class TestUpsertThreadRead:
    async def test_insert_then_update(self, db_session: AsyncSession):
        thread = await _make_thread_with_posts(db_session)
        t1 = datetime(2026, 7, 1, tzinfo=UTC)
        t2 = datetime(2026, 7, 2, tzinfo=UTC)

        await upsert_thread_read(db_session, 1, thread.thread_id, t1)
        await db_session.commit()
        row = await db_session.get(ForumThreadReads, (1, thread.thread_id))
        assert row is not None

        await upsert_thread_read(db_session, 1, thread.thread_id, t2)
        await db_session.commit()
        db_session.expire_all()
        row = await db_session.get(ForumThreadReads, (1, thread.thread_id))
        assert row.last_read_at.day == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/services/test_forum.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.forum'`

- [ ] **Step 3: Create the service**

Create `app/services/forum.py`:

```python
"""
Forum helpers: category access checks, denormalized thread stats, read tracking.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forum import ForumPosts, ForumThreadReads, ForumThreads


def can_access(user_perms: set[str], required_perm: str | None) -> bool:
    """True when a category action gated by required_perm is allowed.

    required_perm None means ungated (authentication requirements are
    enforced separately by the route).
    """
    return required_perm is None or required_perm in user_perms


async def recompute_thread_stats(db: AsyncSession, thread: ForumThreads) -> None:
    """Recompute post_count/last_post_at/last_post_user_id from live posts.

    Always recompute — never increment — so the counters cannot drift.
    Caller must hold the thread row lock (SELECT ... FOR UPDATE) when other
    writers may race, and is responsible for committing.
    """
    result = await db.execute(
        select(func.count(), func.max(ForumPosts.post_id))
        .where(ForumPosts.thread_id == thread.thread_id)
        .where(ForumPosts.deleted == False)  # noqa: E712
    )
    count, last_post_id = result.one()
    thread.post_count = count or 0
    if last_post_id is None:
        thread.last_post_at = None
        thread.last_post_user_id = None
    else:
        last_post = await db.get(ForumPosts, last_post_id)
        assert last_post is not None
        thread.last_post_at = last_post.date
        thread.last_post_user_id = last_post.user_id


async def upsert_thread_read(
    db: AsyncSession, user_id: int, thread_id: int, read_at: datetime
) -> None:
    """Record that user has seen the thread as of read_at. Caller commits."""
    stmt = mysql_insert(ForumThreadReads).values(
        user_id=user_id, thread_id=thread_id, last_read_at=read_at
    )
    stmt = stmt.on_duplicate_key_update(last_read_at=stmt.inserted.last_read_at)
    await db.execute(stmt)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/services/test_forum.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app tests && uv run mypy app
git add app/services/forum.py tests/services/test_forum.py
git commit -m "feat(forum): access check, thread stat recompute, read upsert helpers"
```

---

### Task 5: Shared test fixtures + Categories endpoints

**Files:**
- Create: `tests/api/v1/conftest.py`
- Create: `app/api/v1/forum.py` (categories section + shared helpers)
- Modify: `app/api/v1/__init__.py`
- Test: `tests/api/v1/test_forum_categories.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces (routes): `GET/POST /api/v1/forum/categories`, `PATCH/DELETE /api/v1/forum/categories/{category_id}`.
- Produces (route-file helpers used by Tasks 6–7): `_effective_perms(db, redis_client, user) -> set[str]`, `_visible_category(db, category_id, perms) -> ForumCategories` (raises 404), `_thread_summary(thread, summaries, unread) -> ForumThreadSummary`, `_post_response(post, user_summary, is_moderator) -> ForumPostResponse`, `_first_post_id(db, thread_id) -> int | None`, `DbDep`, `RedisDep`.
- Produces (test helpers in `tests/api/v1/conftest.py`): `grant_permission(db_session, user_id, permission) -> token`, `activate_user(db_session, user_id) -> token`, `make_thread(db_session, category, user_id=1, title=..., post_text=...) -> ForumThreads`, fixtures `public_category`, `announce_category`, `staff_category`, `tagger_category`, `public_thread`, `user_token` (user 3, no perms), `author_token` (user 1), `staff_token` (user 2: both tiers + FORUM_MODERATE), `category_manager_token` (user 2: FORUM_CATEGORY_MANAGE), `tagger_token` (new user 4: FORUM_ACCESS_TAGGER only).

- [ ] **Step 1: Create the shared forum test fixtures**

Create `tests/api/v1/conftest.py`:

```python
"""Shared fixtures for forum API tests.

Test personas (users 1-3 are pre-seeded by the root conftest):
- user 1 "testuser": content author
- user 2 "testuser2": privileged (grants stacked per fixture)
- user 3 "testuser3": plain authenticated user, no forum permissions
- user 4 "testtagger": FORUM_ACCESS_TAGGER only (created here)
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.core.security import create_access_token
from app.models.forum import ForumCategories, ForumPosts, ForumThreads
from app.models.permissions import Perms, UserPerms
from app.models.user import Users


async def grant_permission(
    db_session: AsyncSession, user_id: int, permission: Permission
) -> str:
    """Grant a permission to a user via user_perms and return a bearer token.

    Looks up the Perms row by title first — sync_permissions() has already
    seeded all enum permissions at session setup.
    """
    user = await db_session.get(Users, user_id)
    user.active = 1
    result = await db_session.execute(select(Perms).where(Perms.title == permission.value))
    perm = result.scalars().first()
    if perm is None:
        perm = Perms(title=permission.value, desc=permission.description)
        db_session.add(perm)
        await db_session.flush()
    db_session.add(UserPerms(user_id=user_id, perm_id=perm.perm_id, permvalue=1))
    await db_session.commit()
    return create_access_token(user_id)


async def activate_user(db_session: AsyncSession, user_id: int) -> str:
    """Mark a pre-seeded user active and return a bearer token."""
    user = await db_session.get(Users, user_id)
    user.active = 1
    await db_session.commit()
    return create_access_token(user_id)


async def make_thread(
    db_session: AsyncSession,
    category: ForumCategories,
    user_id: int = 1,
    title: str = "Test thread",
    post_text: str = "Opening post",
) -> ForumThreads:
    """Create a thread + opening post with correct denormalized fields."""
    thread = ForumThreads(category_id=category.category_id, title=title, user_id=user_id)
    db_session.add(thread)
    await db_session.flush()
    post = ForumPosts(thread_id=thread.thread_id, user_id=user_id, post_text=post_text)
    db_session.add(post)
    await db_session.flush()
    await db_session.refresh(post)
    thread.post_count = 1
    thread.last_post_at = post.date
    thread.last_post_user_id = user_id
    await db_session.commit()
    await db_session.refresh(thread)
    return thread


@pytest.fixture
async def public_category(db_session: AsyncSession) -> ForumCategories:
    cat = ForumCategories(title="Site Discussion", description="General site talk", sort_order=1)
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


@pytest.fixture
async def announce_category(db_session: AsyncSession) -> ForumCategories:
    """Public view/reply, staff-only thread creation."""
    cat = ForumCategories(
        title="Announcements",
        sort_order=0,
        thread_create_perm=Permission.FORUM_ACCESS_STAFF.value,
    )
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


@pytest.fixture
async def staff_category(db_session: AsyncSession) -> ForumCategories:
    """Fully staff-gated."""
    cat = ForumCategories(
        title="Mod Board",
        sort_order=2,
        view_perm=Permission.FORUM_ACCESS_STAFF.value,
        thread_create_perm=Permission.FORUM_ACCESS_STAFF.value,
        reply_perm=Permission.FORUM_ACCESS_STAFF.value,
    )
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


@pytest.fixture
async def tagger_category(db_session: AsyncSession) -> ForumCategories:
    """Fully tagger-gated (staff hold FORUM_ACCESS_TAGGER too via grants)."""
    cat = ForumCategories(
        title="Tagger Board",
        sort_order=3,
        view_perm=Permission.FORUM_ACCESS_TAGGER.value,
        thread_create_perm=Permission.FORUM_ACCESS_TAGGER.value,
        reply_perm=Permission.FORUM_ACCESS_TAGGER.value,
    )
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


@pytest.fixture
async def public_thread(db_session: AsyncSession, public_category: ForumCategories) -> ForumThreads:
    return await make_thread(db_session, public_category)


@pytest.fixture
async def user_token(db_session: AsyncSession) -> str:
    """Plain authenticated user (user 3), no forum permissions."""
    return await activate_user(db_session, 3)


@pytest.fixture
async def author_token(db_session: AsyncSession) -> str:
    """Token for user 1, the default content author."""
    return await activate_user(db_session, 1)


@pytest.fixture
async def staff_token(db_session: AsyncSession) -> str:
    """User 2 with both access tiers + FORUM_MODERATE."""
    await grant_permission(db_session, 2, Permission.FORUM_ACCESS_STAFF)
    await grant_permission(db_session, 2, Permission.FORUM_ACCESS_TAGGER)
    return await grant_permission(db_session, 2, Permission.FORUM_MODERATE)


@pytest.fixture
async def category_manager_token(db_session: AsyncSession) -> str:
    """User 2 with FORUM_CATEGORY_MANAGE."""
    return await grant_permission(db_session, 2, Permission.FORUM_CATEGORY_MANAGE)


@pytest.fixture
async def tagger_token(db_session: AsyncSession) -> str:
    """User 4 with FORUM_ACCESS_TAGGER only."""
    user = Users(
        user_id=4,
        username="testtagger",
        password="testpassword",
        password_type="bcrypt",
        salt="testsalt00000004",
        email="tagger@example.com",
        active=1,
    )
    db_session.add(user)
    await db_session.commit()
    return await grant_permission(db_session, 4, Permission.FORUM_ACCESS_TAGGER)
```

- [ ] **Step 2: Write the failing category tests**

Create `tests/api/v1/test_forum_categories.py`:

```python
"""Tests for forum category endpoints."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.api.v1.conftest import make_thread


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestListCategories:
    """GET /api/v1/forum/categories"""

    async def test_anon_sees_public_not_gated(
        self, client: AsyncClient, public_category, staff_category
    ):
        response = await client.get("/api/v1/forum/categories")
        assert response.status_code == 200
        titles = [c["title"] for c in response.json()["categories"]]
        assert "Site Discussion" in titles
        assert "Mod Board" not in titles

    async def test_plain_user_does_not_see_gated(
        self, client: AsyncClient, staff_category, tagger_category, user_token
    ):
        response = await client.get("/api/v1/forum/categories", headers=_auth(user_token))
        titles = [c["title"] for c in response.json()["categories"]]
        assert titles == []

    async def test_tagger_sees_tagger_not_staff(
        self, client: AsyncClient, staff_category, tagger_category, tagger_token
    ):
        response = await client.get("/api/v1/forum/categories", headers=_auth(tagger_token))
        titles = [c["title"] for c in response.json()["categories"]]
        assert "Tagger Board" in titles
        assert "Mod Board" not in titles

    async def test_staff_sees_everything(
        self, client: AsyncClient, public_category, staff_category, tagger_category, staff_token
    ):
        response = await client.get("/api/v1/forum/categories", headers=_auth(staff_token))
        titles = [c["title"] for c in response.json()["categories"]]
        assert set(titles) == {"Site Discussion", "Mod Board", "Tagger Board"}

    async def test_ordered_by_sort_order(
        self, client: AsyncClient, public_category, announce_category
    ):
        response = await client.get("/api/v1/forum/categories")
        titles = [c["title"] for c in response.json()["categories"]]
        assert titles == ["Announcements", "Site Discussion"]  # sort_order 0 before 1

    async def test_stats_and_last_post(
        self, client: AsyncClient, db_session: AsyncSession, public_category
    ):
        thread = await make_thread(db_session, public_category, title="Latest thread")
        response = await client.get("/api/v1/forum/categories")
        cat = response.json()["categories"][0]
        assert cat["thread_count"] == 1
        assert cat["post_count"] == 1
        assert cat["last_thread_id"] == thread.thread_id
        assert cat["last_thread_title"] == "Latest thread"
        assert cat["last_post_user"]["username"] == "testuser"

    async def test_capabilities(
        self, client: AsyncClient, announce_category, user_token, staff_token
    ):
        # Anon: no capabilities anywhere
        anon = (await client.get("/api/v1/forum/categories")).json()["categories"][0]
        assert anon["can_create_thread"] is False
        assert anon["can_reply"] is False
        # Plain user on Announcements: reply yes, create no
        plain = (
            await client.get("/api/v1/forum/categories", headers=_auth(user_token))
        ).json()["categories"][0]
        assert plain["can_create_thread"] is False
        assert plain["can_reply"] is True
        # Staff: both
        staff = (
            await client.get("/api/v1/forum/categories", headers=_auth(staff_token))
        ).json()["categories"][0]
        assert staff["can_create_thread"] is True
        assert staff["can_reply"] is True


class TestCreateCategory:
    """POST /api/v1/forum/categories"""

    async def test_requires_auth(self, client: AsyncClient):
        response = await client.post("/api/v1/forum/categories", json={"title": "New"})
        assert response.status_code == 401

    async def test_requires_permission(self, client: AsyncClient, user_token):
        response = await client.post(
            "/api/v1/forum/categories", json={"title": "New"}, headers=_auth(user_token)
        )
        assert response.status_code == 403

    async def test_create_success(self, client: AsyncClient, category_manager_token):
        response = await client.post(
            "/api/v1/forum/categories",
            json={
                "title": "Feature Requests",
                "description": "Ask for features",
                "sort_order": 5,
                "thread_create_perm": None,
                "view_perm": None,
                "reply_perm": None,
            },
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Feature Requests"
        assert data["sort_order"] == 5
        assert data["view_perm"] is None

    async def test_invalid_perm_rejected(self, client: AsyncClient, category_manager_token):
        response = await client.post(
            "/api/v1/forum/categories",
            json={"title": "Bad", "view_perm": "user_ban"},
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 422

    async def test_duplicate_title_conflict(
        self, client: AsyncClient, public_category, category_manager_token
    ):
        response = await client.post(
            "/api/v1/forum/categories",
            json={"title": "Site Discussion"},
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 409


class TestUpdateCategory:
    """PATCH /api/v1/forum/categories/{category_id}"""

    async def test_requires_permission(self, client: AsyncClient, public_category, user_token):
        response = await client.patch(
            f"/api/v1/forum/categories/{public_category.category_id}",
            json={"title": "Renamed"},
            headers=_auth(user_token),
        )
        assert response.status_code == 403

    async def test_update_fields(
        self, client: AsyncClient, public_category, category_manager_token
    ):
        response = await client.patch(
            f"/api/v1/forum/categories/{public_category.category_id}",
            json={"title": "Renamed", "view_perm": "forum_access_staff"},
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Renamed"
        assert data["view_perm"] == "forum_access_staff"
        assert data["description"] == "General site talk"  # unchanged

    async def test_not_found(self, client: AsyncClient, category_manager_token):
        response = await client.patch(
            "/api/v1/forum/categories/99999",
            json={"title": "X"},
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 404

    async def test_duplicate_title_conflict(
        self, client: AsyncClient, public_category, announce_category, category_manager_token
    ):
        response = await client.patch(
            f"/api/v1/forum/categories/{public_category.category_id}",
            json={"title": "Announcements"},
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 409


class TestDeleteCategory:
    """DELETE /api/v1/forum/categories/{category_id}"""

    async def test_delete_empty_category(
        self, client: AsyncClient, public_category, category_manager_token
    ):
        response = await client.delete(
            f"/api/v1/forum/categories/{public_category.category_id}",
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 204

    async def test_delete_nonempty_conflict(
        self, client: AsyncClient, db_session, public_category, category_manager_token
    ):
        thread = await make_thread(db_session, public_category)
        # Even a soft-deleted thread blocks deletion (FK RESTRICT)
        thread.deleted = True
        await db_session.commit()
        response = await client.delete(
            f"/api/v1/forum/categories/{public_category.category_id}",
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 409

    async def test_requires_permission(self, client: AsyncClient, public_category, user_token):
        response = await client.delete(
            f"/api/v1/forum/categories/{public_category.category_id}",
            headers=_auth(user_token),
        )
        assert response.status_code == 403
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_forum_categories.py -v`
Expected: FAIL — all tests 404 (`/api/v1/forum/*` routes don't exist yet)

- [ ] **Step 4: Create the router with helpers + category endpoints**

Create `app/api/v1/forum.py`:

```python
"""Forum API endpoints: categories, threads, posts."""

from datetime import UTC, datetime
from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams
from app.core.auth import CurrentUser, OptionalCurrentUser
from app.core.database import get_db
from app.core.permission_cache import get_cached_user_permissions
from app.core.permission_deps import require_permission
from app.core.permissions import Permission
from app.core.redis import get_redis
from app.core.user_loader import build_user_summaries
from app.models.forum import ForumCategories, ForumPosts, ForumThreadReads, ForumThreads
from app.models.user import Users
from app.schemas.common import UserSummary
from app.schemas.forum import (
    ForumCategoryCreate,
    ForumCategoryListResponse,
    ForumCategoryResponse,
    ForumCategoryUpdate,
    ForumPostCreate,
    ForumPostResponse,
    ForumPostUpdate,
    ForumThreadCreate,
    ForumThreadDetailResponse,
    ForumThreadListResponse,
    ForumThreadSummary,
    ForumThreadUpdate,
)
from app.services.forum import can_access, recompute_thread_stats, upsert_thread_read

router = APIRouter(prefix="/forum", tags=["forum"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[redis.Redis, Depends(get_redis)]  # type: ignore[type-arg]


# ===== Shared helpers =====


async def _effective_perms(
    db: AsyncSession,
    redis_client: redis.Redis,  # type: ignore[type-arg]
    user: Users | None,
) -> set[str]:
    """Resolve the caller's permission set; empty for anonymous callers."""
    if user is None or user.user_id is None:
        return set()
    return await get_cached_user_permissions(db, redis_client, user.user_id)


async def _visible_category(
    db: AsyncSession, category_id: int, perms: set[str]
) -> ForumCategories:
    """Load a category the caller may view; 404 (not 403) otherwise so gated
    categories don't leak existence."""
    category = await db.get(ForumCategories, category_id)
    if category is None or not can_access(perms, category.view_perm):
        raise HTTPException(status_code=404, detail="Category not found")
    return category


async def _first_post_id(db: AsyncSession, thread_id: int) -> int | None:
    """post_id of a thread's opening post (min post_id; opening posts can't
    be deleted alone, so this is stable)."""
    result = await db.execute(
        select(func.min(ForumPosts.post_id)).where(ForumPosts.thread_id == thread_id)
    )
    return result.scalar()


def _thread_summary(
    thread: ForumThreads, summaries: dict[int, UserSummary], unread: bool
) -> ForumThreadSummary:
    return ForumThreadSummary(
        thread_id=thread.thread_id,
        category_id=thread.category_id,
        title=thread.title,
        user=summaries[thread.user_id],
        date=thread.date,
        pinned=thread.pinned,
        locked=thread.locked,
        deleted=thread.deleted,
        post_count=thread.post_count,
        last_post_at=thread.last_post_at,
        last_post_user=(
            summaries.get(thread.last_post_user_id) if thread.last_post_user_id else None
        ),
        unread=unread,
    )


def _post_response(
    post: ForumPosts, user: UserSummary, is_moderator: bool
) -> ForumPostResponse:
    """Build a post response; tombstoned posts have their text blanked for
    callers without FORUM_MODERATE."""
    return ForumPostResponse(
        post_id=post.post_id,
        thread_id=post.thread_id,
        user_id=post.user_id,
        post_text="" if post.deleted and not is_moderator else post.post_text,
        date=post.date,
        deleted=post.deleted,
        update_count=post.update_count,
        last_updated=post.last_updated,
        last_updated_user_id=post.last_updated_user_id,
        user=user,
    )


def _category_response(
    category: ForumCategories,
    *,
    thread_count: int = 0,
    post_count: int = 0,
    last_post_at: datetime | None = None,
    last_thread_id: int | None = None,
    last_thread_title: str | None = None,
    last_post_user: UserSummary | None = None,
    can_create_thread: bool = False,
    can_reply: bool = False,
) -> ForumCategoryResponse:
    return ForumCategoryResponse(
        category_id=category.category_id,
        title=category.title,
        description=category.description,
        sort_order=category.sort_order,
        view_perm=category.view_perm,
        thread_create_perm=category.thread_create_perm,
        reply_perm=category.reply_perm,
        thread_count=thread_count,
        post_count=post_count,
        last_post_at=last_post_at,
        last_thread_id=last_thread_id,
        last_thread_title=last_thread_title,
        last_post_user=last_post_user,
        can_create_thread=can_create_thread,
        can_reply=can_reply,
    )


async def _check_duplicate_title(
    db: AsyncSession, title: str, exclude_category_id: int | None = None
) -> None:
    query = select(ForumCategories).where(ForumCategories.title == title)
    if exclude_category_id is not None:
        query = query.where(ForumCategories.category_id != exclude_category_id)
    existing = (await db.execute(query)).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="A category with this title already exists"
        )


# ===== Categories =====


@router.get("/categories", response_model=ForumCategoryListResponse)
async def list_categories(
    db: DbDep,
    redis_client: RedisDep,
    current_user: OptionalCurrentUser,
) -> ForumCategoryListResponse:
    """List categories the caller may view, with stats and capabilities."""
    perms = await _effective_perms(db, redis_client, current_user)
    result = await db.execute(
        select(ForumCategories).order_by(
            ForumCategories.sort_order, ForumCategories.category_id  # type: ignore[arg-type]
        )
    )
    categories = [c for c in result.scalars().all() if can_access(perms, c.view_perm)]

    # Thread/post counts per category (live threads only)
    stats_rows = await db.execute(
        select(
            ForumThreads.category_id,
            func.count(),
            func.coalesce(func.sum(ForumThreads.post_count), 0),
        )
        .where(ForumThreads.deleted == False)  # noqa: E712
        .group_by(ForumThreads.category_id)
    )
    stats = {cid: (threads, int(posts)) for cid, threads, posts in stats_rows.all()}

    # Latest-activity thread per category
    rn = (
        func.row_number()
        .over(
            partition_by=ForumThreads.category_id,
            order_by=(
                ForumThreads.last_post_at.desc(),  # type: ignore[union-attr]
                ForumThreads.thread_id.desc(),  # type: ignore[union-attr]
            ),
        )
        .label("rn")
    )
    latest_sq = (
        select(
            ForumThreads.category_id,
            ForumThreads.thread_id,
            ForumThreads.title,
            ForumThreads.last_post_at,
            ForumThreads.last_post_user_id,
            rn,
        )
        .where(ForumThreads.deleted == False)  # noqa: E712
        .subquery()
    )
    latest_rows = (await db.execute(select(latest_sq).where(latest_sq.c.rn == 1))).all()
    latest = {row.category_id: row for row in latest_rows}

    user_ids = {row.last_post_user_id for row in latest_rows if row.last_post_user_id}
    summaries = await build_user_summaries(db, user_ids)

    authed = current_user is not None
    entries = []
    for c in categories:
        thread_count, post_count = stats.get(c.category_id, (0, 0))
        last = latest.get(c.category_id)
        entries.append(
            _category_response(
                c,
                thread_count=thread_count,
                post_count=post_count,
                last_post_at=last.last_post_at if last else None,
                last_thread_id=last.thread_id if last else None,
                last_thread_title=last.title if last else None,
                last_post_user=(
                    summaries.get(last.last_post_user_id)
                    if last and last.last_post_user_id
                    else None
                ),
                can_create_thread=authed and can_access(perms, c.thread_create_perm),
                can_reply=authed and can_access(perms, c.reply_perm),
            )
        )
    return ForumCategoryListResponse(categories=entries)


@router.post(
    "/categories", response_model=ForumCategoryResponse, status_code=status.HTTP_201_CREATED
)
async def create_category(
    body: ForumCategoryCreate,
    current_user: CurrentUser,
    db: DbDep,
    _: Annotated[None, Depends(require_permission(Permission.FORUM_CATEGORY_MANAGE))],
) -> ForumCategoryResponse:
    """Create a category. Requires FORUM_CATEGORY_MANAGE."""
    await _check_duplicate_title(db, body.title)
    category = ForumCategories(**body.model_dump())
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return _category_response(category)


@router.patch("/categories/{category_id}", response_model=ForumCategoryResponse)
async def update_category(
    category_id: int,
    body: ForumCategoryUpdate,
    current_user: CurrentUser,
    db: DbDep,
    _: Annotated[None, Depends(require_permission(Permission.FORUM_CATEGORY_MANAGE))],
) -> ForumCategoryResponse:
    """Update a category. Only provided fields change. Requires FORUM_CATEGORY_MANAGE."""
    category = await db.get(ForumCategories, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    updates = body.model_dump(exclude_unset=True)
    if "title" in updates:
        await _check_duplicate_title(db, updates["title"], exclude_category_id=category_id)
    for field, value in updates.items():
        setattr(category, field, value)
    await db.commit()
    await db.refresh(category)
    return _category_response(category)


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: int,
    current_user: CurrentUser,
    db: DbDep,
    _: Annotated[None, Depends(require_permission(Permission.FORUM_CATEGORY_MANAGE))],
) -> None:
    """Delete an empty category. 409 if it has any threads (even soft-deleted)."""
    category = await db.get(ForumCategories, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    thread_count = (
        await db.execute(
            select(func.count())
            .select_from(ForumThreads)
            .where(ForumThreads.category_id == category_id)
        )
    ).scalar() or 0
    if thread_count:
        raise HTTPException(
            status_code=409, detail="Category has threads and cannot be deleted"
        )
    await db.delete(category)
    await db.commit()
```

- [ ] **Step 5: Register the router**

In `app/api/v1/__init__.py`: add `forum,` to the import list (alphabetically, between `feeds` and `history`) and add `router.include_router(forum.router)` after the `comments` include.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_forum_categories.py -v`
Expected: PASS (19 tests)

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check app tests && uv run mypy app
git add app/api/v1/forum.py app/api/v1/__init__.py tests/api/v1/conftest.py tests/api/v1/test_forum_categories.py
git commit -m "feat(forum): category endpoints with perm-gated visibility and admin CRUD"
```

---

### Task 6: Thread endpoints

**Files:**
- Modify: `app/api/v1/forum.py` (add threads section)
- Test: `tests/api/v1/test_forum_threads.py`

**Interfaces:**
- Consumes: helpers from Task 5, services from Task 4.
- Produces: `GET/POST /api/v1/forum/categories/{category_id}/threads`, `GET/PATCH/DELETE /api/v1/forum/threads/{thread_id}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/v1/test_forum_threads.py`:

```python
"""Tests for forum thread endpoints."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forum import ForumPosts
from tests.api.v1.conftest import activate_user, make_thread


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _add_reply(db_session, thread, user_id=2, text="A reply") -> ForumPosts:
    """Insert a reply directly and fix up denormalized fields (test setup only)."""
    post = ForumPosts(thread_id=thread.thread_id, user_id=user_id, post_text=text)
    db_session.add(post)
    await db_session.flush()
    await db_session.refresh(post)
    thread.post_count += 1
    thread.last_post_at = post.date
    thread.last_post_user_id = user_id
    await db_session.commit()
    return post


class TestListThreads:
    """GET /api/v1/forum/categories/{category_id}/threads"""

    async def test_lists_threads_with_envelope(
        self, client: AsyncClient, public_category, public_thread
    ):
        response = await client.get(
            f"/api/v1/forum/categories/{public_category.category_id}/threads"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["threads"][0]["title"] == "Test thread"
        assert data["threads"][0]["user"]["username"] == "testuser"
        assert data["threads"][0]["post_count"] == 1

    async def test_gated_category_404(self, client: AsyncClient, staff_category, user_token):
        response = await client.get(
            f"/api/v1/forum/categories/{staff_category.category_id}/threads",
            headers=_auth(user_token),
        )
        assert response.status_code == 404

    async def test_pinned_first_then_activity(
        self, client: AsyncClient, db_session: AsyncSession, public_category
    ):
        oldest = await make_thread(db_session, public_category, title="Oldest")
        await make_thread(db_session, public_category, title="Middle")
        await make_thread(db_session, public_category, title="Newest")
        oldest.pinned = True
        await db_session.commit()

        response = await client.get(
            f"/api/v1/forum/categories/{public_category.category_id}/threads"
        )
        titles = [t["title"] for t in response.json()["threads"]]
        assert titles[0] == "Oldest"  # pinned wins over recency
        assert titles[1] == "Newest"

    async def test_excludes_deleted(
        self, client: AsyncClient, db_session: AsyncSession, public_category, public_thread
    ):
        public_thread.deleted = True
        await db_session.commit()
        response = await client.get(
            f"/api/v1/forum/categories/{public_category.category_id}/threads"
        )
        assert response.json()["total"] == 0

    async def test_unread_lifecycle(
        self, client: AsyncClient, db_session: AsyncSession, public_category, public_thread, user_token
    ):
        url = f"/api/v1/forum/categories/{public_category.category_id}/threads"
        # Anonymous: never unread
        anon = (await client.get(url)).json()["threads"][0]
        assert anon["unread"] is False
        # Fresh user: unread
        listed = (await client.get(url, headers=_auth(user_token))).json()["threads"][0]
        assert listed["unread"] is True
        # Viewing the thread marks it read
        await client.get(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(user_token)
        )
        listed = (await client.get(url, headers=_auth(user_token))).json()["threads"][0]
        assert listed["unread"] is False
        # A new reply by someone else makes it unread again
        await _add_reply(db_session, public_thread, user_id=2)
        listed = (await client.get(url, headers=_auth(user_token))).json()["threads"][0]
        assert listed["unread"] is True


class TestCreateThread:
    """POST /api/v1/forum/categories/{category_id}/threads"""

    async def test_requires_auth(self, client: AsyncClient, public_category):
        response = await client.post(
            f"/api/v1/forum/categories/{public_category.category_id}/threads",
            json={"title": "T", "post_text": "body"},
        )
        assert response.status_code == 401

    async def test_create_success(self, client: AsyncClient, public_category, user_token):
        response = await client.post(
            f"/api/v1/forum/categories/{public_category.category_id}/threads",
            json={"title": "My thread", "post_text": "Opening **post**"},
            headers=_auth(user_token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "My thread"
        assert data["post_count"] == 1
        assert data["user"]["username"] == "testuser3"
        assert data["unread"] is False  # own post never unread for the author

        # The opening post exists and renders markdown
        detail = (
            await client.get(f"/api/v1/forum/threads/{data['thread_id']}")
        ).json()
        assert detail["total"] == 1
        assert "<strong>post</strong>" in detail["posts"][0]["post_text_html"]

    async def test_create_gated_403(self, client: AsyncClient, announce_category, user_token):
        response = await client.post(
            f"/api/v1/forum/categories/{announce_category.category_id}/threads",
            json={"title": "T", "post_text": "body"},
            headers=_auth(user_token),
        )
        assert response.status_code == 403

    async def test_view_gated_404_not_403(
        self, client: AsyncClient, staff_category, user_token
    ):
        response = await client.post(
            f"/api/v1/forum/categories/{staff_category.category_id}/threads",
            json={"title": "T", "post_text": "body"},
            headers=_auth(user_token),
        )
        assert response.status_code == 404

    async def test_staff_can_create_in_gated(
        self, client: AsyncClient, staff_category, staff_token
    ):
        response = await client.post(
            f"/api/v1/forum/categories/{staff_category.category_id}/threads",
            json={"title": "Staff only", "post_text": "body"},
            headers=_auth(staff_token),
        )
        assert response.status_code == 201

    async def test_empty_title_422(self, client: AsyncClient, public_category, user_token):
        response = await client.post(
            f"/api/v1/forum/categories/{public_category.category_id}/threads",
            json={"title": "", "post_text": "body"},
            headers=_auth(user_token),
        )
        assert response.status_code == 422


class TestGetThread:
    """GET /api/v1/forum/threads/{thread_id}"""

    async def test_anon_reads_public_thread(self, client: AsyncClient, public_thread):
        response = await client.get(f"/api/v1/forum/threads/{public_thread.thread_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["thread"]["title"] == "Test thread"
        assert data["can_reply"] is False
        assert data["can_moderate"] is False
        assert data["total"] == 1
        assert data["posts"][0]["post_text"] == "Opening post"

    async def test_gated_thread_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category, user_token
    ):
        thread = await make_thread(db_session, staff_category)
        response = await client.get(
            f"/api/v1/forum/threads/{thread.thread_id}", headers=_auth(user_token)
        )
        assert response.status_code == 404

    async def test_deleted_thread_404_for_users_200_for_mods(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token, staff_token
    ):
        public_thread.deleted = True
        await db_session.commit()
        response = await client.get(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(user_token)
        )
        assert response.status_code == 404
        response = await client.get(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(staff_token)
        )
        assert response.status_code == 200
        assert response.json()["thread"]["deleted"] is True

    async def test_post_pagination(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        for i in range(3):
            await _add_reply(db_session, public_thread, text=f"reply {i}")
        response = await client.get(
            f"/api/v1/forum/threads/{public_thread.thread_id}?page=2&per_page=2"
        )
        data = response.json()
        assert data["total"] == 4  # opening + 3 replies
        assert len(data["posts"]) == 2
        assert data["posts"][0]["post_text"] == "reply 1"  # chronological order

    async def test_tombstone_hides_text_from_users_not_mods(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, staff_token
    ):
        reply = await _add_reply(db_session, public_thread, text="secret")
        reply.deleted = True
        await db_session.commit()

        url = f"/api/v1/forum/threads/{public_thread.thread_id}"
        anon_posts = (await client.get(url)).json()["posts"]
        assert anon_posts[1]["deleted"] is True
        assert anon_posts[1]["post_text"] == ""
        assert anon_posts[1]["post_text_html"] == ""

        mod_posts = (await client.get(url, headers=_auth(staff_token))).json()["posts"]
        assert mod_posts[1]["post_text"] == "secret"


class TestUpdateThread:
    """PATCH /api/v1/forum/threads/{thread_id}"""

    async def test_author_edits_title(self, client: AsyncClient, public_thread, author_token):
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"title": "Renamed"},
            headers=_auth(author_token),
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Renamed"

    async def test_non_author_cannot_edit_title(
        self, client: AsyncClient, public_thread, user_token
    ):
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"title": "Hijacked"},
            headers=_auth(user_token),
        )
        assert response.status_code == 403

    async def test_mod_fields_require_moderate(
        self, client: AsyncClient, public_thread, author_token
    ):
        # Even the author cannot pin/lock without FORUM_MODERATE
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"pinned": True},
            headers=_auth(author_token),
        )
        assert response.status_code == 403

    async def test_moderator_pins_locks(self, client: AsyncClient, public_thread, staff_token):
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"pinned": True, "locked": True},
            headers=_auth(staff_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["pinned"] is True
        assert data["locked"] is True

    async def test_locked_thread_title_still_editable(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, author_token
    ):
        public_thread.locked = True
        await db_session.commit()
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"title": "Still editable"},
            headers=_auth(author_token),
        )
        assert response.status_code == 200

    async def test_moderator_moves_thread(
        self, client: AsyncClient, public_thread, announce_category, staff_token
    ):
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"category_id": announce_category.category_id},
            headers=_auth(staff_token),
        )
        assert response.status_code == 200
        assert response.json()["category_id"] == announce_category.category_id

    async def test_move_to_missing_category_400(
        self, client: AsyncClient, public_thread, staff_token
    ):
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"category_id": 99999},
            headers=_auth(staff_token),
        )
        assert response.status_code == 400

    async def test_moderator_restores_deleted_thread(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, staff_token
    ):
        public_thread.deleted = True
        await db_session.commit()
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"deleted": False},
            headers=_auth(staff_token),
        )
        assert response.status_code == 200
        assert response.json()["deleted"] is False


class TestDeleteThread:
    """DELETE /api/v1/forum/threads/{thread_id}"""

    async def test_author_deletes_replyless_thread(
        self, client: AsyncClient, public_thread, author_token
    ):
        response = await client.delete(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(author_token)
        )
        assert response.status_code == 204
        response = await client.get(f"/api/v1/forum/threads/{public_thread.thread_id}")
        assert response.status_code == 404

    async def test_author_cannot_delete_thread_with_replies(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, author_token
    ):
        await _add_reply(db_session, public_thread)
        response = await client.delete(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(author_token)
        )
        assert response.status_code == 403

    async def test_moderator_deletes_thread_with_replies(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, staff_token
    ):
        await _add_reply(db_session, public_thread)
        response = await client.delete(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(staff_token)
        )
        assert response.status_code == 204

    async def test_non_author_cannot_delete(
        self, client: AsyncClient, public_thread, user_token
    ):
        response = await client.delete(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(user_token)
        )
        assert response.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_forum_threads.py -v`
Expected: FAIL — 404/405 (thread routes don't exist yet)

- [ ] **Step 3: Add the thread endpoints**

Append to `app/api/v1/forum.py`:

```python
# ===== Threads =====


@router.get("/categories/{category_id}/threads", response_model=ForumThreadListResponse)
async def list_threads(
    category_id: int,
    pagination: Annotated[PaginationParams, Depends()],
    db: DbDep,
    redis_client: RedisDep,
    current_user: OptionalCurrentUser,
) -> ForumThreadListResponse:
    """List live threads in a category: pinned first, then by last activity."""
    perms = await _effective_perms(db, redis_client, current_user)
    await _visible_category(db, category_id, perms)

    base = (
        select(ForumThreads)
        .where(ForumThreads.category_id == category_id)
        .where(ForumThreads.deleted == False)  # noqa: E712
    )
    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar() or 0
    rows = await db.execute(
        base.order_by(
            ForumThreads.pinned.desc(),  # type: ignore[union-attr]
            ForumThreads.last_post_at.desc(),  # type: ignore[union-attr]
        )
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )
    threads = list(rows.scalars().all())

    user_ids = {t.user_id for t in threads} | {
        t.last_post_user_id for t in threads if t.last_post_user_id
    }
    summaries = await build_user_summaries(db, user_ids)

    read_map: dict[int, datetime] = {}
    if current_user is not None and threads:
        read_rows = await db.execute(
            select(ForumThreadReads.thread_id, ForumThreadReads.last_read_at)
            .where(ForumThreadReads.user_id == current_user.user_id)
            .where(ForumThreadReads.thread_id.in_([t.thread_id for t in threads]))  # type: ignore[union-attr]
        )
        read_map = dict(read_rows.all())

    def is_unread(t: ForumThreads) -> bool:
        if current_user is None or t.last_post_at is None:
            return False
        last_read = read_map.get(t.thread_id)
        return last_read is None or last_read < t.last_post_at

    return ForumThreadListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        threads=[_thread_summary(t, summaries, is_unread(t)) for t in threads],
    )


@router.post(
    "/categories/{category_id}/threads",
    response_model=ForumThreadSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    category_id: int,
    body: ForumThreadCreate,
    request: Request,
    current_user: CurrentUser,
    db: DbDep,
    redis_client: RedisDep,
) -> ForumThreadSummary:
    """Create a thread with its opening post in one transaction."""
    assert current_user.user_id is not None
    perms = await _effective_perms(db, redis_client, current_user)
    category = await _visible_category(db, category_id, perms)
    if not can_access(perms, category.thread_create_perm):
        raise HTTPException(
            status_code=403, detail="You cannot create threads in this category"
        )

    thread = ForumThreads(
        category_id=category.category_id, title=body.title, user_id=current_user.user_id
    )
    db.add(thread)
    await db.flush()
    post = ForumPosts(
        thread_id=thread.thread_id,
        user_id=current_user.user_id,
        post_text=body.post_text,
        ip=request.client.host if request.client else "",
    )
    db.add(post)
    await db.flush()
    await db.refresh(post)
    thread.post_count = 1
    thread.last_post_at = post.date
    thread.last_post_user_id = current_user.user_id
    # The author has obviously read their own thread
    await upsert_thread_read(db, current_user.user_id, thread.thread_id, post.date)
    await db.commit()
    await db.refresh(thread)

    summaries = await build_user_summaries(db, {current_user.user_id})
    return _thread_summary(thread, summaries, unread=False)


@router.get("/threads/{thread_id}", response_model=ForumThreadDetailResponse)
async def get_thread(
    thread_id: int,
    pagination: Annotated[PaginationParams, Depends()],
    db: DbDep,
    redis_client: RedisDep,
    current_user: OptionalCurrentUser,
) -> ForumThreadDetailResponse:
    """Thread meta + one page of posts (chronological). Marks the thread read
    for authenticated callers."""
    perms = await _effective_perms(db, redis_client, current_user)
    is_moderator = Permission.FORUM_MODERATE.value in perms

    thread = await db.get(ForumThreads, thread_id)
    if thread is None or (thread.deleted and not is_moderator):
        raise HTTPException(status_code=404, detail="Thread not found")
    category = await _visible_category(db, thread.category_id, perms)

    total = (
        await db.execute(
            select(func.count())
            .select_from(ForumPosts)
            .where(ForumPosts.thread_id == thread_id)
        )
    ).scalar() or 0
    posts = (
        await db.execute(
            select(ForumPosts)
            .where(ForumPosts.thread_id == thread_id)
            .order_by(ForumPosts.post_id)  # type: ignore[arg-type]
            .offset(pagination.offset)
            .limit(pagination.per_page)
        )
    ).scalars().all()

    user_ids = {p.user_id for p in posts} | {thread.user_id}
    if thread.last_post_user_id:
        user_ids.add(thread.last_post_user_id)
    summaries = await build_user_summaries(db, user_ids)

    if current_user is not None and current_user.user_id is not None:
        await upsert_thread_read(db, current_user.user_id, thread_id, datetime.now(UTC))
        await db.commit()

    return ForumThreadDetailResponse(
        thread=_thread_summary(thread, summaries, unread=False),
        can_reply=current_user is not None and can_access(perms, category.reply_perm),
        can_moderate=is_moderator,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        posts=[_post_response(p, summaries[p.user_id], is_moderator) for p in posts],
    )


@router.patch("/threads/{thread_id}", response_model=ForumThreadSummary)
async def update_thread(
    thread_id: int,
    body: ForumThreadUpdate,
    current_user: CurrentUser,
    db: DbDep,
    redis_client: RedisDep,
) -> ForumThreadSummary:
    """title: author or FORUM_MODERATE. pinned/locked/category_id/deleted:
    FORUM_MODERATE only."""
    assert current_user.user_id is not None
    perms = await _effective_perms(db, redis_client, current_user)
    is_moderator = Permission.FORUM_MODERATE.value in perms

    thread = await db.get(ForumThreads, thread_id)
    if thread is None or (thread.deleted and not is_moderator):
        raise HTTPException(status_code=404, detail="Thread not found")
    await _visible_category(db, thread.category_id, perms)

    updates = body.model_dump(exclude_unset=True)
    mod_fields = {"pinned", "locked", "category_id", "deleted"} & updates.keys()
    if mod_fields and not is_moderator:
        raise HTTPException(
            status_code=403, detail="FORUM_MODERATE permission required"
        )
    if "title" in updates and not (is_moderator or thread.user_id == current_user.user_id):
        raise HTTPException(
            status_code=403,
            detail="Only the thread author or a moderator can edit the title",
        )
    if "category_id" in updates:
        target = await db.get(ForumCategories, updates["category_id"])
        if target is None:
            raise HTTPException(status_code=400, detail="Target category does not exist")

    for field, value in updates.items():
        setattr(thread, field, value)
    await db.commit()
    await db.refresh(thread)

    user_ids = {thread.user_id}
    if thread.last_post_user_id:
        user_ids.add(thread.last_post_user_id)
    summaries = await build_user_summaries(db, user_ids)
    return _thread_summary(thread, summaries, unread=False)


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: int,
    current_user: CurrentUser,
    db: DbDep,
    redis_client: RedisDep,
) -> None:
    """Soft-delete. Author allowed only while the thread has no replies;
    otherwise FORUM_MODERATE."""
    assert current_user.user_id is not None
    perms = await _effective_perms(db, redis_client, current_user)
    is_moderator = Permission.FORUM_MODERATE.value in perms

    thread = await db.get(ForumThreads, thread_id)
    if thread is None or (thread.deleted and not is_moderator):
        raise HTTPException(status_code=404, detail="Thread not found")
    await _visible_category(db, thread.category_id, perms)

    if not is_moderator:
        if thread.user_id != current_user.user_id:
            raise HTTPException(
                status_code=403, detail="Only the author or a moderator can delete this thread"
            )
        if thread.post_count > 1:
            raise HTTPException(
                status_code=403,
                detail="Threads with replies can only be deleted by moderators",
            )
    thread.deleted = True
    await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_forum_threads.py -v`
Expected: PASS (28 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app tests && uv run mypy app
git add app/api/v1/forum.py tests/api/v1/test_forum_threads.py
git commit -m "feat(forum): thread endpoints with unread tracking and moderation"
```

---

### Task 7: Post endpoints

**Files:**
- Modify: `app/api/v1/forum.py` (add posts section)
- Test: `tests/api/v1/test_forum_posts.py`

**Interfaces:**
- Consumes: helpers from Task 5, `recompute_thread_stats`/`upsert_thread_read` from Task 4.
- Produces: `POST /api/v1/forum/threads/{thread_id}/posts`, `PATCH/DELETE /api/v1/forum/posts/{post_id}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/v1/test_forum_posts.py`:

```python
"""Tests for forum post endpoints."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.models.forum import ForumCategories, ForumThreads
from tests.api.v1.conftest import make_thread


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _get_thread(db_session: AsyncSession, thread_id: int) -> ForumThreads:
    db_session.expire_all()
    thread = await db_session.get(ForumThreads, thread_id)
    assert thread is not None
    return thread


class TestCreatePost:
    """POST /api/v1/forum/threads/{thread_id}/posts"""

    async def test_requires_auth(self, client: AsyncClient, public_thread):
        response = await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": "hi"},
        )
        assert response.status_code == 401

    async def test_reply_updates_thread_stats(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token
    ):
        response = await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": 'Nice thread [quote="testuser"]Opening post[/quote]'},
            headers=_auth(user_token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["user"]["username"] == "testuser3"
        assert "blockquote" in data["post_text_html"]

        thread = await _get_thread(db_session, public_thread.thread_id)
        assert thread.post_count == 2
        assert thread.last_post_user_id == 3
        assert thread.last_post_at is not None

    async def test_own_reply_not_unread_for_author(
        self, client: AsyncClient, public_category, public_thread, user_token
    ):
        await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": "my reply"},
            headers=_auth(user_token),
        )
        listed = (
            await client.get(
                f"/api/v1/forum/categories/{public_category.category_id}/threads",
                headers=_auth(user_token),
            )
        ).json()["threads"][0]
        assert listed["unread"] is False

    async def test_locked_thread_403(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token
    ):
        public_thread.locked = True
        await db_session.commit()
        response = await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": "hi"},
            headers=_auth(user_token),
        )
        assert response.status_code == 403
        assert "locked" in response.json()["detail"].lower()

    async def test_locked_blocks_moderators_too(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, staff_token
    ):
        public_thread.locked = True
        await db_session.commit()
        response = await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": "hi"},
            headers=_auth(staff_token),
        )
        assert response.status_code == 403

    async def test_deleted_thread_403(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, staff_token
    ):
        public_thread.deleted = True
        await db_session.commit()
        response = await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": "hi"},
            headers=_auth(staff_token),
        )
        assert response.status_code == 403

    async def test_view_gated_thread_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category, user_token
    ):
        thread = await make_thread(db_session, staff_category)
        response = await client.post(
            f"/api/v1/forum/threads/{thread.thread_id}/posts",
            json={"post_text": "hi"},
            headers=_auth(user_token),
        )
        assert response.status_code == 404

    async def test_reply_gated_403(
        self, client: AsyncClient, db_session: AsyncSession, user_token
    ):
        # Public view, staff-only replies (a read-only announcements pattern)
        cat = ForumCategories(
            title="Read Only",
            reply_perm=Permission.FORUM_ACCESS_STAFF.value,
        )
        db_session.add(cat)
        await db_session.flush()
        thread = await make_thread(db_session, cat)
        response = await client.post(
            f"/api/v1/forum/threads/{thread.thread_id}/posts",
            json={"post_text": "hi"},
            headers=_auth(user_token),
        )
        assert response.status_code == 403


class TestUpdatePost:
    """PATCH /api/v1/forum/posts/{post_id}"""

    async def _create_reply(self, client, thread_id: int, token: str) -> dict:
        response = await client.post(
            f"/api/v1/forum/threads/{thread_id}/posts",
            json={"post_text": "original"},
            headers=_auth(token),
        )
        assert response.status_code == 201
        return response.json()

    async def test_owner_edits_with_tracking(
        self, client: AsyncClient, public_thread, user_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"post_text": "edited"},
            headers=_auth(user_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["post_text"] == "edited"
        assert data["update_count"] == 1
        assert data["last_updated"] is not None
        assert data["last_updated_user_id"] == 3

    async def test_non_owner_cannot_edit(
        self, client: AsyncClient, public_thread, user_token, author_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"post_text": "hijack"},
            headers=_auth(author_token),
        )
        assert response.status_code == 403

    async def test_moderator_edits_others_post(
        self, client: AsyncClient, public_thread, user_token, staff_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"post_text": "moderated"},
            headers=_auth(staff_token),
        )
        assert response.status_code == 200
        assert response.json()["last_updated_user_id"] == 2

    async def test_deleted_post_edit_400(
        self, client: AsyncClient, public_thread, user_token, staff_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        await client.delete(
            f"/api/v1/forum/posts/{post['post_id']}", headers=_auth(user_token)
        )
        response = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"post_text": "necro-edit"},
            headers=_auth(staff_token),
        )
        assert response.status_code == 400

    async def test_plain_user_cannot_set_deleted(
        self, client: AsyncClient, public_thread, user_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"deleted": True},
            headers=_auth(user_token),
        )
        assert response.status_code == 403

    async def test_moderator_restores_post_and_stats_recover(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token, staff_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        await client.delete(
            f"/api/v1/forum/posts/{post['post_id']}", headers=_auth(staff_token)
        )
        thread = await _get_thread(db_session, public_thread.thread_id)
        assert thread.post_count == 1

        response = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"deleted": False},
            headers=_auth(staff_token),
        )
        assert response.status_code == 200
        thread = await _get_thread(db_session, public_thread.thread_id)
        assert thread.post_count == 2
        assert thread.last_post_user_id == 3


class TestDeletePost:
    """DELETE /api/v1/forum/posts/{post_id}"""

    async def _create_reply(self, client, thread_id: int, token: str) -> dict:
        response = await client.post(
            f"/api/v1/forum/threads/{thread_id}/posts",
            json={"post_text": "to delete"},
            headers=_auth(token),
        )
        return response.json()

    async def test_owner_deletes_and_stats_recompute(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.delete(
            f"/api/v1/forum/posts/{post['post_id']}", headers=_auth(user_token)
        )
        assert response.status_code == 204

        thread = await _get_thread(db_session, public_thread.thread_id)
        assert thread.post_count == 1
        assert thread.last_post_user_id == 1  # back to the opening post's author

        # Tombstone visible in the thread, text blanked
        detail = (
            await client.get(f"/api/v1/forum/threads/{public_thread.thread_id}")
        ).json()
        assert detail["posts"][1]["deleted"] is True
        assert detail["posts"][1]["post_text"] == ""

    async def test_opening_post_cannot_be_deleted(
        self, client: AsyncClient, public_thread, author_token
    ):
        detail = (
            await client.get(f"/api/v1/forum/threads/{public_thread.thread_id}")
        ).json()
        opening_id = detail["posts"][0]["post_id"]
        response = await client.delete(
            f"/api/v1/forum/posts/{opening_id}", headers=_auth(author_token)
        )
        assert response.status_code == 400

    async def test_non_owner_cannot_delete(
        self, client: AsyncClient, public_thread, user_token, author_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.delete(
            f"/api/v1/forum/posts/{post['post_id']}", headers=_auth(author_token)
        )
        assert response.status_code == 403

    async def test_moderator_deletes_others_post(
        self, client: AsyncClient, public_thread, user_token, staff_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.delete(
            f"/api/v1/forum/posts/{post['post_id']}", headers=_auth(staff_token)
        )
        assert response.status_code == 204
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_forum_posts.py -v`
Expected: FAIL — 404/405 (post routes don't exist yet)

- [ ] **Step 3: Add the post endpoints**

Append to `app/api/v1/forum.py`:

```python
# ===== Posts =====


@router.post(
    "/threads/{thread_id}/posts",
    response_model=ForumPostResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_post(
    thread_id: int,
    body: ForumPostCreate,
    request: Request,
    current_user: CurrentUser,
    db: DbDep,
    redis_client: RedisDep,
) -> ForumPostResponse:
    """Reply to a thread. Locked blocks everyone (moderators unlock first)."""
    assert current_user.user_id is not None
    perms = await _effective_perms(db, redis_client, current_user)
    is_moderator = Permission.FORUM_MODERATE.value in perms

    # Lock the thread row: the denormalized stats recompute below must not
    # race a concurrent reply/delete.
    result = await db.execute(
        select(ForumThreads).where(ForumThreads.thread_id == thread_id).with_for_update()
    )
    thread = result.scalar_one_or_none()
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    category = await _visible_category(db, thread.category_id, perms)
    if thread.deleted:
        raise HTTPException(status_code=403, detail="Thread is deleted")
    if thread.locked:
        raise HTTPException(status_code=403, detail="Thread is locked")
    if not can_access(perms, category.reply_perm):
        raise HTTPException(status_code=403, detail="You cannot reply in this category")

    post = ForumPosts(
        thread_id=thread_id,
        user_id=current_user.user_id,
        post_text=body.post_text,
        ip=request.client.host if request.client else "",
    )
    db.add(post)
    await db.flush()
    await db.refresh(post)
    await recompute_thread_stats(db, thread)
    # The author has read their own reply
    await upsert_thread_read(db, current_user.user_id, thread_id, post.date)
    await db.commit()

    summaries = await build_user_summaries(db, {current_user.user_id})
    return _post_response(post, summaries[current_user.user_id], is_moderator)


@router.patch("/posts/{post_id}", response_model=ForumPostResponse)
async def update_post(
    post_id: int,
    body: ForumPostUpdate,
    current_user: CurrentUser,
    db: DbDep,
    redis_client: RedisDep,
) -> ForumPostResponse:
    """post_text: owner or FORUM_MODERATE (post must not be deleted).
    deleted: FORUM_MODERATE only (restore or delete; recomputes thread stats)."""
    assert current_user.user_id is not None
    perms = await _effective_perms(db, redis_client, current_user)
    is_moderator = Permission.FORUM_MODERATE.value in perms

    post = await db.get(ForumPosts, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    result = await db.execute(
        select(ForumThreads)
        .where(ForumThreads.thread_id == post.thread_id)
        .with_for_update()
    )
    thread = result.scalar_one()
    if thread.deleted and not is_moderator:
        raise HTTPException(status_code=404, detail="Thread not found")
    await _visible_category(db, thread.category_id, perms)

    updates = body.model_dump(exclude_unset=True)
    if "deleted" in updates and not is_moderator:
        raise HTTPException(status_code=403, detail="FORUM_MODERATE permission required")
    if "post_text" in updates:
        if not (is_moderator or post.user_id == current_user.user_id):
            raise HTTPException(
                status_code=403, detail="Only the author or a moderator can edit this post"
            )
        will_be_deleted = updates.get("deleted", post.deleted)
        if will_be_deleted:
            raise HTTPException(
                status_code=400, detail="Restore the post before editing it"
            )
        post.post_text = updates["post_text"]
        post.update_count += 1
        post.last_updated = datetime.now(UTC)
        post.last_updated_user_id = current_user.user_id
    if "deleted" in updates:
        if updates["deleted"] and post.post_id == await _first_post_id(db, thread.thread_id):
            raise HTTPException(
                status_code=400,
                detail="The opening post cannot be deleted; delete the thread instead",
            )
        post.deleted = updates["deleted"]
        await recompute_thread_stats(db, thread)
    await db.commit()

    summaries = await build_user_summaries(db, {post.user_id})
    return _post_response(post, summaries[post.user_id], is_moderator)


@router.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(
    post_id: int,
    current_user: CurrentUser,
    db: DbDep,
    redis_client: RedisDep,
) -> None:
    """Soft-delete a post (owner or FORUM_MODERATE). The opening post cannot
    be deleted alone — delete the thread instead."""
    assert current_user.user_id is not None
    perms = await _effective_perms(db, redis_client, current_user)
    is_moderator = Permission.FORUM_MODERATE.value in perms

    post = await db.get(ForumPosts, post_id)
    if post is None or (post.deleted and not is_moderator):
        raise HTTPException(status_code=404, detail="Post not found")
    result = await db.execute(
        select(ForumThreads)
        .where(ForumThreads.thread_id == post.thread_id)
        .with_for_update()
    )
    thread = result.scalar_one()
    if thread.deleted and not is_moderator:
        raise HTTPException(status_code=404, detail="Thread not found")
    await _visible_category(db, thread.category_id, perms)

    if not (is_moderator or post.user_id == current_user.user_id):
        raise HTTPException(
            status_code=403, detail="Only the author or a moderator can delete this post"
        )
    if post.post_id == await _first_post_id(db, thread.thread_id):
        raise HTTPException(
            status_code=400,
            detail="The opening post cannot be deleted; delete the thread instead",
        )
    post.deleted = True
    await recompute_thread_stats(db, thread)
    await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_forum_posts.py -v`
Expected: PASS (18 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app tests && uv run mypy app
git add app/api/v1/forum.py tests/api/v1/test_forum_posts.py
git commit -m "feat(forum): post endpoints with edit tracking and tombstones"
```

---

### Task 8: Full-suite verification and dev deploy check

**Files:** none new.

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest`
Expected: PASS with no new failures (compare against a `git stash`-free main baseline if anything unrelated fails — pre-existing failures are documented in the repo's memory, but new failures are yours).

- [ ] **Step 2: Lint + type-check everything**

Run: `uv run ruff check app tests && uv run mypy app`
Expected: clean (or only pre-existing issues on files you didn't touch).

- [ ] **Step 3: Apply the migration to the dev database and verify grants**

```bash
uv run alembic upgrade head
uv run python -c "
import asyncio
from sqlalchemy import text
from app.core.database import engine

async def main():
    async with engine.connect() as conn:
        rows = await conn.execute(text('''
            SELECT g.title AS grp, p.title AS perm
            FROM group_perms gp
            JOIN perms p ON gp.perm_id = p.perm_id
            JOIN \`groups\` g ON gp.group_id = g.group_id
            WHERE p.title LIKE 'forum_%' ORDER BY 1, 2
        '''))
        for row in rows:
            print(row.grp, row.perm)

asyncio.run(main())
"
```

Expected output (8 grant rows — Taggers get only the tagger tier):

```
Admins forum_access_staff
Admins forum_access_tagger
Admins forum_category_manage
Admins forum_moderate
Mods forum_access_staff
Mods forum_access_tagger
Mods forum_moderate
Taggers forum_access_tagger
```

- [ ] **Step 4: Smoke-test the API manually**

With the dev API running: `curl -s localhost:8000/api/v1/forum/categories | jq` → `{"categories": []}`.

- [ ] **Step 5: Hand off to the frontend**

The frontend regenerates its API types from this server's OpenAPI schema (see the frontend repo's type-generation script) — the frontend plan's Task 1 depends on this API branch running locally.
