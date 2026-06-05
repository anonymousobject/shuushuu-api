"""Benchmark: naive vs fast default-feed pagination COUNT.

Quantifies the list_images count optimization (hidden-complement instead of the
`count(visible OR mine)` full-table scan). Runs against the configured DB
(DATABASE_URL_SYNC — point it at a production-like dataset).

    uv run python scripts/bench_feed_count.py
"""

import time

from sqlalchemy import create_engine, text

from app.config import settings

VISIBLE = "(-1, 1, 2)"  # PUBLIC_IMAGE_STATUSES: REPOST, ACTIVE, SPOILER


def _time_ms(conn, sql: str, runs: int = 3) -> float:
    conn.execute(text(sql)).scalar()  # warm
    best = float("inf")
    for _ in range(runs):
        t = time.perf_counter()
        conn.execute(text(sql)).scalar()
        best = min(best, (time.perf_counter() - t) * 1000)
    return best


def _explain_type(conn, sql: str) -> str:
    row = conn.execute(text("EXPLAIN " + sql)).mappings().first()
    return f"{row['type']}/{row['key']}/{row['rows']} rows" if row else "?"


def main() -> None:
    engine = create_engine(settings.DATABASE_URL_SYNC)
    with engine.connect() as c:
        uid = c.execute(
            text("SELECT user_id FROM images GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 1")
        ).scalar()
        total = c.execute(text("SELECT COUNT(*) FROM images")).scalar()
        print(f"images = {total:,}   sample_user = {uid}\n")

        naive_all = f"SELECT COUNT(*) FROM images WHERE status IN {VISIBLE}"
        naive_mine = f"SELECT COUNT(*) FROM images WHERE status IN {VISIBLE} OR user_id = {uid}"
        all_count = "SELECT COUNT(*) FROM images"
        hidden = f"SELECT COUNT(*) FROM images WHERE status NOT IN {VISIBLE}"
        hidden_mine = f"SELECT COUNT(*) FROM images WHERE status NOT IN {VISIBLE} AND user_id = {uid}"

        print(f"  naive OR plan : {_explain_type(c, naive_mine)}")
        print(f"  hidden  plan  : {_explain_type(c, hidden)}\n")

        scenarios = {
            "anonymous": (naive_all, [all_count, hidden]),
            "logged-in show_all=0": (naive_mine, [all_count, hidden, hidden_mine]),
            "logged-in show_all=1": (all_count, [all_count]),
        }
        print(f"{'scenario':24}  {'before':>10}  {'after':>10}  {'speedup':>8}")
        print("-" * 60)
        for name, (naive, fast_parts) in scenarios.items():
            before = _time_ms(c, naive)
            after = sum(_time_ms(c, p) for p in fast_parts)
            speedup = before / after if after else 0.0
            print(f"{name:24}  {before:8.1f}ms  {after:8.1f}ms  {speedup:6.1f}x")


if __name__ == "__main__":
    main()
