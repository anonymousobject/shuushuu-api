"""Refresh the precomputed tag co-occurrence table (top-N related tags per tag).

Ranked by lift, gated by minimum support, over alias-resolved, visible images.
Uses materialized regular helper tables (MariaDB cannot self-join a TEMPORARY
table, and a pure CTE re-evaluates the 14.5M-row working set ~4x — see the
2026-06-14 plan, Chunk 0). Issues DDL with implicit commits and manages its own
transaction. NOTE: the swapped-in table is rebuilt weekly and intentionally has
NO foreign keys (CREATE TABLE ... LIKE does not copy them); acceptable for a
read-only analytics table and avoids FK-check overhead on bulk load.
"""

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES

logger = get_logger(__name__)

_PUBLIC = list(PUBLIC_IMAGE_STATUSES)
_HELPERS = ("_cooccur_vl", "_cooccur_vc", "_cooccur_vlf", "_cooccur_pairs")
_LOCK_NAME = "tag_cooccurrence_refresh"


async def _exec(db, sql, params=None):
    # Special case: a "public_statuses" key in `params` is expanded as an IN list
    # via an expanding bindparam (the IN :public_statuses clauses below rely on this).
    stmt = text(sql)
    if params and "public_statuses" in params:
        stmt = stmt.bindparams(bindparam("public_statuses", expanding=True))
    await db.execute(stmt, params or {})


