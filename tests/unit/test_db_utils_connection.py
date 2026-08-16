"""Tests for db_utils connection resolution.

The mariadb client is invoked two ways: on the host (dialing the port compose
publishes) and inside the compose mariadb service via `docker compose exec`
(dialing the server's internal port). These must not use the same port when the
published port is remapped, as it is on a host running a second dev instance.
"""

from pathlib import Path

import pytest

from scripts.db_utils import (
    _build_mysql_cmd,
    _resolve_connection,
    drop_and_create_database,
    import_sql_dump,
)

# Published port deliberately differs from the container's internal 3306, the
# way a second dev instance remaps it (see docker-compose.local.yml).
REMAPPED_CONFIG = {
    "host": "mariadb",
    "port": "13306",
    "user": "shuushuu_dev",
    "password": "secret",
    "database": "shuushuu_dev",
}


def _port_of(cmd: list[str]) -> str:
    return next(arg for arg in cmd if arg.startswith("--port=")).removeprefix("--port=")


def _host_of(cmd: list[str]) -> str:
    return next(arg for arg in cmd if arg.startswith("--host=")).removeprefix("--host=")


@pytest.mark.unit
class TestResolveConnection:
    def test_docker_exec_uses_internal_port(self):
        """Inside the container the published port is meaningless."""
        host, port = _resolve_connection(REMAPPED_CONFIG, use_docker=True)
        assert port == "3306"
        assert host in ("127.0.0.1", "localhost")

    def test_host_execution_uses_published_port(self):
        host, port = _resolve_connection(REMAPPED_CONFIG, use_docker=False)
        assert port == "13306"
        assert host == "localhost"

    def test_host_execution_preserves_non_compose_host(self):
        config = REMAPPED_CONFIG | {"host": "db.example.com"}
        host, port = _resolve_connection(config, use_docker=False)
        assert host == "db.example.com"
        assert port == "13306"


@pytest.mark.unit
class TestBuildMysqlCmd:
    def test_docker_exec_targets_internal_port(self):
        cmd = _build_mysql_cmd(REMAPPED_CONFIG, use_docker=True)
        assert cmd[:5] == ["docker", "compose", "exec", "-T", "mariadb"]
        assert _port_of(cmd) == "3306"

    def test_host_execution_targets_published_port(self):
        cmd = _build_mysql_cmd(REMAPPED_CONFIG, use_docker=False)
        assert cmd[0] == "mariadb"
        assert _port_of(cmd) == "13306"
        assert _host_of(cmd) == "localhost"


@pytest.mark.unit
class TestCommandsUseResolvedPort:
    """The drop/create and import paths build their own command lists."""

    async def test_drop_and_create_uses_internal_port_under_docker(self, monkeypatch):
        captured: list[list[str]] = []

        async def fake_run_command(cmd, description, **kwargs):
            captured.append(cmd)
            return True

        monkeypatch.setattr("scripts.db_utils.run_command", fake_run_command)

        assert await drop_and_create_database(REMAPPED_CONFIG, use_docker=True)
        assert _port_of(captured[0]) == "3306"

    async def test_import_uses_internal_port_under_docker(self, monkeypatch, tmp_path: Path):
        dump = tmp_path / "dump.sql"
        dump.write_text("SELECT 1;\n")
        captured: list[list[str]] = []

        async def fake_run_command(cmd, description, **kwargs):
            captured.append(cmd)
            return True

        monkeypatch.setattr("scripts.db_utils.run_command", fake_run_command)

        assert await import_sql_dump(dump, REMAPPED_CONFIG, use_docker=True)
        flat = " ".join(str(part) for cmd in captured for part in cmd)
        assert "--port=3306" in flat
        assert "--port=13306" not in flat
