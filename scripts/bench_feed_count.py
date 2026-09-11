"""Benchmark: naive vs fast default-feed pagination COUNT.

Quantifies the list_images count optimization (hidden-complement instead of the
`count(visible OR mine)` full-table scan). Runs against the configured DB
(DATABASE_URL — point it at a production-like dataset).

    uv run python scripts/bench_feed_count.py
"""

import asyncio
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import settings

VISIBLE = "(-1, 1, 2)"  # PUBLIC_IMAGE_STATUSES: REPOST, ACTIVE, SPOILER


async def _time_ms(conn: AsyncConnection, sql: str, runs: int = 3) -> float:
    (await conn.execute(text(sql))).scalar()  # warm
    best = float("inf")
    for _ in range(runs):
        t = time.perf_counter()
        (await conn.execute(text(sql))).scalar()
        best = min(best, (time.perf_counter() - t) * 1000)
    return best


async def _explain_plan(conn: AsyncConnection, sql: str) -> str:
    """The top line of the plan (the outer node and its estimate)."""
    rows = (await conn.execute(text("EXPLAIN " + sql))).scalars().all()
    return str(rows[0]) if rows else "?"


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as c:
            uid = (
                await c.execute(
                    text(
                        "SELECT user_id FROM images GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 1"
                    )
                )
            ).scalar()
            total = (await c.execute(text("SELECT COUNT(*) FROM images"))).scalar()
            print(f"images = {total:,}   sample_user = {uid}\n")

            naive_all = f"SELECT COUNT(*) FROM images WHERE status IN {VISIBLE}"
            naive_mine = f"SELECT COUNT(*) FROM images WHERE status IN {VISIBLE} OR user_id = {uid}"
            all_count = "SELECT COUNT(*) FROM images"
            hidden = f"SELECT COUNT(*) FROM images WHERE status NOT IN {VISIBLE}"
            hidden_mine = (
                f"SELECT COUNT(*) FROM images WHERE status NOT IN {VISIBLE} AND user_id = {uid}"
            )

            print(f"  naive OR plan : {await _explain_plan(c, naive_mine)}")
            print(f"  hidden  plan  : {await _explain_plan(c, hidden)}\n")

            scenarios = {
                "anonymous": (naive_all, [all_count, hidden]),
                "logged-in show_all=0": (naive_mine, [all_count, hidden, hidden_mine]),
                "logged-in show_all=1": (all_count, [all_count]),
            }
            # "after" is the cache-MISS path (the raw DB cost of the hidden-complement
            # counts). In production these globals are TTL-cached (feed_count_cache), so a
            # warm hit is a couple of single-digit-ms Redis reads — faster still.
            print(f"{'scenario':24}  {'before':>10}  {'after':>10}  {'speedup':>8}")
            print("-" * 60)
            for name, (naive, fast_parts) in scenarios.items():
                before = await _time_ms(c, naive)
                after = 0.0
                for part in fast_parts:
                    after += await _time_ms(c, part)
                speedup = before / after if after else 0.0
                print(f"{name:24}  {before:8.1f}ms  {after:8.1f}ms  {speedup:6.1f}x")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