async def refresh_tag_cooccurrence(
    db: AsyncSession, *, min_cooccur: int, top_n: int, min_base_usage: int = 0
) -> int:
    """Rebuild the tag_cooccurrence table; return the number of rows written.

    Serialized by a connection-scoped MySQL named lock so the weekly cron and a
    manual run cannot collide. Returns the sentinel ``-1`` (callers should treat
    ``< 0`` as "skipped") without touching any tables if another refresh already
    holds the lock.
    """
    # Acquire before any work; GET_LOCK(name, 0) -> 1 if acquired now, 0 on
    # timeout, NULL on error. Anything falsy means "another refresh is running".
    locked = (await db.execute(text("SELECT GET_LOCK(:n, 0)"), {"n": _LOCK_NAME})).scalar()
    if not locked:
        logger.info("tag_cooccurrence_refresh_skipped_locked")
        return -1  # sentinel: another refresh is already running
    try:
        # Optionally keep the big DISTINCT / GROUP BY in RAM. 0 (default) means
        # "use the server default; the build will spill to disk, which is acceptable
        # for a weekly job" — a large value on a memory-constrained host can OOM-crash
        # MariaDB mid-build, so this is opt-in.
        sz = settings.COOCCUR_SESSION_TMP_TABLE_SIZE
        if sz > 0:
            await _exec(db, f"SET SESSION tmp_table_size = {int(sz)}")
            await _exec(db, f"SET SESSION max_heap_table_size = {int(sz)}")

        for t in _HELPERS:
            await _exec(db, f"DROP TABLE IF EXISTS {t}")

        # 1. canonical, visible links (regular table — self-joined below)
        await _exec(
            db,
            """
            CREATE TABLE _cooccur_vl ENGINE=InnoDB AS
            SELECT DISTINCT tl.image_id, COALESCE(t.alias_of, t.tag_id) AS tag_id
            FROM tag_links tl
            JOIN images i ON i.image_id = tl.image_id
            JOIN tags   t ON t.tag_id   = tl.tag_id
            WHERE i.status IN :public_statuses
            """,
            {"public_statuses": _PUBLIC},
        )
        await _exec(
            db, "ALTER TABLE _cooccur_vl ADD PRIMARY KEY (image_id, tag_id), ADD KEY k_tag (tag_id)"
        )

        # 2. per-canonical-tag visible counts
        await _exec(
            db,
            "CREATE TABLE _cooccur_vc ENGINE=InnoDB AS "
            "SELECT tag_id, COUNT(*) AS vc FROM _cooccur_vl GROUP BY tag_id",
        )
        await _exec(db, "ALTER TABLE _cooccur_vc ADD PRIMARY KEY (tag_id)")

        # N = number of visible images
        n = (
            await db.execute(text("SELECT COUNT(DISTINCT image_id) FROM _cooccur_vl"))
        ).scalar() or 0

        # 3. EXACT lossless filter: a tag with vc < min_cooccur can't be in a surviving pair.
        #    (min_base_usage adds an optional EXTRA lossy floor; default 0 = off.)
        await _exec(
            db,
            """
            CREATE TABLE _cooccur_vlf ENGINE=InnoDB AS
            SELECT vl.image_id, vl.tag_id
            FROM _cooccur_vl vl JOIN _cooccur_vc vc ON vc.tag_id = vl.tag_id
            WHERE vc.vc >= :min_cooccur AND (:min_base_usage = 0 OR vc.vc >= :min_base_usage)
            """,
            {"min_cooccur": min_cooccur, "min_base_usage": min_base_usage},
        )
        await _exec(
            db,
            "ALTER TABLE _cooccur_vlf ADD PRIMARY KEY (image_id, tag_id), ADD KEY k_tag (tag_id)",
        )

        # 4. pairwise co-occurrence with min-support (a<b counts each unordered pair once)
        await _exec(
            db,
            """
            CREATE TABLE _cooccur_pairs ENGINE=InnoDB AS
            SELECT a.tag_id AS a, b.tag_id AS b, COUNT(*) AS c
            FROM _cooccur_vlf a JOIN _cooccur_vlf b
              ON a.image_id = b.image_id AND a.tag_id < b.tag_id
            GROUP BY a.tag_id, b.tag_id
            HAVING COUNT(*) >= :min_cooccur
            """,
            {"min_cooccur": min_cooccur},
        )

        # 5. staging table (LIKE copies PK + lookup index so the post-swap read is indexed)
        await _exec(db, "DROP TABLE IF EXISTS tag_cooccurrence_new")
        await _exec(db, "CREATE TABLE tag_cooccurrence_new LIKE tag_cooccurrence")

        # 6. expand to directed rows, score (confidence = c/vc(base); lift = c*N/(vc(base)*vc(other))),
        #    keep top-N per base by lift. _cooccur_pairs is a regular table so referencing it twice is fine.
        await _exec(
            db,
            """
            INSERT INTO tag_cooccurrence_new
                (tag_id, related_tag_id, related_type, cooccur_count, lift, confidence)
            SELECT tag_id, related_tag_id, related_type, c, lift, confidence FROM (
                SELECT d.base AS tag_id, d.other AS related_tag_id, ot.type AS related_type, d.c AS c,
                       d.c / cb.vc AS confidence,
                       (d.c * :n) / (cb.vc * co.vc) AS lift,
                       ROW_NUMBER() OVER (PARTITION BY d.base
                           ORDER BY (d.c * :n) / (cb.vc * co.vc) DESC, d.c DESC, d.other ASC) AS rn
                FROM ( SELECT a AS base, b AS other, c FROM _cooccur_pairs
                       UNION ALL
                       SELECT b AS base, a AS other, c FROM _cooccur_pairs ) d
                JOIN _cooccur_vc cb ON cb.tag_id = d.base
                JOIN _cooccur_vc co ON co.tag_id = d.other
                JOIN tags        ot ON ot.tag_id = d.other
            ) ranked
            WHERE rn <= :top_n
            """,
            {"n": n, "top_n": top_n},
        )

        n_rows = (await db.execute(text("SELECT COUNT(*) FROM tag_cooccurrence_new"))).scalar() or 0

        # 7. atomic swap, then clean up
        await _exec(db, "DROP TABLE IF EXISTS tag_cooccurrence_old")
        await _exec(
            db,
            "RENAME TABLE tag_cooccurrence TO tag_cooccurrence_old, "
            "tag_cooccurrence_new TO tag_cooccurrence",
        )
        await _exec(db, "DROP TABLE tag_cooccurrence_old")
        for t in _HELPERS:
            await _exec(db, f"DROP TABLE IF EXISTS {t}")
        await db.commit()
        return n_rows
    finally:
        # Connection-scoped lock survives the DDL implicit-commits on this session.
        try:
            await db.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": _LOCK_NAME})
        except Exception:
            # lock auto-releases on connection close; never mask the real exception
            pass
