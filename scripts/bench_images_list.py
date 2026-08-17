"""Benchmark harness for GET /api/v1/images/ (the FE home-page query).

Measures warm-cache latency for a fixed mix of representative scenarios and
provides a golden-response correctness gate. Built as the metric + verify step
for autonomous perf-optimization loops (autoresearch): the loop optimizes
``total_p50_ms`` from ``--json`` and must keep ``--golden check`` passing.

    uv run python scripts/bench_images_list.py                 # human table
    uv run python scripts/bench_images_list.py --json          # machine output
    uv run python scripts/bench_images_list.py --golden save   # record baseline bodies
    uv run python scripts/bench_images_list.py --golden check  # diff against baseline

Targets BENCH_BASE_URL (default http://localhost:18000 — skinny's local API
container; never spiff's forwarded :8000). Authed scenarios run only when
BENCH_USERNAME/BENCH_PASSWORD are set. Golden bodies live in .bench/golden/
(gitignored; specific to this host's dataset).
"""

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

BASE_URL = os.environ.get("BENCH_BASE_URL", "http://localhost:18000")
IMAGES_PATH = "/api/v1/images/"
LOGIN_PATH = "/api/v1/auth/login"
GOLDEN_DIR = Path(__file__).resolve().parent.parent / ".bench" / "golden"

# Pinned to skinny's dataset (1.1M images): 46 = "long hair" (729k links),
# 169 = "blonde hair" (279k links). The intersection is the expensive-join case.
POPULAR_TAG = "46"
INTERSECT_TAGS = "46,169"

# The FE home page always sends this shape (see shuushuu-frontend
# src/routes/+page.server.ts).
FE_BASE_PARAMS = {"per_page": "20", "sort_by": "image_id", "sort_order": "DESC"}

NOISE_RATIO = 2.0  # p95/p50 above this means the box was busy; distrust the run


@dataclass(frozen=True)
class Scenario:
    name: str
    params: dict[str, str]
    needs_auth: bool = False


