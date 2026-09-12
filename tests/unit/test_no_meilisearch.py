"""Meilisearch is gone: nothing in app/ or scripts/ may import or mention it."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_no_meilisearch_references_outside_history():
    result = subprocess.run(
        [
            "git",
            "grep",
            "-il",
            "meili",
            "--",
            ".",
            ":!docs/adr",
            ":!docs/plans",
            ":!uv.lock",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    offenders = [
        line for line in result.stdout.splitlines() if line != "tests/unit/test_no_meilisearch.py"
    ]
    assert offenders == [], f"Meilisearch references remain: {offenders}"
