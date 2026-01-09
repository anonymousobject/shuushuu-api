#!/usr/bin/env python3
"""
Performance comparison script for comment search endpoints.

Compares the performance of:
1. /comments/search/text (LIKE pattern matching)
2. /comments/search/fulltext (MySQL FULLTEXT index)

Requirements:
- Install httpx: pip install httpx
- Ensure the API is running
- Create the FULLTEXT index first (see fulltext_index_migration.sql)

Usage:
    python docs/test_search_performance.py
    python docs/test_search_performance.py --base-url http://localhost:8000
    python docs/test_search_performance.py --queries "awesome" "great" "beautiful"
"""

import argparse
import asyncio
import statistics
import time
from typing import Any

try:
    import httpx
except ImportError:
    print("Error: httpx is not installed. Install it with: pip install httpx")
    exit(1)


async def time_request(
    client: httpx.AsyncClient, url: str, params: dict[str, Any]
) -> tuple[float, dict[str, Any]]:
    """Make a request and return the elapsed time and response data."""
    start = time.perf_counter()
    response = await client.get(url, params=params)
    elapsed = time.perf_counter() - start

    response.raise_for_status()
    return elapsed, response.json()


async def benchmark_search(
    base_url: str, query_text: str, runs: int = 10, per_page: int = 20
) -> dict[str, Any]:
    """
    Benchmark both search endpoints for a given query.

    Returns:
        Dictionary with timing statistics for both endpoints
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        like_times = []
        fulltext_times = []

        like_url = f"{base_url}/api/v1/comments/search/text"
        fulltext_url = f"{base_url}/api/v1/comments/search/fulltext"

        params = {"query_text": query_text, "per_page": per_page, "page": 1}

        print(f"\n{'=' * 70}")
        print(f"Testing query: '{query_text}'")
        print(f"{'=' * 70}")
        print(f"Running {runs} iterations for each endpoint...\n")

        # Warm-up requests (not counted)
        print("Warming up...")
        await time_request(client, like_url, params)
        await time_request(client, fulltext_url, params)

        # Benchmark LIKE search
        print(f"Testing /search/text (LIKE)...")
        for i in range(runs):
            elapsed, data = await time_request(client, like_url, params)
            like_times.append(elapsed)
            if i == 0:
                like_total = data.get("total", 0)
            print(f"  Run {i + 1}/{runs}: {elapsed * 1000:.2f}ms")

        # Benchmark FULLTEXT search
        print(f"\nTesting /search/fulltext (FULLTEXT)...")
        for i in range(runs):
            elapsed, data = await time_request(client, fulltext_url, params)
            fulltext_times.append(elapsed)
            if i == 0:
                fulltext_total = data.get("total", 0)
            print(f"  Run {i + 1}/{runs}: {elapsed * 1000:.2f}ms")

        return {
            "query": query_text,
            "like": {
                "times": like_times,
                "mean": statistics.mean(like_times),
                "median": statistics.median(like_times),
                "stdev": statistics.stdev(like_times) if len(like_times) > 1 else 0,
                "min": min(like_times),
                "max": max(like_times),
                "total_results": like_total,
            },
            "fulltext": {
                "times": fulltext_times,
                "mean": statistics.mean(fulltext_times),
                "median": statistics.median(fulltext_times),
                "stdev": statistics.stdev(fulltext_times) if len(fulltext_times) > 1 else 0,
                "min": min(fulltext_times),
                "max": max(fulltext_times),
                "total_results": fulltext_total,
            },
        }


def print_results(results: list[dict[str, Any]]) -> None:
    """Print formatted benchmark results."""
    print("\n" + "=" * 70)
    print("PERFORMANCE COMPARISON SUMMARY")
    print("=" * 70)

    for result in results:
        query = result["query"]
        like_stats = result["like"]
        fulltext_stats = result["fulltext"]

        print(f"\nQuery: '{query}'")
        print(
            f"Results found: LIKE={like_stats['total_results']}, FULLTEXT={fulltext_stats['total_results']}"
        )
        print(f"\n{'Metric':<20} {'LIKE (ms)':<15} {'FULLTEXT (ms)':<15} {'Speedup':<10}")
        print("-" * 70)

        metrics = [
            ("Mean", like_stats["mean"], fulltext_stats["mean"]),
            ("Median", like_stats["median"], fulltext_stats["median"]),
            ("Min", like_stats["min"], fulltext_stats["min"]),
            ("Max", like_stats["max"], fulltext_stats["max"]),
            ("Std Dev", like_stats["stdev"], fulltext_stats["stdev"]),
        ]

        for name, like_val, fulltext_val in metrics:
            speedup = like_val / fulltext_val if fulltext_val > 0 else 0
            speedup_str = f"{speedup:.2f}x" if speedup > 0 else "N/A"
            print(
                f"{name:<20} {like_val * 1000:<15.2f} {fulltext_val * 1000:<15.2f} {speedup_str:<10}"
            )

        # Overall verdict
        mean_speedup = like_stats["mean"] / fulltext_stats["mean"]
        print(f"\n{'Overall Verdict:':<20}", end="")
        if mean_speedup > 1.5:
            print(f"FULLTEXT is {mean_speedup:.2f}x faster! 🚀")
        elif mean_speedup > 1.1:
            print(f"FULLTEXT is slightly faster ({mean_speedup:.2f}x)")
        elif mean_speedup < 0.9:
            print(f"LIKE is faster ({1 / mean_speedup:.2f}x) - consider dataset size")
        else:
            print("Performance is similar")


async def main():
    parser = argparse.ArgumentParser(description="Compare performance of LIKE vs FULLTEXT search")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the API (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--runs", type=int, default=10, help="Number of runs per query (default: 10)"
    )
    parser.add_argument(
        "--queries",
        nargs="+",
        default=["awesome", "great", "beautiful", "nice"],
        help="Search queries to test (default: awesome great beautiful nice)",
    )
    parser.add_argument("--per-page", type=int, default=20, help="Results per page (default: 20)")

    args = parser.parse_args()

    print("=" * 70)
    print("COMMENT SEARCH PERFORMANCE BENCHMARK")
    print("=" * 70)
    print(f"API URL: {args.base_url}")
    print(f"Queries: {', '.join(args.queries)}")
    print(f"Runs per query: {args.runs}")
    print(f"Results per page: {args.per_page}")

    results = []
    for query in args.queries:
        try:
            result = await benchmark_search(
                base_url=args.base_url, query_text=query, runs=args.runs, per_page=args.per_page
            )
            results.append(result)
        except httpx.HTTPError as e:
            print(f"\n⚠️  Error testing query '{query}': {e}")
            continue

    if results:
        print_results(results)
    else:
        print("\n⚠️  No successful benchmark results")
        print("\nTroubleshooting:")
        print("1. Ensure the API is running at", args.base_url)
        print("2. Create the FULLTEXT index (see docs/fulltext_index_migration.sql)")
        print("3. Ensure there is comment data in the database")


if __name__ == "__main__":
    asyncio.run(main())
