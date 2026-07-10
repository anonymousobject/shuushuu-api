# User Taste Profile & Recommendations — API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Precompute a per-user tag-affinity profile from favorites/ratings/uploads and expose it via `GET /users/me/taste-profile` (private analytics) and `GET /images/recommended` (live-scored personalized feed).

**Architecture:** A nightly-refreshed `user_tag_affinity` analytics table (built with the same materialized-helper-tables + advisory-lock + staging-swap machinery as `tag_cooccurrence`, but batched by user ranges), read by two thin endpoints. Recommendation scoring happens live at request time from the profile. Spec: `docs/plans/2026-07-09-user-taste-profile-design.md` (approved).

**Tech Stack:** FastAPI + SQLModel + MariaDB (raw SQL via `text()` for the batch job), arq cron, alembic, pytest (real MariaDB test DB).

## Global Constraints

- Branch: `feat/user-taste-profile` (already exists; the spec commit is on it). Repo: `shuushuu-api`. This PR merges BEFORE the frontend PR.
- All thresholds are config settings: `TASTE_MIN_SUPPORT=5`, `TASTE_SMOOTHING_K=200`, `TASTE_RATING_BETA=0.5`, `TASTE_MIN_EVENTS=10`, `TASTE_BATCH_SIZE=500`, `TASTE_TOP_TAGS=30`, `TASTE_CANDIDATE_CAP=3000`, `TASTE_FEED_POOL=500`, `TASTE_DISPLAY_MIN_LIFT=1.5`.
- The analytics table has NO foreign keys (rebuilt wholesale via `RENAME TABLE` swap; `CREATE TABLE ... LIKE` wouldn't copy them anyway).
- Never `git add -A` in this repo (unrelated WIP is present). Stage files explicitly.
- Commit messages use conventional-commit prefixes (`feat:`, `test:`, `docs:`).
- Tests run from the repo root with `uv run pytest ...` (`/etc/hosts` maps `mariadb` → 127.0.0.1). Markers: service tests use `pytestmark = [pytest.mark.integration, pytest.mark.needs_commit]`.
- Timing evidence (measured on dev data 2026-07-09): full live-scoring pipeline ≈ 49 ms for the heaviest user; refresh ≈ 73 s per 500-user batch → ~15 min for all ~6k eligible users.

---

### Task 1: Model, migration, config settings

**Files:**
- Create: `app/models/user_tag_affinity.py`
- Modify: `app/models/__init__.py` (register model)
- Modify: `app/config.py` (TASTE_* settings block, after the `COOCCUR_*`-style blocks near line ~187; on this branch, after `BANNER_CACHE_TTL_JITTER`)
- Create: `alembic/versions/<generated>_add_user_tag_affinity_table.py`
- Test: `tests/services/test_user_tag_affinity.py` (schema smoke test only in this task)

**Interfaces:**
- Produces: `UserTagAffinity` SQLModel (table `user_tag_affinity`) with columns `user_id, tag_id, pool_cnt, fav_count, upload_count, rated_count, rating_avg, lift, rating_delta, affinity, updated_at`; settings `settings.TASTE_MIN_SUPPORT` etc. Later tasks import both.

- [ ] **Step 1: Write the failing schema smoke test**

Create `tests/services/test_user_tag_affinity.py`:

```python
import pytest
from sqlalchemy import text

from app.models.user_tag_affinity import UserTagAffinity

pytestmark = [pytest.mark.integration, pytest.mark.needs_commit]


async def test_table_roundtrip(db_session):
    db_session.add(
        UserTagAffinity(
            user_id=1,
            tag_id=2,
            pool_cnt=10,
            fav_count=8,
            upload_count=3,
            rated_count=6,
            rating_avg=8.5,
            lift=4.2,
            rating_delta=1.5,
            affinity=2.19,
        )
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            text("SELECT pool_cnt, affinity FROM user_tag_affinity WHERE user_id=1 AND tag_id=2")
        )
    ).one()
    assert row.pool_cnt == 10
    assert row.affinity == pytest.approx(2.19)


async def test_updated_at_server_default(db_session):
    # The refresh job inserts via raw INSERT…SELECT that OMITS updated_at,
    # relying on the server default. ORM inserts send explicit NULL (SQLModel
    # materializes the None default), so test the raw path directly.
    await db_session.execute(
        text(
            "INSERT INTO user_tag_affinity "
            "(user_id, tag_id, pool_cnt, fav_count, upload_count, rated_count, affinity) "
            "VALUES (7, 8, 5, 5, 0, 0, 1.0)"
        )
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            text("SELECT updated_at FROM user_tag_affinity WHERE user_id=7 AND tag_id=8")
        )
    ).one()
    assert row.updated_at is not None  # server_default filled it
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/services/test_user_tag_affinity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.user_tag_affinity'`

- [ ] **Step 3: Create the model**

Create `app/models/user_tag_affinity.py`:

```python
"""SQLModel for the precomputed per-user tag-affinity table (taste profiles)."""

from datetime import datetime

from sqlalchemy import Column, Float, Index, Integer, text
from sqlmodel import Field, SQLModel

from app.models.types import UnsignedInt, UtcDateTime


class UserTagAffinity(SQLModel, table=True):
    """Per-(user, tag) taste evidence + blended affinity score.

    Rebuilt nightly by refresh_user_tag_affinity via atomic staging-table swap;
    treat as read-only outside the refresh job. No FKs by design (same rationale
    as tag_cooccurrence: full rebuild maintains consistency, and
    CREATE TABLE ... LIKE would silently drop FKs after the first swap anyway).
    Only rows meeting min support are stored: pool_cnt >= TASTE_MIN_SUPPORT or
    rated_count >= TASTE_MIN_SUPPORT.
    """

    __tablename__ = "user_tag_affinity"

    __table_args__ = (Index("idx_user_tag_affinity_lookup", "user_id", "affinity"),)

    user_id: int = Field(sa_column=Column(UnsignedInt, primary_key=True, nullable=False))
    tag_id: int = Field(sa_column=Column(UnsignedInt, primary_key=True, nullable=False))
    # positive pool = favorites ∪ uploads, deduped; pool_cnt is the lift support
    pool_cnt: int = Field(sa_column=Column(Integer, nullable=False))
    fav_count: int = Field(sa_column=Column(Integer, nullable=False))
    upload_count: int = Field(sa_column=Column(Integer, nullable=False))
    rated_count: int = Field(sa_column=Column(Integer, nullable=False))
    rating_avg: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    # smoothed pool-share vs global-share; NULL when the user has no positive pool
    lift: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    # rating_avg minus the user's overall mean rating; NULL when unrated
    rating_delta: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    affinity: float = Field(sa_column=Column(Float, nullable=False))
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(UtcDateTime, nullable=True, server_default=text("current_timestamp()")),
    )
```

- [ ] **Step 4: Register the model**

In `app/models/__init__.py`, add (alphabetical placement, mirroring the existing imports):

```python
from app.models.user_tag_affinity import UserTagAffinity
```

and add `"UserTagAffinity",` to `__all__` (in the junction/relationship-tables group where `"TagCooccurrence"` would sit — this branch doesn't have co-occurrence merged, so put it after `"TagLinks"`).

- [ ] **Step 5: Add config settings**

In `app/config.py`, inside `class Settings`, after the `BANNER_CACHE_TTL_JITTER` field, add:

```python
    # User taste profiles (per-user tag affinity) + recommendations
    TASTE_MIN_SUPPORT: int = Field(default=5, ge=1)  # min pool/rated images to store a row
    TASTE_SMOOTHING_K: int = Field(
        default=200, ge=0
    )  # add-K on global tag counts; damps sole-contributor lift saturation
    TASTE_RATING_BETA: float = Field(
        default=0.5, ge=0.0
    )  # weight of the rating-delta axis in the blended affinity
    TASTE_MIN_EVENTS: int = Field(default=10, ge=1)  # favs+ratings+uploads to be profiled
    TASTE_BATCH_SIZE: int = Field(
        default=500, ge=1
    )  # users per INSERT…SELECT batch; unbatched is ~75M intermediate rows (OOM risk)
    TASTE_TOP_TAGS: int = Field(default=30, ge=1)  # positive tags used for candidate generation
    TASTE_CANDIDATE_CAP: int = Field(default=3000, ge=1)  # max candidate images scored per request
    TASTE_FEED_POOL: int = Field(default=500, ge=1)  # max scored feed depth (pagination cap)
    TASTE_DISPLAY_MIN_LIFT: float = Field(
        default=1.5, ge=0.0
    )  # analytics display floor; keeps popularity-only tags (e.g. "long hair") out
```

- [ ] **Step 6: Create the migration**

Run: `uv run alembic revision -m "add user_tag_affinity table"`
Edit the generated file in `alembic/versions/` so upgrade/downgrade read:

```python
def upgrade() -> None:
    """Upgrade schema."""
    # No FKs: this table is fully rebuilt nightly via atomic swap
    # (CREATE TABLE ... LIKE drops FKs anyway).
    op.create_table(
        "user_tag_affinity",
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("tag_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("pool_cnt", sa.Integer(), nullable=False),
        sa.Column("fav_count", sa.Integer(), nullable=False),
        sa.Column("upload_count", sa.Integer(), nullable=False),
        sa.Column("rated_count", sa.Integer(), nullable=False),
        sa.Column("rating_avg", sa.Float(), nullable=True),
        sa.Column("lift", sa.Float(), nullable=True),
        sa.Column("rating_delta", sa.Float(), nullable=True),
        sa.Column("affinity", sa.Float(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("user_id", "tag_id"),
    )
    op.create_index("idx_user_tag_affinity_lookup", "user_tag_affinity", ["user_id", "affinity"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_user_tag_affinity_lookup", table_name="user_tag_affinity")
    op.drop_table("user_tag_affinity")
```

Add `from sqlalchemy.dialects import mysql` to the imports. `down_revision` must be `"25cad69549de"` (current main head); the `revision` id is whatever alembic generated.

- [ ] **Step 7: Apply the migration to the dev DB and run the test**

Run: `uv run alembic upgrade head`
Expected: `Running upgrade 25cad69549de -> <newid>, add user_tag_affinity table`

GOTCHA: the dev DB may have unmerged-branch migrations applied (e.g. the
co-occurrence branch's `6d0cd96441f5`), which makes `head` ambiguous
("Multiple head revisions"). If that happens, run
`uv run alembic upgrade heads` (plural) — it advances every branch, applying
only the new revision. Check state first with `uv run alembic current`.

Run: `uv run pytest tests/services/test_user_tag_affinity.py -v`
Expected: PASS (the test DB creates tables from model metadata, so the model alone satisfies it; the migration is exercised by the dev-DB upgrade above)

- [ ] **Step 8: Commit**

```bash
git add app/models/user_tag_affinity.py app/models/__init__.py app/config.py alembic/versions/*user_tag_affinity* tests/services/test_user_tag_affinity.py
git commit -m "feat(taste): user_tag_affinity model, migration, config knobs"
```

---

### Task 2: Refresh service (the scoring math)

**Files:**
- Create: `app/services/user_tag_affinity.py`
- Test: `tests/services/test_user_tag_affinity.py` (extend)

**Interfaces:**
- Consumes: `UserTagAffinity` model/table (Task 1), `PUBLIC_IMAGE_STATUSES` from `app.services.image_visibility`.
- Produces: `async def refresh_user_tag_affinity(db: AsyncSession, *, min_support: int, smoothing_k: int, beta: float, min_events: int, batch_size: int) -> int` — returns rows written, or `-1` if the advisory lock was held. Module constant `_LOCK_PREFIX = "user_tag_affinity_refresh"`.

**Scoring definitions (from the spec — the tests below encode these exactly):**
- Positive pool P(u) = favorites ∪ uploads, deduped, visible statuses only.
- `lift(u,t) = (pool_cnt / pool_size) / ((vc(t) + K) / N)` where `vc(t)` = visible images carrying canonical tag t, `N` = all visible tagged images.
- `rating_delta(u,t) = avg(rating over u's rated visible images with t) − avg(all u's ratings of visible images)`.
- `affinity = [pool_cnt ≥ min_support] · ln(lift) + β · [rated_count ≥ min_support] · rating_delta` (each axis contributes only when supported; brackets are 0/1).
- Row stored iff `pool_cnt ≥ min_support OR rated_count ≥ min_support`. Users below `min_events` total raw events (favs+ratings+uploads) get no rows.

- [ ] **Step 1: Write the failing service tests**

Append to `tests/services/test_user_tag_affinity.py` (keep the Task-1 test; add imports and helpers at top):

```python
import math

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagType
from app.models.favorite import Favorites
from app.models.image import Images
from app.models.image_rating import ImageRatings
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.services.user_tag_affinity import _LOCK_PREFIX, refresh_user_tag_affinity


def _img(db, image_id, user_id=1, status=ImageStatus.ACTIVE):
    # ext is NOT NULL with no default in the schema, so it must be supplied.
    db.add(Images(image_id=image_id, user_id=user_id, ext="jpg", status=status))


def _tag(db, tag_id, ttype, title, alias_of=None):
    db.add(Tags(tag_id=tag_id, type=ttype, title=title, alias_of=alias_of))


def _link(db, tag_id, image_id):
    db.add(TagLinks(tag_id=tag_id, image_id=image_id, user_id=1))


def _fav(db, user_id, image_id):
    db.add(Favorites(user_id=user_id, image_id=image_id))


def _rate(db, user_id, image_id, rating):
    db.add(ImageRatings(user_id=user_id, image_id=image_id, rating=rating))


async def _rows(db):
    from sqlalchemy import text

    res = await db.execute(
        text(
            "SELECT user_id, tag_id, pool_cnt, fav_count, upload_count, rated_count, "
            "rating_avg, lift, rating_delta, affinity FROM user_tag_affinity"
        )
    )
    return {(r.user_id, r.tag_id): r for r in res.all()}


REFRESH_KW = dict(min_support=2, smoothing_k=0, beta=0.5, min_events=3, batch_size=500)


async def test_lift_and_affinity_from_favorites(db_session):
    # World: 10 visible images. Tag 20 on images 1..2 only; tag 30 on all 10.
    # User 500 favorites images 1..2 (pool_size=2, both tags 20 and 30... tag 30 also on 1..2).
    # lift(500,20) = (2/2) / (2/10) = 5.0 ; lift(500,30) = (2/2) / (10/10) = 1.0
    _tag(db_session, 20, TagType.SOURCE, "Niche")
    _tag(db_session, 30, TagType.THEME, "Generic")
    for i in range(1, 11):
        _img(db_session, i)
        _link(db_session, 30, i)
    for i in (1, 2):
        _link(db_session, 20, i)
    _fav(db_session, 500, 1)
    _fav(db_session, 500, 2)
    _rate(db_session, 500, 3, 7)  # 3rd event to clear min_events=3
    await db_session.commit()

    n = await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    assert n > 0
    rows = await _rows(db_session)
    r20 = rows[(500, 20)]
    r30 = rows[(500, 30)]
    assert r20.pool_cnt == 2 and r20.fav_count == 2 and r20.upload_count == 0
    assert r20.lift == pytest.approx(5.0)
    assert r30.lift == pytest.approx(1.0)
    assert r20.affinity == pytest.approx(math.log(5.0))
    assert r30.affinity == pytest.approx(0.0)  # ln(1.0)


async def test_pool_dedupes_fav_of_own_upload(db_session):
    # User 500 uploads images 1-2 AND favorites both: each image counts ONCE
    # in the pool (pool_cnt=2, not 4), while fav_count and upload_count each
    # record their own axis.
    _tag(db_session, 20, TagType.SOURCE, "S")
    for i in (1, 2):
        _img(db_session, i, user_id=500)
        _link(db_session, 20, i)
    _fav(db_session, 500, 1)
    _fav(db_session, 500, 2)  # 4 events total >= 3
    await db_session.commit()

    await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    rows = await _rows(db_session)
    r = rows[(500, 20)]
    assert r.pool_cnt == 2
    assert r.fav_count == 2
    assert r.upload_count == 2


async def test_rating_delta_is_centered_on_user_mean(db_session):
    # User 500 rates: images with tag 20 at 9,9 ; images with tag 30 at 5,5.
    # user_mean = 7.0 -> delta(20)=+2, delta(30)=-2.
    # affinity = beta * delta (no pool) -> +1.0 / -1.0 with beta=0.5.
    _tag(db_session, 20, TagType.SOURCE, "Loved")
    _tag(db_session, 30, TagType.THEME, "Disliked")
    for i, (tag, rating) in enumerate([(20, 9), (20, 9), (30, 5), (30, 5)], start=1):
        _img(db_session, i)
        _link(db_session, tag, i)
        _rate(db_session, 500, i, rating)
    await db_session.commit()

    await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    rows = await _rows(db_session)
    assert rows[(500, 20)].rating_delta == pytest.approx(2.0)
    assert rows[(500, 30)].rating_delta == pytest.approx(-2.0)
    assert rows[(500, 20)].affinity == pytest.approx(1.0)
    assert rows[(500, 30)].affinity == pytest.approx(-1.0)  # negative rows are KEPT


async def test_min_support_gates_rows(db_session):
    # Tag 20 has pool support 2 (kept, min_support=2); tag 30 appears once (dropped).
    _tag(db_session, 20, TagType.SOURCE, "Kept")
    _tag(db_session, 30, TagType.THEME, "Dropped")
    for i in (1, 2):
        _img(db_session, i)
        _link(db_session, 20, i)
    _img(db_session, 3)
    _link(db_session, 30, 3)
    for i in (1, 2, 3):
        _fav(db_session, 500, i)
    await db_session.commit()

    await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    rows = await _rows(db_session)
    assert (500, 20) in rows
    assert (500, 30) not in rows


async def test_min_events_excludes_light_users(db_session):
    # User 500 has 3 events (profiled); user 600 has only 2 (not profiled).
    _tag(db_session, 20, TagType.SOURCE, "S")
    for i in (1, 2, 3):
        _img(db_session, i)
        _link(db_session, 20, i)
    for i in (1, 2, 3):
        _fav(db_session, 500, i)
    for i in (1, 2):
        _fav(db_session, 600, i)
    await db_session.commit()

    await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    rows = await _rows(db_session)
    assert (500, 20) in rows
    assert all(uid != 600 for uid, _ in rows)


async def test_alias_links_resolve_to_canonical(db_session):
    # Tag 21 is an alias of 20; links via 21 count toward canonical 20.
    _tag(db_session, 20, TagType.SOURCE, "Canonical")
    _tag(db_session, 21, TagType.SOURCE, "Alias", alias_of=20)
    for i in (1, 2):
        _img(db_session, i)
    _link(db_session, 20, 1)
    _link(db_session, 21, 2)
    for i in (1, 2):
        _fav(db_session, 500, i)
    _rate(db_session, 500, 1, 8)  # 3rd event
    await db_session.commit()

    await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    rows = await _rows(db_session)
    assert rows[(500, 20)].pool_cnt == 2
    assert (500, 21) not in rows


async def test_invisible_images_excluded(db_session):
    # DEACTIVATED image favorites don't count toward the pool.
    _tag(db_session, 20, TagType.SOURCE, "S")
    for i in (1, 2):
        _img(db_session, i)
        _link(db_session, 20, i)
    for i in (3, 4):
        _img(db_session, i, status=ImageStatus.DEACTIVATED)
        _link(db_session, 20, i)
    for i in (1, 2, 3, 4):
        _fav(db_session, 500, i)
    await db_session.commit()

    await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    rows = await _rows(db_session)
    assert rows[(500, 20)].pool_cnt == 2


async def test_batching_covers_users_across_batches(db_session):
    # batch_size=1 forces one INSERT per user; both users must land.
    _tag(db_session, 20, TagType.SOURCE, "S")
    for i in (1, 2, 3):
        _img(db_session, i)
        _link(db_session, 20, i)
    for uid in (500, 600):
        for i in (1, 2, 3):
            _fav(db_session, uid, i)
    await db_session.commit()

    kw = dict(REFRESH_KW)
    kw["batch_size"] = 1
    await refresh_user_tag_affinity(db_session, **kw)
    rows = await _rows(db_session)
    assert (500, 20) in rows and (600, 20) in rows


async def test_rerun_is_idempotent(db_session):
    _tag(db_session, 20, TagType.SOURCE, "S")
    for i in (1, 2, 3):
        _img(db_session, i)
        _link(db_session, 20, i)
        _fav(db_session, 500, i)
    await db_session.commit()

    n1 = await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    n2 = await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    assert n1 == n2 > 0
    rows = await _rows(db_session)
    assert rows[(500, 20)].pool_cnt == 3


async def test_lock_skip_returns_sentinel(db_session, db_engine):
    # A second connection holds the lock -> refresh skips with -1.
    from sqlalchemy import text as sqla_text

    db_name = (await db_session.execute(sqla_text("SELECT DATABASE()"))).scalar()
    lock_name = f"{_LOCK_PREFIX}:{db_name}"
    async with db_engine.connect() as other:
        got = (await other.execute(sqla_text("SELECT GET_LOCK(:n, 0)"), {"n": lock_name})).scalar()
        assert got == 1
        n = await refresh_user_tag_affinity(db_session, **REFRESH_KW)
        assert n == -1
        await other.execute(sqla_text("SELECT RELEASE_LOCK(:n)"), {"n": lock_name})
```

NOTE for the implementer: check `tests/conftest.py` for the engine fixture name — if there is no `db_engine` fixture, look at how the co-occurrence-style tests or `client_real_redis` obtain a second connection, or create one inline with `create_async_engine(settings.DATABASE_URL)` scoped to the test (dispose in a `finally`). The assertion logic stays the same.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/services/test_user_tag_affinity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.user_tag_affinity'` (the Task-1 roundtrip test still passes)

- [ ] **Step 3: Implement the refresh service**

Create `app/services/user_tag_affinity.py`:

```python
"""Refresh the precomputed per-user tag-affinity table (taste profiles).

For each eligible user (>= min_events favorites+ratings+uploads) and each tag
with minimum support, stores positive-pool counts (favorites ∪ uploads,
deduped), rating stats, a popularity-normalized lift, a per-user-mean-centered
rating delta, and the blended affinity used by /images/recommended.

Mirrors app/services/tag_cooccurrence.py on the unmerged co-occurrence branch:
materialized regular helper tables (MariaDB cannot self-join a TEMPORARY table),
a database-scoped advisory lock, and an atomic staging-table swap. The main
aggregation is batched by user-id ranges — the unbatched join is ~75M
intermediate rows (5.7M favorites × ~13 tags/image), the exact shape that
OOM-crashed MariaDB during the co-occurrence build. Issues DDL with implicit
commits and manages its own transaction. The swapped-in table intentionally has
NO foreign keys (CREATE TABLE ... LIKE does not copy them); acceptable for a
read-only analytics table and avoids FK-check overhead on bulk load.
"""

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES

logger = get_logger(__name__)

_PUBLIC = list(PUBLIC_IMAGE_STATUSES)
_HELPERS = ("_taste_vl", "_taste_vi", "_taste_vc", "_taste_elig", "_taste_pool", "_taste_users")
# Advisory-lock names are server-global; scope to the current database so
# pytest-xdist per-worker DBs get independent locks while production's single
# DB still serializes cron + manual runs (same reasoning as tag_cooccurrence).
_LOCK_PREFIX = "user_tag_affinity_refresh"

# Each axis contributes only when it has enough support on its own; NULL-safe
# via COALESCE. lift > 0 is guaranteed inside its CASE (pool_cnt >= min_support
# implies a positive numerator).
_BATCH_INSERT = """
INSERT INTO user_tag_affinity_new
    (user_id, tag_id, pool_cnt, fav_count, upload_count, rated_count,
     rating_avg, lift, rating_delta, affinity)
SELECT
    agg.user_id, agg.tag_id, agg.pool_cnt, agg.fav_count, agg.upload_count, agg.rated_count,
    agg.rating_sum / NULLIF(agg.rated_count, 0) AS rating_avg,
    CASE WHEN agg.pool_cnt > 0 AND u.pool_size > 0
         THEN (agg.pool_cnt / u.pool_size) / ((vc.vc + :k) / :n) END AS lift,
    agg.rating_sum / NULLIF(agg.rated_count, 0) - u.user_mean AS rating_delta,
    COALESCE(CASE WHEN agg.pool_cnt >= :min_support
                  THEN LN((agg.pool_cnt / u.pool_size) / ((vc.vc + :k) / :n)) END, 0)
    + :beta * COALESCE(CASE WHEN agg.rated_count >= :min_support
                            THEN agg.rating_sum / agg.rated_count - u.user_mean END, 0)
      AS affinity
FROM (
    SELECT y.user_id, y.tag_id,
           SUM(y.pool) AS pool_cnt, SUM(y.fav) AS fav_count, SUM(y.upl) AS upload_count,
           SUM(y.rated) AS rated_count, SUM(y.rsum) AS rating_sum
    FROM (
        SELECT p.user_id, vl.tag_id,
               1 AS pool, p.is_fav AS fav, p.is_upl AS upl, 0 AS rated, 0 AS rsum
        FROM _taste_pool p JOIN _taste_vl vl ON vl.image_id = p.image_id
        WHERE p.user_id BETWEEN :lo AND :hi
        UNION ALL
        SELECT r.user_id, vl.tag_id, 0, 0, 0, 1, r.rating
        FROM image_ratings r
        JOIN _taste_elig e ON e.user_id = r.user_id
        JOIN _taste_vl vl ON vl.image_id = r.image_id
        WHERE r.user_id BETWEEN :lo AND :hi
    ) y
    GROUP BY y.user_id, y.tag_id
    HAVING SUM(y.pool) >= :min_support OR SUM(y.rated) >= :min_support
) agg
JOIN _taste_users u ON u.user_id = agg.user_id
JOIN _taste_vc vc ON vc.tag_id = agg.tag_id
"""


async def _exec(db, sql, params=None):
    # A "public_statuses" key in `params` is expanded as an IN list.
    stmt = text(sql)
    if params and "public_statuses" in params:
        stmt = stmt.bindparams(bindparam("public_statuses", expanding=True))
    await db.execute(stmt, params or {})


async def refresh_user_tag_affinity(
    db: AsyncSession,
    *,
    min_support: int,
    smoothing_k: int,
    beta: float,
    min_events: int,
    batch_size: int,
) -> int:
    """Rebuild the user_tag_affinity table; return the number of rows written.

    Serialized by a connection-scoped MySQL named lock so the nightly cron and a
    manual run cannot collide. Returns the sentinel ``-1`` (callers should treat
    ``< 0`` as "skipped") without touching any tables if another refresh already
    holds the lock.
    """
    db_name = (await db.execute(text("SELECT DATABASE()"))).scalar()
    lock_name = f"{_LOCK_PREFIX}:{db_name}"
    locked = (await db.execute(text("SELECT GET_LOCK(:n, 0)"), {"n": lock_name})).scalar()
    if not locked:
        logger.info("user_tag_affinity_refresh_skipped_locked")
        return -1
    try:
        for t in _HELPERS:
            await _exec(db, f"DROP TABLE IF EXISTS {t}")

        # 1. canonical, visible links
        await _exec(
            db,
            """
            CREATE TABLE _taste_vl ENGINE=InnoDB AS
            SELECT DISTINCT tl.image_id, COALESCE(t.alias_of, t.tag_id) AS tag_id
            FROM tag_links tl
            JOIN images i ON i.image_id = tl.image_id
            JOIN tags   t ON t.tag_id   = tl.tag_id
            WHERE i.status IN :public_statuses
            """,
            {"public_statuses": _PUBLIC},
        )
        await _exec(
            db, "ALTER TABLE _taste_vl ADD PRIMARY KEY (image_id, tag_id), ADD KEY k_tag (tag_id)"
        )

        # 2. visible tagged images, per-canonical-tag counts, and N
        await _exec(
            db, "CREATE TABLE _taste_vi ENGINE=InnoDB AS SELECT DISTINCT image_id FROM _taste_vl"
        )
        await _exec(db, "ALTER TABLE _taste_vi ADD PRIMARY KEY (image_id)")
        await _exec(
            db,
            "CREATE TABLE _taste_vc ENGINE=InnoDB AS "
            "SELECT tag_id, COUNT(*) AS vc FROM _taste_vl GROUP BY tag_id",
        )
        await _exec(db, "ALTER TABLE _taste_vc ADD PRIMARY KEY (tag_id)")
        n = (await db.execute(text("SELECT COUNT(*) FROM _taste_vi"))).scalar() or 0

        # 3. eligible users (raw event counts)
        await _exec(
            db,
            """
            CREATE TABLE _taste_elig ENGINE=InnoDB AS
            SELECT user_id FROM (
                SELECT user_id FROM favorites
                UNION ALL SELECT user_id FROM image_ratings
                UNION ALL SELECT user_id FROM images WHERE user_id IS NOT NULL
            ) ev GROUP BY user_id HAVING COUNT(*) >= :min_events
            """,
            {"min_events": min_events},
        )
        await _exec(db, "ALTER TABLE _taste_elig ADD PRIMARY KEY (user_id)")

        # 4. deduped positive pool (favorites ∪ uploads), visible only
        await _exec(
            db,
            """
            CREATE TABLE _taste_pool ENGINE=InnoDB AS
            SELECT x.user_id, x.image_id, MAX(x.is_fav) AS is_fav, MAX(x.is_upl) AS is_upl
            FROM (
                SELECT f.user_id, f.image_id, 1 AS is_fav, 0 AS is_upl
                FROM favorites f JOIN _taste_elig e ON e.user_id = f.user_id
                UNION ALL
                SELECT i.user_id, i.image_id, 0 AS is_fav, 1 AS is_upl
                FROM images i JOIN _taste_elig e ON e.user_id = i.user_id
            ) x JOIN _taste_vi vi ON vi.image_id = x.image_id
            GROUP BY x.user_id, x.image_id
            """,
        )
        await _exec(db, "ALTER TABLE _taste_pool ADD PRIMARY KEY (user_id, image_id)")

        # 5. per-user scalars (pool size, mean rating over visible images)
        await _exec(
            db,
            """
            CREATE TABLE _taste_users ENGINE=InnoDB AS
            SELECT e.user_id,
                (SELECT COUNT(*) FROM _taste_pool p WHERE p.user_id = e.user_id) AS pool_size,
                (SELECT AVG(r.rating) FROM image_ratings r
                  JOIN _taste_vi vi ON vi.image_id = r.image_id
                  WHERE r.user_id = e.user_id) AS user_mean
            FROM _taste_elig e
            """,
        )
        await _exec(db, "ALTER TABLE _taste_users ADD PRIMARY KEY (user_id)")

        # 6. staging table (LIKE copies PK + lookup index + defaults)
        await _exec(db, "DROP TABLE IF EXISTS user_tag_affinity_new")
        await _exec(db, "CREATE TABLE user_tag_affinity_new LIKE user_tag_affinity")

        # 7. batched aggregation: contiguous user-id ranges over the sorted
        #    eligible ids, so BETWEEN lo AND hi covers exactly one chunk.
        user_ids = [
            r[0]
            for r in (
                await db.execute(text("SELECT user_id FROM _taste_elig ORDER BY user_id"))
            ).all()
        ]
        for start in range(0, len(user_ids), batch_size):
            chunk = user_ids[start : start + batch_size]
            await _exec(
                db,
                _BATCH_INSERT,
                {
                    "lo": chunk[0],
                    "hi": chunk[-1],
                    "k": smoothing_k,
                    "n": n,
                    "beta": beta,
                    "min_support": min_support,
                },
            )

        n_rows = (
            await db.execute(text("SELECT COUNT(*) FROM user_tag_affinity_new"))
        ).scalar() or 0

        # 8. atomic swap, then clean up
        await _exec(db, "DROP TABLE IF EXISTS user_tag_affinity_old")
        await _exec(
            db,
            "RENAME TABLE user_tag_affinity TO user_tag_affinity_old, "
            "user_tag_affinity_new TO user_tag_affinity",
        )
        await _exec(db, "DROP TABLE user_tag_affinity_old")
        for t in _HELPERS:
            await _exec(db, f"DROP TABLE IF EXISTS {t}")
        await db.commit()
        return n_rows
    finally:
        # Connection-scoped lock survives the DDL implicit-commits on this session.
        try:
            await db.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": lock_name})
        except Exception:
            # lock auto-releases on connection close; never mask the real exception
            pass
```

Edge case the implementer must NOT "fix": when `n == 0` (empty DB) the lift divisor `(vc + :k) / :n` divides by zero — but `_taste_vl` is then empty, so no pool/rating rows join to it and the INSERT writes nothing. MariaDB division-by-zero inside a dead branch never evaluates. If a test surfaces a real division error, guard `n = max(n, 1)` — do not restructure the SQL.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/services/test_user_tag_affinity.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/user_tag_affinity.py tests/services/test_user_tag_affinity.py
git commit -m "feat(taste): refresh service — lift + centered rating delta + blended affinity"
```

---

### Task 3: arq cron task, manual script, dev verification run

**Files:**
- Create: `app/tasks/taste_profile.py`
- Modify: `app/tasks/worker.py` (register function + cron)
- Create: `scripts/refresh_user_tag_affinity.py`

**Interfaces:**
- Consumes: `refresh_user_tag_affinity` (Task 2), `settings.TASTE_*` (Task 1).
- Produces: `refresh_user_tag_affinity_job(ctx)` arq task; nightly cron at 05:00 UTC.

No new pytest coverage in this task: the wrapper is glue identical in shape to the existing `cleanup_stale_accounts` cron pattern, and the service underneath is fully tested in Task 2. Verification is a REAL run against the dev DB (Step 4), which exercises the script end-to-end on 5.7M favorites.

- [ ] **Step 1: Create the task module**

Create `app/tasks/taste_profile.py`:

```python
"""Arq task for the nightly user-taste-profile refresh."""

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


async def refresh_user_tag_affinity_job(ctx: dict[str, Any]) -> None:
    """
    Nightly refresh of the user_tag_affinity table (05:00 UTC).

    Skips silently if another refresh is already running (advisory lock not
    acquired). ~15 minutes on production-scale data, batched to keep MariaDB
    memory bounded.
    """
    from app.config import settings
    from app.core.database import get_async_session
    from app.services.user_tag_affinity import refresh_user_tag_affinity

    async with get_async_session() as db:
        try:
            n = await refresh_user_tag_affinity(
                db,
                min_support=settings.TASTE_MIN_SUPPORT,
                smoothing_k=settings.TASTE_SMOOTHING_K,
                beta=settings.TASTE_RATING_BETA,
                min_events=settings.TASTE_MIN_EVENTS,
                batch_size=settings.TASTE_BATCH_SIZE,
            )
        except Exception as e:
            logger.exception(
                "user_tag_affinity_refresh_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            return

    if n < 0:
        logger.info("user_tag_affinity_refresh_skipped_lock_held")
    else:
        logger.info("user_tag_affinity_refreshed", rows=n)
```

- [ ] **Step 2: Register in the worker**

In `app/tasks/worker.py`:
- Add import: `from app.tasks.taste_profile import refresh_user_tag_affinity_job`
- Append to `WorkerSettings.functions`: `func(refresh_user_tag_affinity_job, max_tries=1),`
- Append to `WorkerSettings.cron_jobs`: `cron(refresh_user_tag_affinity_job, hour=5, minute=0),  # nightly, 05:00 UTC`

NOTE: the refresh takes ~15 min but `WorkerSettings.job_timeout = 300` (5 min) would kill it. Pass a per-function timeout instead: `func(refresh_user_tag_affinity_job, max_tries=1, timeout=3600)`. arq's `func()` accepts `timeout`; verify against the installed arq version and, if the kwarg differs, set it on the cron entry (`cron(..., timeout=3600)`).

- [ ] **Step 3: Create the manual script**

Create `scripts/refresh_user_tag_affinity.py`:

```python
#!/usr/bin/env python3
"""
Manually trigger a full rebuild of the user_tag_affinity table.

Usage:
    uv run python scripts/refresh_user_tag_affinity.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.services.user_tag_affinity import refresh_user_tag_affinity


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        n = await refresh_user_tag_affinity(
            db,
            min_support=settings.TASTE_MIN_SUPPORT,
            smoothing_k=settings.TASTE_SMOOTHING_K,
            beta=settings.TASTE_RATING_BETA,
            min_events=settings.TASTE_MIN_EVENTS,
            batch_size=settings.TASTE_BATCH_SIZE,
        )

    await engine.dispose()

    if n < 0:
        print("Skipped: another refresh is already running.")
    else:
        print(f"Refreshed user_tag_affinity: {n} rows")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Verification run against the dev DB**

Run: `uv run python scripts/refresh_user_tag_affinity.py`
Expected: `Refreshed user_tag_affinity: <~1-2M> rows` in ~10–20 minutes.

Then sanity-check a known profile (whitekitten, user 59006 — validated during design):

```bash
docker exec shuushuu-mariadb-dev mariadb -u shuushuu_dev -pdev_password_insecure shuushuu_dev -e "
SELECT t.title, a.pool_cnt, ROUND(a.lift,1) lift, ROUND(a.affinity,2) aff
FROM user_tag_affinity a JOIN tags t ON t.tag_id=a.tag_id
WHERE a.user_id=59006 ORDER BY a.affinity DESC LIMIT 10;"
```

Expected: Code Geass / C.C. / Annin Doufu / The Idolm@ster Cinderella Girls near the top with lift ≫ 1.5; total rows for the user in the low thousands.

- [ ] **Step 5: Commit**

```bash
git add app/tasks/taste_profile.py app/tasks/worker.py scripts/refresh_user_tag_affinity.py
git commit -m "feat(taste): nightly refresh cron + manual runner"
```

---

### Task 4: GET /users/me/taste-profile

**Files:**
- Create: `app/schemas/taste_profile.py`
- Modify: `app/api/v1/users.py` (new route — MUST be declared with the other `/me` routes, i.e. BEFORE the `/{user_id}` route around line 714, or FastAPI matches "me" as a user_id)
- Test: `tests/api/v1/test_taste_profile.py`

**Interfaces:**
- Consumes: `UserTagAffinity` (Task 1), `settings.TASTE_DISPLAY_MIN_LIFT`, `settings.TASTE_MIN_SUPPORT`.
- Produces: `TasteProfileResponse`, `TasteProfileTag`, `TasteProfileSummary` schemas (frontend consumes via generated OpenAPI types).

- [ ] **Step 1: Write the failing endpoint tests**

Create `tests/api/v1/test_taste_profile.py`:

```python
import pytest

from app.config import TagType
from app.models.tag import Tags
from app.models.user_tag_affinity import UserTagAffinity

pytestmark = [pytest.mark.api]


def _aff(db, user_id, tag_id, *, pool_cnt=0, fav=0, upl=0, rated=0,
         rating_avg=None, lift=None, delta=None, affinity=0.0):
    db.add(UserTagAffinity(
        user_id=user_id, tag_id=tag_id, pool_cnt=pool_cnt, fav_count=fav,
        upload_count=upl, rated_count=rated, rating_avg=rating_avg,
        lift=lift, rating_delta=delta, affinity=affinity,
    ))


async def test_requires_auth(client):
    resp = await client.get("/api/v1/users/me/taste-profile")
    assert resp.status_code == 401


async def test_cold_start_profile_not_ready(authenticated_client):
    resp = await authenticated_client.get("/api/v1/users/me/taste-profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_ready"] is False
    assert data["top_tags"] == []
    assert data["rated_high"] == []
    assert data["rated_low"] == []


async def test_profile_payload(db_session, authenticated_client, sample_user):
    uid = sample_user.user_id
    db_session.add(Tags(tag_id=201, type=TagType.SOURCE, title="Code Geass"))
    db_session.add(Tags(tag_id=202, type=TagType.THEME, title="long hair"))
    db_session.add(Tags(tag_id=203, type=TagType.THEME, title="chibi"))
    # strong positive: high lift + positive delta
    _aff(db_session, uid, 201, pool_cnt=100, fav=90, upl=20, rated=50,
         rating_avg=9.0, lift=12.0, delta=2.0, affinity=3.5)
    # popularity-only: lift below the 1.5 display floor -> excluded from top_tags
    _aff(db_session, uid, 202, pool_cnt=500, fav=500, lift=1.3, affinity=0.26)
    # disliked: negative delta -> rated_low
    _aff(db_session, uid, 203, rated=30, rating_avg=5.0, lift=None,
         delta=-2.0, affinity=-1.0)
    await db_session.commit()

    resp = await authenticated_client.get("/api/v1/users/me/taste-profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_ready"] is True
    top_ids = [t["tag_id"] for t in data["top_tags"]]
    assert top_ids == [201]  # 202 fails the lift floor; 203 has no pool support
    top = data["top_tags"][0]
    assert top["title"] == "Code Geass"
    assert top["type_name"] == "Source"
    assert top["lift"] == pytest.approx(12.0)
    high_ids = [t["tag_id"] for t in data["rated_high"]]
    low_ids = [t["tag_id"] for t in data["rated_low"]]
    assert high_ids == [201]   # only positive deltas
    assert low_ids == [203]    # only negative deltas
    assert data["summary"]["mean_rating"] is None or isinstance(data["summary"]["mean_rating"], float)


async def test_other_users_rows_not_leaked(db_session, authenticated_client, sample_user):
    db_session.add(Tags(tag_id=201, type=TagType.SOURCE, title="S"))
    _aff(db_session, sample_user.user_id + 1, 201, pool_cnt=100, lift=10.0, affinity=2.3)
    await db_session.commit()

    resp = await authenticated_client.get("/api/v1/users/me/taste-profile")
    assert resp.json()["profile_ready"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_taste_profile.py -v`
Expected: FAIL — 404 (route doesn't exist) on all but `test_requires_auth` (which may also 404; adjust expectations only if the route exists)

- [ ] **Step 3: Create the schemas**

Create `app/schemas/taste_profile.py`:

```python
"""Pydantic schemas for the private user taste-profile endpoint"""

from pydantic import BaseModel

from app.schemas.base import UTCDatetime


class TasteProfileTag(BaseModel):
    """One tag's evidence in a user's taste profile."""

    tag_id: int
    title: str | None
    type: int
    type_name: str
    pool_cnt: int
    fav_count: int
    upload_count: int
    rated_count: int
    rating_avg: float | None
    lift: float | None
    rating_delta: float | None
    affinity: float


class TasteProfileSummary(BaseModel):
    """Aggregate stats shown above the tag lists."""

    pool_size: int  # favorites ∪ uploads (deduped)
    rated_total: int
    mean_rating: float | None
    updated_at: UTCDatetime | None


class TasteProfileResponse(BaseModel):
    """Private analytics payload; owner-only."""

    profile_ready: bool
    summary: TasteProfileSummary | None = None
    top_tags: list[TasteProfileTag] = []
    rated_high: list[TasteProfileTag] = []
    rated_low: list[TasteProfileTag] = []
```

NOTE: if `app/schemas/base.py` has no `UTCDatetime`, check its actual export name (`from app.schemas.base import UTCDatetime` is used by `app/schemas/image.py`; copy whatever that file imports).

- [ ] **Step 4: Implement the endpoint**

In `app/api/v1/users.py`, next to the other `/me` routes (after `GET /me/warnings`, BEFORE `/{user_id}`):

```python
@router.get("/me/taste-profile", response_model=TasteProfileResponse)
async def get_taste_profile(
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_db),
) -> TasteProfileResponse:
    """
    The logged-in user's taste profile (private, owner-only).

    Reads the precomputed user_tag_affinity table (refreshed nightly).
    top_tags applies the TASTE_DISPLAY_MIN_LIFT floor so popularity-only tags
    (e.g. "long hair" at lift ~1.3) don't crowd out actual taste; rated_high /
    rated_low are the largest positive / negative per-user-centered rating
    deltas with rating support.
    """
    from sqlalchemy import func, text

    from app.models.user_tag_affinity import UserTagAffinity
    from app.services.feeds import TAG_TYPE_NAME

    # profile_ready = any rows exist. Do NOT key this on updated_at: ORM
    # inserts (tests) send explicit NULL there; only the refresh job's raw
    # INSERT…SELECT gets the server default.
    has_rows = (
        await db.execute(
            select(UserTagAffinity.user_id)
            .where(UserTagAffinity.user_id == current_user_id)
            .limit(1)
        )
    ).first()
    if has_rows is None:
        return TasteProfileResponse(profile_ready=False)
    updated_at = (
        await db.execute(
            select(func.max(UserTagAffinity.updated_at)).where(
                UserTagAffinity.user_id == current_user_id
            )
        )
    ).scalar()

    def _mk(row) -> TasteProfileTag:
        aff, title, ttype = row
        return TasteProfileTag(
            tag_id=aff.tag_id,
            title=title,
            type=ttype,
            type_name=TAG_TYPE_NAME.get(ttype, "Tag"),
            pool_cnt=aff.pool_cnt,
            fav_count=aff.fav_count,
            upload_count=aff.upload_count,
            rated_count=aff.rated_count,
            rating_avg=aff.rating_avg,
            lift=aff.lift,
            rating_delta=aff.rating_delta,
            affinity=aff.affinity,
        )

    base = (
        select(UserTagAffinity, Tags.title, Tags.type)
        .join(Tags, Tags.tag_id == UserTagAffinity.tag_id)
        .where(UserTagAffinity.user_id == current_user_id)
    )
    top_rows = (
        await db.execute(
            base.where(
                UserTagAffinity.pool_cnt >= settings.TASTE_MIN_SUPPORT,
                UserTagAffinity.lift >= settings.TASTE_DISPLAY_MIN_LIFT,
                UserTagAffinity.affinity > 0,
            )
            .order_by(desc(UserTagAffinity.affinity))
            .limit(40)
        )
    ).all()
    high_rows = (
        await db.execute(
            base.where(
                UserTagAffinity.rated_count >= settings.TASTE_MIN_SUPPORT,
                UserTagAffinity.rating_delta > 0,
            )
            .order_by(desc(UserTagAffinity.rating_delta))
            .limit(10)
        )
    ).all()
    low_rows = (
        await db.execute(
            base.where(
                UserTagAffinity.rated_count >= settings.TASTE_MIN_SUPPORT,
                UserTagAffinity.rating_delta < 0,
            )
            .order_by(UserTagAffinity.rating_delta)
            .limit(10)
        )
    ).all()

    pool_size = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM ("
                "SELECT image_id FROM favorites WHERE user_id = :u "
                "UNION SELECT image_id FROM images WHERE user_id = :u) p"
            ),
            {"u": current_user_id},
        )
    ).scalar() or 0
    rated_row = (
        await db.execute(
            text("SELECT COUNT(*) c, AVG(rating) m FROM image_ratings WHERE user_id = :u"),
            {"u": current_user_id},
        )
    ).one()

    return TasteProfileResponse(
        profile_ready=True,
        summary=TasteProfileSummary(
            pool_size=pool_size,
            rated_total=rated_row.c or 0,
            mean_rating=float(rated_row.m) if rated_row.m is not None else None,
            updated_at=updated_at,
        ),
        top_tags=[_mk(r) for r in top_rows],
        rated_high=[_mk(r) for r in high_rows],
        rated_low=[_mk(r) for r in low_rows],
    )
```

Move the local imports to the module top, merging with existing imports in `users.py` (`select`, `desc`, `settings`, `Tags` are almost certainly already imported there — check before adding duplicates). Add `from app.schemas.taste_profile import TasteProfileResponse, TasteProfileSummary, TasteProfileTag`. If `app.services.feeds.TAG_TYPE_NAME` does not exist on this branch, grep for `TAG_TYPE_NAME` and import from wherever it lives; if absent entirely, define `TAG_TYPE_NAME = {1: "Theme", 2: "Source", 3: "Artist", 4: "Character"}` in `app/schemas/taste_profile.py` and import that.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_taste_profile.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add app/schemas/taste_profile.py app/api/v1/users.py tests/api/v1/test_taste_profile.py
git commit -m "feat(taste): GET /users/me/taste-profile — private analytics endpoint"
```

---

### Task 5: GET /images/recommended

**Files:**
- Create: `app/services/recommendations.py`
- Modify: `app/schemas/image.py` (two schemas at the end of the file)
- Modify: `app/api/v1/images.py` (new route — MUST be declared BEFORE the `GET /{image_id}` route; `/{image_id}` has an int path converter, so `/images/recommended` would 422 if declared after)
- Test: `tests/api/v1/test_images_recommended.py`

**Interfaces:**
- Consumes: `UserTagAffinity` (Task 1), `settings.TASTE_TOP_TAGS / TASTE_CANDIDATE_CAP / TASTE_FEED_POOL`, `PUBLIC_IMAGE_STATUSES`, `TagSummary` + `ImageDetailedResponse` from `app.schemas.image`.
- Produces: `RecommendedImagesResponse` (standard list envelope + `profile_ready`), `RecommendedImageResponse` (= `ImageDetailedResponse` + `because_tags`); service `get_recommended_images(db, user, *, page, per_page) -> RecommendationPage`.

- [ ] **Step 1: Write the failing endpoint tests**

Create `tests/api/v1/test_images_recommended.py`:

```python
import pytest

from app.config import ImageStatus, TagType
from app.models.favorite import Favorites
from app.models.image import Images
from app.models.image_rating import ImageRatings
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user_tag_affinity import UserTagAffinity

pytestmark = [pytest.mark.api]


def _img(db, image_id, user_id, status=ImageStatus.ACTIVE):
    db.add(Images(image_id=image_id, user_id=user_id, ext="jpg", status=status))


def _aff(db, user_id, tag_id, affinity, pool_cnt=10):
    db.add(UserTagAffinity(
        user_id=user_id, tag_id=tag_id, pool_cnt=pool_cnt, fav_count=pool_cnt,
        upload_count=0, rated_count=0, rating_avg=None,
        lift=5.0 if affinity > 0 else None, rating_delta=None, affinity=affinity,
    ))


@pytest.fixture
async def rec_world(db_session, sample_user, test_user):
    """3 images by test_user; sample_user loves tag 301 (+2), dislikes 302 (−3)."""
    db_session.add(Tags(tag_id=301, type=TagType.SOURCE, title="Loved"))
    db_session.add(Tags(tag_id=302, type=TagType.THEME, title="Hated"))
    _img(db_session, 9001, test_user.user_id)          # tags: 301        -> score +2
    _img(db_session, 9002, test_user.user_id)          # tags: 301, 302   -> score −1
    _img(db_session, 9003, test_user.user_id)          # tags: 302        -> not a candidate
    for iid, tags in [(9001, [301]), (9002, [301, 302]), (9003, [302])]:
        for t in tags:
            db_session.add(TagLinks(tag_id=t, image_id=iid, user_id=test_user.user_id))
    _aff(db_session, sample_user.user_id, 301, 2.0)
    _aff(db_session, sample_user.user_id, 302, -3.0)
    await db_session.commit()
    return sample_user


async def test_requires_auth(client):
    resp = await client.get("/api/v1/images/recommended")
    assert resp.status_code == 401


async def test_cold_start(authenticated_client):
    resp = await authenticated_client.get("/api/v1/images/recommended")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_ready"] is False
    assert data["images"] == []
    assert data["total"] == 0


async def test_scored_order_and_because_tags(authenticated_client, rec_world):
    resp = await authenticated_client.get("/api/v1/images/recommended")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_ready"] is True
    ids = [im["image_id"] for im in data["images"]]
    # 9001 (+2.0) ranks above 9002 (2.0 − 3.0 = −1.0); 9003 never becomes a
    # candidate (only carries a negative-affinity tag).
    assert ids == [9001, 9002]
    because = data["images"][0]["because_tags"]
    assert [t["tag_id"] for t in because] == [301]
    assert because[0]["title"] == "Loved"


async def test_excludes_seen_and_own_images(db_session, authenticated_client, rec_world):
    uid = rec_world.user_id
    db_session.add(Favorites(user_id=uid, image_id=9001))       # favorited -> excluded
    db_session.add(ImageRatings(user_id=uid, image_id=9002, rating=8))  # rated -> excluded
    _img(db_session, 9004, uid)                                  # own upload -> excluded
    db_session.add(TagLinks(tag_id=301, image_id=9004, user_id=uid))
    await db_session.commit()

    resp = await authenticated_client.get("/api/v1/images/recommended")
    ids = [im["image_id"] for im in resp.json()["images"]]
    assert ids == []


async def test_excludes_hidden_statuses(db_session, authenticated_client, rec_world, test_user):
    _img(db_session, 9005, test_user.user_id, status=ImageStatus.DEACTIVATED)
    db_session.add(TagLinks(tag_id=301, image_id=9005, user_id=test_user.user_id))
    await db_session.commit()

    resp = await authenticated_client.get("/api/v1/images/recommended")
    ids = [im["image_id"] for im in resp.json()["images"]]
    assert 9005 not in ids


async def test_pagination_slices_scored_list(authenticated_client, rec_world):
    resp = await authenticated_client.get("/api/v1/images/recommended?page=2&per_page=1")
    data = resp.json()
    assert data["total"] == 2
    assert [im["image_id"] for im in data["images"]] == [9002]
```

NOTE: `sample_user.show_all_images` — if the fixture user has `show_all_images == 1`, the DEACTIVATED exclusion test would fail by design. Check the fixture; if needed, set `sample_user.show_all_images = 0; db_session.add(sample_user); await db_session.commit()` inside `test_excludes_hidden_statuses` before the request.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_images_recommended.py -v`
Expected: FAIL — 404/422 (route doesn't exist)

- [ ] **Step 3: Add the response schemas**

At the end of `app/schemas/image.py`:

```python
class RecommendedImageResponse(ImageDetailedResponse):
    """A recommended image plus the profile tags that most contributed to its score."""

    because_tags: list[TagSummary] = []


class RecommendedImagesResponse(BaseModel):
    """Personalized feed envelope (standard list shape + profile_ready flag)."""

    total: int
    page: int
    per_page: int
    profile_ready: bool
    images: list[RecommendedImageResponse]
```

- [ ] **Step 4: Implement the recommendation service**

Create `app/services/recommendations.py`:

```python
"""Live scoring for the personalized /images/recommended feed.

Reads the nightly-precomputed user_tag_affinity profile and scores a capped,
recency-biased candidate set at request time (measured ≈49 ms for the heaviest
profile on production-scale data). Negative-affinity tags subtract from an
image's score, so the feed actively avoids content the user routinely
down-rates — not just fails to boost it.
"""

from dataclasses import dataclass, field

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, settings
from app.models.user import Users
from app.models.user_tag_affinity import UserTagAffinity
from app.schemas.image import TagSummary
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES


@dataclass
class RecommendationPage:
    total: int
    image_ids: list[int]  # this page's ids, score-ordered
    because: dict[int, list[TagSummary]] = field(default_factory=dict)
    profile_ready: bool = False


async def get_recommended_images(
    db: AsyncSession, user: Users, *, page: int, per_page: int
) -> RecommendationPage:
    """Score candidates against the user's profile; return one page of image ids.

    Pipeline: top-K positive-affinity tags -> recency-biased candidate images
    carrying any of them (capped) -> sum affinity over ALL profile-covered tags
    (alias-resolved; DISTINCT guards against alias+canonical double links) ->
    drop seen (favorited/rated/own) and invisible images -> order by score,
    keep the top TASTE_FEED_POOL, slice the requested page.
    """
    top_rows = (
        await db.execute(
            select(UserTagAffinity.tag_id)
            .where(UserTagAffinity.user_id == user.user_id, UserTagAffinity.affinity > 0)
            .order_by(UserTagAffinity.affinity.desc())  # type: ignore[attr-defined]
            .limit(settings.TASTE_TOP_TAGS)
        )
    ).all()
    top_tag_ids = [r[0] for r in top_rows]
    if not top_tag_ids:
        # Distinguish "no profile" (cold start) from "profile exists but has no
        # positive tags" (e.g. a user who only down-rates) — the frontend shows
        # different copy for each.
        has_rows = (
            await db.execute(
                select(UserTagAffinity.user_id)
                .where(UserTagAffinity.user_id == user.user_id)
                .limit(1)
            )
        ).first()
        return RecommendationPage(total=0, image_ids=[], profile_ready=has_rows is not None)

    show_all = user.show_all_images == 1
    status_clause = "" if show_all else "AND i.status IN :public_statuses"
    hide_reposts_clause = (
        "AND i.status != :repost_status" if user.hide_reposts == 1 else ""
    )

    sql = f"""
        SELECT d.image_id, SUM(d.affinity) AS score
        FROM (
            SELECT DISTINCT c.image_id, p.tag_id, p.affinity
            FROM (
                SELECT DISTINCT tl.image_id FROM tag_links tl
                WHERE tl.tag_id IN :top_tag_ids
                ORDER BY tl.image_id DESC
                LIMIT {int(settings.TASTE_CANDIDATE_CAP)}
            ) c
            JOIN images i ON i.image_id = c.image_id
            JOIN tag_links tl2 ON tl2.image_id = c.image_id
            JOIN tags tg ON tg.tag_id = tl2.tag_id
            JOIN user_tag_affinity p
              ON p.user_id = :uid AND p.tag_id = COALESCE(tg.alias_of, tg.tag_id)
            WHERE i.user_id != :uid
              {status_clause}
              {hide_reposts_clause}
              AND NOT EXISTS (
                  SELECT 1 FROM favorites f
                  WHERE f.user_id = :uid AND f.image_id = c.image_id)
              AND NOT EXISTS (
                  SELECT 1 FROM image_ratings r
                  WHERE r.user_id = :uid AND r.image_id = c.image_id)
        ) d
        GROUP BY d.image_id
        ORDER BY score DESC, d.image_id DESC
        LIMIT {int(settings.TASTE_FEED_POOL)}
    """
    stmt = text(sql).bindparams(bindparam("top_tag_ids", expanding=True))
    params: dict = {"top_tag_ids": top_tag_ids, "uid": user.user_id}
    if not show_all:
        stmt = stmt.bindparams(bindparam("public_statuses", expanding=True))
        params["public_statuses"] = list(PUBLIC_IMAGE_STATUSES)
    if user.hide_reposts == 1:
        params["repost_status"] = int(ImageStatus.REPOST)

    scored = (await db.execute(stmt, params)).all()
    total = len(scored)
    offset = (page - 1) * per_page
    page_ids = [r.image_id for r in scored[offset : offset + per_page]]
    if not page_ids:
        return RecommendationPage(total=total, image_ids=[], profile_ready=True)

    # top contributing (positive) profile tags per page image
    because_stmt = text(
        """
        SELECT tl.image_id, p.tag_id, p.affinity, t.title, t.type
        FROM tag_links tl
        JOIN tags tg0 ON tg0.tag_id = tl.tag_id
        JOIN user_tag_affinity p
          ON p.user_id = :uid AND p.tag_id = COALESCE(tg0.alias_of, tg0.tag_id)
        JOIN tags t ON t.tag_id = p.tag_id
        WHERE tl.image_id IN :ids AND p.affinity > 0
        """
    ).bindparams(bindparam("ids", expanding=True))
    because_rows = (
        await db.execute(because_stmt, {"uid": user.user_id, "ids": page_ids})
    ).all()
    by_image: dict[int, list] = {}
    for row in because_rows:
        by_image.setdefault(row.image_id, []).append(row)
    because = {
        iid: [
            TagSummary(tag_id=r.tag_id, title=r.title, type=r.type)
            for r in sorted(rows, key=lambda r: -r.affinity)[:3]
        ]
        for iid, rows in by_image.items()
    }
    return RecommendationPage(
        total=total, image_ids=page_ids, because=because, profile_ready=True
    )
```

NOTE: check `TagSummary`'s required fields at `app/schemas/image.py:41` — if it requires `type_name` (or others), supply them (`TAG_TYPE_NAME.get(r.type, "Tag")`, and select any extra columns the schema needs). Adjust the constructor call, not the schema.

- [ ] **Step 5: Implement the endpoint**

In `app/api/v1/images.py`, ABOVE the `GET /{image_id}` route:

```python
@router.get("/recommended", response_model=RecommendedImagesResponse)
async def get_recommended(
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> RecommendedImagesResponse:
    """
    Personalized feed: images scored against the caller's taste profile.

    Live-scored per request from the nightly-refreshed user_tag_affinity table.
    Excludes images the user favorited, rated, or uploaded; applies the standard
    status-visibility rules and the user's hide_reposts setting. Feed depth is
    capped at TASTE_FEED_POOL scored images. `profile_ready=false` with an empty
    list means the user has no profile yet (cold start) — not an error.
    """
    rec = await get_recommended_images(
        db, current_user, page=pagination.page, per_page=pagination.per_page
    )
    if not rec.image_ids:
        return RecommendedImagesResponse(
            total=rec.total,
            page=pagination.page,
            per_page=pagination.per_page,
            profile_ready=rec.profile_ready,
            images=[],
        )

    query = (
        select(Images)
        .options(
            selectinload(Images.user),  # copy the EXACT selectinload chain from
            selectinload(Images.tag_links).selectinload(TagLinks.tag),  # list_images (~line 895-925)
        )
        .where(Images.image_id.in_(rec.image_ids))
    )
    result = await db.execute(query)
    by_id = {img.image_id: img for img in result.scalars().all()}
    items: list[RecommendedImageResponse] = []
    for iid in rec.image_ids:
        img = by_id.get(iid)
        if img is None:
            continue
        item = RecommendedImageResponse.from_db_model(img, is_favorited=False)
        item.because_tags = rec.because.get(iid, [])
        items.append(item)
    return RecommendedImagesResponse(
        total=rec.total,
        page=pagination.page,
        per_page=pagination.per_page,
        profile_ready=True,
        images=items,
    )
```

Implementation notes:
- Copy the exact `selectinload(...)` option chain that `list_images` uses (`images.py` ~lines 895–925) — the two lines above are indicative, the real chain may load user groups etc. `from_db_model` needs those relationships loaded.
- `RecommendedImageResponse.from_db_model(...)` works because `from_db_model` is a classmethod constructing `cls(...)` — verify at `app/schemas/image.py:193-230`. If it hardcodes `ImageDetailedResponse(...)` instead of `cls(...)`, change it to `cls(...)` (safe: classmethod convention) rather than building a dump/re-validate dance.
- `is_favorited=False` is correct by construction: favorited images are excluded from the feed.
- Imports to add in `images.py`: `RecommendedImageResponse, RecommendedImagesResponse` (from `app.schemas.image`), `from app.services.recommendations import get_recommended_images`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_images_recommended.py -v`
Expected: ALL PASS

- [ ] **Step 7: Live smoke test against dev data** (requires Task 3's refresh run)

```bash
docker exec shuushuu-api curl -s -H "Authorization: Bearer $(docker exec shuushuu-api python -c "
from app.core.security import create_access_token; print(create_access_token(59006))")" \
  http://localhost:8000/api/v1/images/recommended | head -c 2000
```

Expected: JSON with `profile_ready: true` and images whose `because_tags` include Code Geass / C.C.-family tags. (If the container lacks curl, use `uv run python -c` with httpx inside the container, or hit it through nginx with a browser cookie — any route that shows real scored output is fine.)

- [ ] **Step 8: Commit**

```bash
git add app/services/recommendations.py app/schemas/image.py app/api/v1/images.py tests/api/v1/test_images_recommended.py
git commit -m "feat(taste): GET /images/recommended — live-scored personalized feed"
```

---

### Task 6: Full verification + PR

- [ ] **Step 1: Full test suite**

Run: `uv run pytest -n auto`
Expected: everything green (compare any failures against a `main` baseline run first — do not chase pre-existing failures, but report them).

- [ ] **Step 2: Lint + types**

Run: `uv run ruff check app tests && uv run ruff format --check app tests`
Expected: clean.
Run: `uv run mypy app`
Expected: no NEW errors vs `main` baseline (run `git stash && uv run mypy app | tail -1 && git stash pop` to get the baseline count if unsure).

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feat/user-taste-profile
gh pr create --title "feat: per-user taste profiles + recommended feed (API)" --body "$(cat <<'EOF'
Implements the approved design in docs/plans/2026-07-09-user-taste-profile-design.md.

- `user_tag_affinity` analytics table (nightly rebuild: advisory lock, user-batched aggregation, atomic staging swap)
- `GET /users/me/taste-profile` — private per-user tag analytics (lift + centered rating deltas)
- `GET /images/recommended` — live-scored personalized feed with because-tags and negative-affinity suppression

Merge BEFORE the frontend PR (shuushuu-frontend feat/user-taste-profile).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