@dataclass(frozen=True)
class Stats:
    min_ms: float
    p50_ms: float
    p95_ms: float
    noisy: bool


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (inclusive method)."""
    ordered = sorted(values)
    rank = (pct / 100) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (rank - lower) * (ordered[upper] - ordered[lower])


def summarize(times_ms: list[float]) -> Stats:
    p50 = percentile(times_ms, 50)
    p95 = percentile(times_ms, 95)
    return Stats(
        min_ms=min(times_ms),
        p50_ms=p50,
        p95_ms=p95,
        noisy=p95 / p50 > NOISE_RATIO,
    )


def build_scenarios(authed: bool) -> list[Scenario]:
    def shape(**extra: str) -> dict[str, str]:
        return {**FE_BASE_PARAMS, **extra}

    scenarios = [
        Scenario("page1", shape(page="1")),
        Scenario("page100", shape(page="100")),
        Scenario("page5000", shape(page="5000")),
        Scenario("tag_filter", shape(page="1", tags=POPULAR_TAG)),
        Scenario("tag_intersect", shape(page="1", tags=INTERSECT_TAGS, tags_mode="all")),
    ]
    if authed:
        scenarios += [
            Scenario("page1_auth", shape(page="1"), needs_auth=True),
            Scenario("page5000_auth", shape(page="5000"), needs_auth=True),
        ]
    return scenarios


def golden_diff(expected: object, actual: object, path: str = "") -> str | None:
    """Return a description of the first difference, or None if equivalent."""
    if type(expected) is not type(actual):
        return f"{path or '$'}: type {type(expected).__name__} != {type(actual).__name__}"
    if isinstance(expected, dict) and isinstance(actual, dict):
        if expected.keys() != actual.keys():
            missing = expected.keys() - actual.keys()
            extra = actual.keys() - expected.keys()
            return f"{path or '$'}: keys changed (missing={sorted(missing)}, extra={sorted(extra)})"
        for key in expected:
            diff = golden_diff(expected[key], actual[key], f"{path}.{key}" if path else key)
            if diff:
                return diff
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return f"{path or '$'}: length {len(expected)} != {len(actual)}"
        for index, (exp_item, act_item) in enumerate(zip(expected, actual)):
            diff = golden_diff(exp_item, act_item, f"{path}[{index}]")
            if diff:
                return diff
        return None
    if expected != actual:
        return f"{path or '$'}: {expected!r} != {actual!r}"
    return None


def _login(client: httpx.Client) -> str:
    username = os.environ["BENCH_USERNAME"]
    password = os.environ["BENCH_PASSWORD"]
    response = client.post(BASE_URL + LOGIN_PATH, json={"username": username, "password": password})
    if response.status_code != 200:
        sys.exit(f"login as {username!r} failed: HTTP {response.status_code} {response.text[:200]}")
    return str(response.json()["access_token"])


def _fetch(client: httpx.Client, scenario: Scenario, token: str | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if scenario.needs_auth and token else {}
    response = client.get(BASE_URL + IMAGES_PATH, params=scenario.params, headers=headers)
    if response.status_code != 200:
        sys.exit(f"{scenario.name}: HTTP {response.status_code} {response.text[:200]}")
    if not response.json().get("images"):
        sys.exit(f"{scenario.name}: response has no images — refusing to benchmark")
    return response


def _run_benchmark(
    scenarios: list[Scenario], token: str | None, runs: int, warmup: int
) -> dict[str, Stats]:
    results: dict[str, Stats] = {}
    with httpx.Client(timeout=60) as client:
        for scenario in scenarios:
            for _ in range(warmup):
                _fetch(client, scenario, token)
            times = []
            for _ in range(runs):
                start = time.perf_counter()
                _fetch(client, scenario, token)
                times.append((time.perf_counter() - start) * 1000)
            results[scenario.name] = summarize(times)
    return results


def _print_results(results: dict[str, Stats], runs: int, as_json: bool) -> None:
    total_p50 = sum(stats.p50_ms for stats in results.values())
    if as_json:
        print(
            json.dumps(
                {
                    "runs": runs,
                    "scenarios": {name: asdict(stats) for name, stats in results.items()},
                    "total_p50_ms": round(total_p50, 2),
                }
            )
        )
        return
    print(f"{'scenario':16} {'min':>9} {'p50':>9} {'p95':>9}")
    print("-" * 48)
    for name, stats in results.items():
        flag = "  NOISY" if stats.noisy else ""
        print(f"{name:16} {stats.min_ms:7.1f}ms {stats.p50_ms:7.1f}ms {stats.p95_ms:7.1f}ms{flag}")
    print("-" * 48)
    print(f"{'total_p50':16} {'':9} {total_p50:7.1f}ms")
    if any(stats.noisy for stats in results.values()):
        print("\nWARNING: noisy scenarios (p95 > 2x p50) — rerun on a quieter box before trusting")


def _run_golden(scenarios: list[Scenario], token: str | None, mode: str) -> None:
    failures = []
    with httpx.Client(timeout=60) as client:
        for scenario in scenarios:
            body = _fetch(client, scenario, token).json()
            golden_path = GOLDEN_DIR / f"{scenario.name}.json"
            if mode == "save":
                golden_path.parent.mkdir(parents=True, exist_ok=True)
                golden_path.write_text(json.dumps(body, sort_keys=True, indent=1))
                print(f"saved {golden_path}")
            else:
                if not golden_path.exists():
                    sys.exit(f"{golden_path} missing — run with --golden save first")
                diff = golden_diff(json.loads(golden_path.read_text()), body)
                status = f"DIFF  {diff}" if diff else "ok"
                print(f"{scenario.name:16} {status}")
                if diff:
                    failures.append(scenario.name)
    if failures:
        sys.exit(f"golden check FAILED for: {', '.join(failures)}")
    if mode == "check":
        print("golden check passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=15, help="timed requests per scenario")
    parser.add_argument("--warmup", type=int, default=3, help="untimed warmup requests per scenario")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--golden", choices=["save", "check"], help="correctness gate mode")
    args = parser.parse_args()

    authed = bool(os.environ.get("BENCH_USERNAME") and os.environ.get("BENCH_PASSWORD"))
    scenarios = build_scenarios(authed)
    token = None
    with httpx.Client(timeout=30) as client:
        if authed:
            token = _login(client)
        else:
            print("note: BENCH_USERNAME/BENCH_PASSWORD not set — skipping authed scenarios",
                  file=sys.stderr)

    if args.golden:
        _run_golden(scenarios, token, args.golden)
        return
    results = _run_benchmark(scenarios, token, args.runs, args.warmup)
    _print_results(results, args.runs, args.json)


if __name__ == "__main__":
    main()
