"""Refresh the precomputed per-user tag-affinity table (taste profiles).

For each eligible user (>= min_events favorites+ratings+uploads) and each tag
with minimum support, stores positive-pool counts (favorites ∪ uploads,
deduped), rating stats, a popularity-normalized lift, a per-user-mean-centered
rating delta, and the blended affinity used by /images/recommended.

One transaction: temp helper tables, the batched aggregation into the live
table after clearing it, and the commit. Readers keep seeing the previous rows
until the commit. The main aggregation is batched by user-id ranges — the
unbatched join is ~75M intermediate rows (5.7M favorites × ~13 tags/image).
A transaction-scoped advisory lock serializes the nightly cron and a manual
run; it and the temp tables vanish with the transaction, so a mid-run failure
leaves nothing behind.
"""

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES

logger = get_logger(__name__)

_PUBLIC = list(PUBLIC_IMAGE_STATUSES)
# Advisory-lock names are server-global; scope to the current database so
# pytest-xdist per-worker DBs get independent locks while production's single
# DB still serializes cron + manual runs.
_LOCK_PREFIX = "user_tag_affinity_refresh"

# Each axis contributes only when it has enough support on its own; NULL-safe
# via COALESCE. lift > 0 is guaranteed inside its CASE (pool_cnt >= min_support
# implies a positive numerator).
_BATCH_INSERT = """
INSERT INTO user_tag_affinity
    (user_id, tag_id, pool_cnt, fav_count, upload_count, rated_count,
     rating_avg, lift, rating_delta, affinity)
SELECT
    agg.user_id, agg.tag_id, agg.pool_cnt, agg.fav_count, agg.upload_count, agg.rated_count,
    agg.rating_sum::float / NULLIF(agg.rated_count, 0) AS rating_avg,
    CASE WHEN agg.pool_cnt > 0 AND u.pool_size > 0
         THEN (agg.pool_cnt::float / u.pool_size) / ((vc.vc + :k)::float / :n) END AS lift,
    agg.rating_sum::float / NULLIF(agg.rated_count, 0) - u.user_mean AS rating_delta,
    COALESCE(CASE WHEN agg.pool_cnt >= :min_support
                  THEN LN((agg.pool_cnt::float / u.pool_size) / ((vc.vc + :k)::float / :n)) END, 0)
    + :beta * COALESCE(CASE WHEN agg.rated_count >= :min_support
                            THEN agg.rating_sum::float / agg.rated_count - u.user_mean END, 0)
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

    Serialized by a transaction-scoped advisory lock so the nightly cron and a
    manual run cannot collide. Returns the sentinel ``-1`` (callers should treat
    ``< 0`` as "skipped") without touching any tables if another refresh already
    holds the lock.
    """
    # Advisory-lock keys are server-global; scope to the current database so
    # pytest-xdist per-worker DBs get independent locks while production's
    # single DB still serializes cron + manual runs.
    db_name = (await db.execute(text("SELECT current_database()"))).scalar()
    lock_name = f"{_LOCK_PREFIX}:{db_name}"
    locked = (
        await db.execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:n))"), {"n": lock_name})
    ).scalar()
    if not locked:
        logger.info("user_tag_affinity_refresh_skipped_locked")
        return -1
    try:
        # 1. canonical, visible links
        await _exec(
            db,
            """
            CREATE TEMP TABLE _taste_vl ON COMMIT DROP AS
            SELECT DISTINCT tl.image_id, COALESCE(t.alias_of, t.tag_id) AS tag_id
            FROM tag_links tl
            JOIN images i ON i.image_id = tl.image_id
            JOIN tags   t ON t.tag_id   = tl.tag_id
            WHERE i.status IN :public_statuses
            """,
            {"public_statuses": _PUBLIC},
        )
        await _exec(db, "ALTER TABLE _taste_vl ADD PRIMARY KEY (image_id, tag_id)")
        await _exec(db, "CREATE INDEX ON _taste_vl (tag_id)")
        # Temp tables carry no statistics: autovacuum can't see another
        # session's temp tables, and CREATE TABLE AS collects none either.
        # Without ANALYZE, the planner full-scans _taste_vl twice per batch.
        await _exec(db, "ANALYZE _taste_vl")

        # 2. visible tagged images, per-canonical-tag counts, and N
        await _exec(
            db,
            "CREATE TEMP TABLE _taste_vi ON COMMIT DROP AS SELECT DISTINCT image_id FROM _taste_vl",
        )
        await _exec(db, "ALTER TABLE _taste_vi ADD PRIMARY KEY (image_id)")
        await _exec(db, "ANALYZE _taste_vi")
        await _exec(
            db,
            "CREATE TEMP TABLE _taste_vc ON COMMIT DROP AS "
            "SELECT tag_id, COUNT(*) AS vc FROM _taste_vl GROUP BY tag_id",
        )
        await _exec(db, "ALTER TABLE _taste_vc ADD PRIMARY KEY (tag_id)")
        await _exec(db, "ANALYZE _taste_vc")
        n = (await db.execute(text("SELECT COUNT(*) FROM _taste_vi"))).scalar() or 0

        # 3. eligible users (raw event counts)
        await _exec(
            db,
            """
            CREATE TEMP TABLE _taste_elig ON COMMIT DROP AS
            SELECT user_id FROM (
                SELECT user_id FROM favorites
                UNION ALL SELECT user_id FROM image_ratings
                UNION ALL SELECT user_id FROM images WHERE user_id IS NOT NULL
            ) ev GROUP BY user_id HAVING COUNT(*) >= :min_events
            """,
            {"min_events": min_events},
        )
        await _exec(db, "ALTER TABLE _taste_elig ADD PRIMARY KEY (user_id)")
        await _exec(db, "ANALYZE _taste_elig")

        # 4. deduped positive pool (favorites ∪ uploads), visible only
        await _exec(
            db,
            """
            CREATE TEMP TABLE _taste_pool ON COMMIT DROP AS
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
        await _exec(db, "ANALYZE _taste_pool")

        # 5. per-user scalars (pool size, mean rating over visible images)
        await _exec(
            db,
            """
            CREATE TEMP TABLE _taste_users ON COMMIT DROP AS
            SELECT e.user_id,
                (SELECT COUNT(*) FROM _taste_pool p WHERE p.user_id = e.user_id) AS pool_size,
                (SELECT AVG(r.rating)::float FROM image_ratings r
                  JOIN _taste_vi vi ON vi.image_id = r.image_id
                  WHERE r.user_id = e.user_id) AS user_mean
            FROM _taste_elig e
            """,
        )
        await _exec(db, "ALTER TABLE _taste_users ADD PRIMARY KEY (user_id)")
        await _exec(db, "ANALYZE _taste_users")

        # 6. clear the live table inside the transaction: readers keep the old
        #    rows until commit, and the schema (PK, lookup index, defaults)
        #    stays exactly what the migration chain created.
        await _exec(db, "DELETE FROM user_tag_affinity")

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

        n_rows = (await db.execute(text("SELECT COUNT(*) FROM user_tag_affinity"))).scalar() or 0
        await db.commit()
        return n_rows
    finally:
        # A mid-run failure leaves the transaction aborted; rolling it back
        # releases the advisory lock and drops the temp tables so the same
        # session can run again. A healthy post-commit session tolerates
        # rollback() fine, so this is a no-op on the success path.
        try:
            await db.rollback()
        except Exception:
            pass
