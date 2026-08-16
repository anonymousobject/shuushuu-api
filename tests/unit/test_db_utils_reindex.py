"""Tests for the post-restore search reindex step.

A restore repopulates MySQL but leaves Meilisearch holding whatever it had
before, so the instance has a silently stale search index until it is rebuilt.
The rebuild runs inside the api container (reindex_search.py reads
settings.DATABASE_URL directly, and the compose hostnames only resolve there),
and must never turn a completed restore into a failure.
"""

from pathlib import Path

import pytest

from scripts.db_utils import reindex_search

PROJECT_ROOT = Path("/repo")


@pytest.mark.unit
class TestReindexSearch:
    async def test_runs_the_reindex_script_inside_the_api_container(self, monkeypatch):
        captured: list[list[str]] = []

        async def fake_run_command(cmd, description, **kwargs):
            captured.append(cmd)
            return True

        monkeypatch.setattr("scripts.db_utils.run_command", fake_run_command)

        assert await reindex_search(PROJECT_ROOT) is True

        cmd = captured[0]
        assert cmd[:5] == ["docker", "compose", "exec", "-T", "api"]
        assert "scripts/reindex_search.py" in cmd

    async def test_returns_false_when_the_reindex_fails(self, monkeypatch):
        """Caller decides what to do; a stale index must not fail the restore."""

        async def fake_run_command(cmd, description, **kwargs):
            return False

        monkeypatch.setattr("scripts.db_utils.run_command", fake_run_command)

        assert await reindex_search(PROJECT_ROOT) is False
