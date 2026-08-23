"""Tests for the prod-restore helpers in scripts/db_utils.py.

Every database step runs inside the compose ``postgres`` service through
``docker compose exec -T postgres psql``: the container's psql is guaranteed
to match the server (a plain pg_dump from 18 opens with ``\\restrict``, which
older clients reject), the unix socket is trust-authenticated so no password
ever reaches the host, and the script can only touch the local stack — the
prod overlay stubs the service out.
"""

from pathlib import Path

import pytest

from scripts.db_utils import (
    analyze_database,
    create_test_users,
    drop_and_create_database,
    dump_owner_roles,
    ensure_roles,
    import_sql_dump,
    load_db_config,
    reassign_ownership,
    run_alembic_upgrade,
    run_command,
)

PROJECT_ROOT = Path("/repo")
CONFIG = {"database": "shuushuu", "user": "shuushuu"}
PSQL_PREFIX = ["docker", "compose", "exec", "-T", "postgres", "psql"]


def _opt(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def _sql(cmd: list[str]) -> str:
    return " ".join(cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-c")


@pytest.fixture
def calls(monkeypatch):
    captured: list[tuple[list[str], dict]] = []

    async def fake_run_command(cmd, description, **kwargs):
        captured.append((cmd, kwargs))
        return True

    monkeypatch.setattr("scripts.db_utils.run_command", fake_run_command)
    return captured


@pytest.mark.unit
class TestLoadDbConfig:
    """The target is the compose postgres service, so its identity comes from
    the same POSTGRES_* variables docker-compose.yml interpolates."""

    def test_defaults_match_compose(self, monkeypatch, tmp_path: Path):
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.delenv("POSTGRES_USER", raising=False)
        assert load_db_config(tmp_path) == {"database": "shuushuu", "user": "shuushuu"}

    def test_reads_dotenv(self, monkeypatch, tmp_path: Path):
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.delenv("POSTGRES_USER", raising=False)
        (tmp_path / ".env").write_text("POSTGRES_DB=other_db\nPOSTGRES_USER=other_user\n")
        assert load_db_config(tmp_path) == {"database": "other_db", "user": "other_user"}

    def test_environment_overrides_dotenv(self, monkeypatch, tmp_path: Path):
        (tmp_path / ".env").write_text("POSTGRES_DB=from_file\n")
        monkeypatch.setenv("POSTGRES_DB", "from_env")
        assert load_db_config(tmp_path)["database"] == "from_env"


@pytest.mark.unit
class TestRunCommand:
    async def test_feeds_stdin_to_the_process(self, tmp_path: Path):
        source = tmp_path / "input.txt"
        source.write_text("hello\n")
        with source.open() as stdin:
            ok = await run_command(
                ["sh", "-c", 'read line; test "$line" = hello'], "read stdin", stdin=stdin
            )
        assert ok is True

    async def test_reports_failure(self):
        assert await run_command(["false"], "fail") is False


@pytest.mark.unit
class TestDropAndCreateDatabase:
    async def test_drops_with_force_from_the_maintenance_database(self, calls):
        """DROP DATABASE refuses to run from inside the target and refuses
        while anything is connected; FORCE evicts stragglers."""
        assert await drop_and_create_database(CONFIG)
        cmd, _ = calls[0]
        assert cmd[: len(PSQL_PREFIX)] == PSQL_PREFIX
        assert _opt(cmd, "-d") == "postgres"
        assert 'DROP DATABASE IF EXISTS "shuushuu" WITH (FORCE)' in _sql(cmd)
        assert 'CREATE DATABASE "shuushuu" OWNER "shuushuu"' in _sql(cmd)
        assert "ON_ERROR_STOP=1" in cmd


@pytest.mark.unit
class TestDumpOwnerRoles:
    def test_collects_roles_from_the_pre_data_section(self, tmp_path: Path):
        dump = tmp_path / "dump.sql"
        dump.write_text(
            "CREATE TYPE public.bannersize AS ENUM ('a');\n"
            "ALTER TYPE public.bannersize OWNER TO shuushuu_user;\n"
            "ALTER TABLE public.users OWNER TO shuushuu_user;\n"
            "ALTER FUNCTION public.f() OWNER TO other_role;\n"
        )
        assert dump_owner_roles(dump) == {"shuushuu_user", "other_role"}

    def test_ignores_statement_lookalikes_inside_copy_data(self, tmp_path: Path):
        """Row text is user content; a post body that happens to start with an
        ALTER statement is data, not an owner."""
        dump = tmp_path / "dump.sql"
        dump.write_text(
            "ALTER TABLE public.posts OWNER TO shuushuu_user;\n"
            "COPY public.posts (post_id, body) FROM stdin;\n"
            "1\tALTER TABLE public.posts OWNER TO evil;\n"
            "\\.\n"
        )
        assert dump_owner_roles(dump) == {"shuushuu_user"}


@pytest.mark.unit
class TestEnsureRoles:
    async def test_creates_missing_roles_without_login(self, calls):
        assert await ensure_roles({"shuushuu_user"}, CONFIG)
        cmd, _ = calls[0]
        assert cmd[: len(PSQL_PREFIX)] == PSQL_PREFIX
        sql = _sql(cmd)
        assert "pg_roles" in sql and "rolname = 'shuushuu_user'" in sql
        assert 'CREATE ROLE "shuushuu_user" NOLOGIN' in sql

    async def test_nothing_to_do_runs_nothing(self, calls):
        assert await ensure_roles(set(), CONFIG)
        assert calls == []


@pytest.mark.unit
class TestImportSqlDump:
    async def test_streams_the_dump_into_psql_as_one_transaction(self, calls, tmp_path: Path):
        dump = tmp_path / "dump.sql"
        dump.write_text("SELECT 1;\n")
        assert await import_sql_dump(dump, CONFIG)
        cmd, kwargs = calls[0]
        assert cmd[: len(PSQL_PREFIX)] == PSQL_PREFIX
        assert _opt(cmd, "-d") == "shuushuu"
        assert "ON_ERROR_STOP=1" in cmd
        assert "--single-transaction" in cmd
        assert _opt(cmd, "-f") == "-"
        assert Path(kwargs["stdin"].name) == dump

    async def test_missing_dump_runs_nothing(self, calls, tmp_path: Path):
        assert await import_sql_dump(tmp_path / "missing.sql", CONFIG) is False
        assert calls == []


@pytest.mark.unit
class TestReassignOwnership:
    async def test_hands_foreign_roles_objects_to_the_dev_user(self, calls):
        assert await reassign_ownership({"shuushuu_user"}, CONFIG)
        cmd, _ = calls[0]
        assert _opt(cmd, "-d") == "shuushuu"
        assert 'REASSIGN OWNED BY "shuushuu_user" TO "shuushuu"' in _sql(cmd)

    async def test_nothing_to_do_runs_nothing(self, calls):
        assert await reassign_ownership(set(), CONFIG)
        assert calls == []


@pytest.mark.unit
class TestAnalyzeDatabase:
    async def test_analyzes_the_target(self, calls):
        assert await analyze_database(CONFIG)
        cmd, _ = calls[0]
        assert _opt(cmd, "-d") == "shuushuu"
        assert "ANALYZE" in _sql(cmd)


@pytest.mark.unit
class TestRunAlembicUpgrade:
    async def test_runs_the_postgres_chain_in_a_one_off_api_container(self, calls):
        """The api service is stopped during a restore, so this is `run`, not
        `exec`; the container's own DATABASE_URL points at the restored db."""
        assert await run_alembic_upgrade(PROJECT_ROOT)
        cmd, kwargs = calls[0]
        assert cmd[:3] == ["docker", "compose", "run"]
        assert "--rm" in cmd and "--no-deps" in cmd and "-T" in cmd
        assert cmd[cmd.index("api") + 1 :] == [
            "uv",
            "run",
            "--no-project",
            "alembic",
            "-c",
            "alembic.pg.ini",
            "upgrade",
            "head",
        ]
        assert kwargs["cwd"] == PROJECT_ROOT


@pytest.mark.unit
class TestCreateTestUsers:
    async def test_runs_the_script_in_a_one_off_api_container(self, calls):
        """Accounts go in through the Users model so its defaults fill the
        NOT NULL columns; that needs the app and its DATABASE_URL, which only
        the api container has. The service is stopped at this point."""
        assert await create_test_users(PROJECT_ROOT)
        cmd, kwargs = calls[0]
        assert cmd[:3] == ["docker", "compose", "run"]
        assert "--rm" in cmd and "--no-deps" in cmd and "-T" in cmd
        assert cmd[cmd.index("api") + 1 :] == [
            "uv",
            "run",
            "--no-project",
            "python",
            "scripts/create_test_users.py",
        ]
        assert kwargs["cwd"] == PROJECT_ROOT
