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


async def _exec(db: AsyncSession, sql: str, params: dict[str, object] | None = None) -> None:
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
        # A mid-run failure can leave the session's transaction in a state
        # that makes RELEASE_LOCK itself raise (e.g. PendingRollbackError) --
        # swallowed below, that would return a still-locked connection to the
        # pool and wedge every later refresh at -1 until the pool recycles it.
        # A healthy post-commit session tolerates rollback() fine, so this is
        # a no-op on the success path.
        try:
            await db.rollback()
        except Exception:
            pass
        # Connection-scoped lock survives the DDL implicit-commits on this session.
        try:
            await db.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": lock_name})
        except Exception:
            # lock auto-releases on connection close; never mask the real exception
            pass
