"""
Helpers for restore_prod_db.py: a pg_dump of prod into the compose postgres service.

Every database step runs inside the running ``postgres`` compose service via
``docker compose exec -T postgres psql``. That is deliberate:

- the container's psql matches the server (a plain pg_dump from 18 opens with
  ``\\restrict``, which older clients reject);
- the unix socket is trust-authenticated in the official image, so no password
  is parsed, printed, or passed around on the host;
- the script can only ever reach the local stack — the prod overlay replaces
  the service with a busybox stub.

The alembic and search-reindex steps run in the api service for the same
reason: the container already holds the DATABASE_URL / MEILISEARCH_URL that
resolve on the compose network.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import IO, Any

from dotenv import dotenv_values

# Compose service names (see docker-compose.yml)
POSTGRES_SERVICE = "postgres"
API_SERVICE = "api"

# Database to connect to while the target is being dropped and recreated
MAINTENANCE_DATABASE = "postgres"

# Ownership statements pg_dump emits alongside each object definition
_OWNER_TO = re.compile(r"^ALTER .* OWNER TO (\w+);$")


def print_header(text: str, width: int = 80) -> None:
    """Print a formatted header."""
    print("\n" + "=" * width)
    print(text.center(width))
    print("=" * width + "\n")


def load_db_config(project_root: Path) -> dict[str, str]:
    """
    Identify the target database the way docker-compose.yml does.

    The postgres service is created from POSTGRES_DB / POSTGRES_USER, which
    compose interpolates from the shell environment and then .env, with the
    defaults below. Resolve them the same way so the script and the container
    never disagree about which database is being replaced.
    """
    from_file = dotenv_values(project_root / ".env")
    values = {key: value for key, value in from_file.items() if value is not None}
    values.update(os.environ)
    return {
        "database": values.get("POSTGRES_DB", "shuushuu"),
        "user": values.get("POSTGRES_USER", "shuushuu"),
    }


async def run_command(
    cmd: list[str],
    description: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin: IO[Any] | None = None,
) -> bool:
    """
    Run a shell command and return success status.

    Args:
        cmd: Command and arguments as list
        description: Human-readable description for logging
        cwd: Working directory (defaults to project root)
        env: Optional environment variables to override/add
        stdin: Optional open file to feed the process on standard input

    Returns:
        True if successful, False otherwise
    """
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}\n")

    try:
        # Merge environment variables
        command_env = os.environ.copy()
        if env:
            command_env.update(env)

        result = subprocess.run(
            cmd,
            cwd=cwd or Path(__file__).parent.parent,
            check=False,
            capture_output=False,
            env=command_env,
            stdin=stdin,
        )

        if result.returncode != 0:
            print(f"\n❌ Command failed with exit code {result.returncode}")
            return False

        print(f"\n✓ {description} completed successfully")
        return True

    except Exception as e:
        print(f"❌ Error running command: {e}")
        return False


def _psql_cmd(db_config: dict[str, str], database: str | None = None) -> list[str]:
    """
    psql inside the compose postgres service, failing on the first SQL error.

    psql's default is to report errors and exit 0 regardless, which would turn
    a half-applied restore into a "success"; ON_ERROR_STOP makes it exit 3.
    """
    return [
        "docker",
        "compose",
        "exec",
        "-T",
        POSTGRES_SERVICE,
        "psql",
        "-U",
        db_config["user"],
        "-d",
        database or db_config["database"],
        "-v",
        "ON_ERROR_STOP=1",
    ]


async def drop_and_create_database(db_config: dict[str, str]) -> bool:
    """
    Drop and recreate the target database.

    Runs against the maintenance database: Postgres refuses to drop the
    database you are connected to. FORCE terminates any session still on it
    (the api and worker are stopped by then; adminer or a forgotten shell are
    the usual stragglers).

    Returns:
        True if successful, False otherwise
    """
    database = db_config["database"]
    print(f"⚠️  Dropping database '{database}' if it exists...")

    cmd = _psql_cmd(db_config, MAINTENANCE_DATABASE) + [
        "-c",
        f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)',
        "-c",
        f'CREATE DATABASE "{database}" OWNER "{db_config["user"]}"',
    ]
    return await run_command(cmd, f"Drop and create database '{database}'")


def dump_owner_roles(sql_file: Path) -> set[str]:
    """
    Roles that own objects in a plain-format pg_dump.

    pg_dump records ownership as ``ALTER ... OWNER TO role;`` next to each
    object definition, all of which precede the data section. Scanning stops
    at the first COPY so row content — which is user text and can contain
    anything — is never mistaken for a statement.
    """
    roles: set[str] = set()
    with sql_file.open(encoding="utf-8", errors="replace") as dump:
        for line in dump:
            if line.startswith("COPY "):
                break
            match = _OWNER_TO.match(line.rstrip("\n"))
            if match:
                roles.add(match.group(1))
    return roles


async def ensure_roles(roles: set[str], db_config: dict[str, str]) -> bool:
    """
    Create the dump's owner roles so its ALTER ... OWNER TO statements succeed.

    A prod dump names the prod role; dev has no such role and the load would
    stop at the first ownership statement. Rather than rewriting the dump
    stream, give each missing role a NOLOGIN existence; reassign_ownership()
    hands its objects to the dev role afterwards.

    Returns:
        True if successful (or nothing to do), False otherwise
    """
    if not roles:
        return True

    statements = [
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
        f'CREATE ROLE "{role}" NOLOGIN; '
        "END IF; END $$"
        for role in sorted(roles)
    ]
    cmd = _psql_cmd(db_config, MAINTENANCE_DATABASE)
    for statement in statements:
        cmd += ["-c", statement]
    return await run_command(cmd, f"Ensure owner roles exist: {', '.join(sorted(roles))}")


async def import_sql_dump(sql_file: Path, db_config: dict[str, str]) -> bool:
    """
    Load a plain-format pg_dump into the (empty) target database.

    The dump is streamed over stdin into the container's psql as a single
    transaction: a failure anywhere leaves an empty database rather than a
    partial one, and ON_ERROR_STOP makes the failure visible.

    Returns:
        True if successful, False otherwise
    """
    if not sql_file.exists():
        print(f"❌ SQL dump file not found: {sql_file}")
        return False

    print(f"Importing SQL dump from: {sql_file}")
    print(f"Into database: {db_config['database']}")

    cmd = _psql_cmd(db_config) + ["--single-transaction", "-f", "-"]
    with sql_file.open("rb") as dump:
        return await run_command(cmd, f"Import SQL dump into '{db_config['database']}'", stdin=dump)


async def reassign_ownership(roles: set[str], db_config: dict[str, str]) -> bool:
    """
    Give everything the dump's roles own to the dev role.

    The placeholder roles from ensure_roles() stay behind, empty and unable to
    log in; the next restore finds them already present.

    Returns:
        True if successful (or nothing to do), False otherwise
    """
    if not roles:
        return True

    cmd = _psql_cmd(db_config)
    for role in sorted(roles):
        cmd += ["-c", f'REASSIGN OWNED BY "{role}" TO "{db_config["user"]}"']
    return await run_command(cmd, f"Reassign ownership to '{db_config['user']}'")


async def analyze_database(db_config: dict[str, str]) -> bool:
    """
    Collect planner statistics.

    A dump restores rows, not statistics; until autovacuum gets around to the
    big tables the planner is guessing, and the first feed queries crawl.

    Returns:
        True if successful, False otherwise
    """
    cmd = _psql_cmd(db_config) + ["-c", "ANALYZE"]
    return await run_command(cmd, f"Analyze database '{db_config['database']}'")


async def run_alembic_upgrade(project_root: Path) -> bool:
    """
    Apply any Postgres-chain migrations the dump predates.

    Runs in a one-off api container (the service itself is stopped during a
    restore) so alembic sees the same DATABASE_URL the application does.

    Returns:
        True if successful, False otherwise
    """
    cmd = [
        "docker",
        "compose",
        "run",
        "--rm",
        "-T",
        "--no-deps",
        API_SERVICE,
        "uv",
        "run",
        "--no-project",
        "alembic",
        "-c",
        "alembic.pg.ini",
        "upgrade",
        "head",
    ]
    return await run_command(cmd, "Run alembic migrations (upgrade head)", cwd=project_root)


async def stop_docker_services(project_root: Path) -> bool:
    """
    Stop API and worker containers to prevent database connection conflicts.

    Args:
        project_root: Project root directory

    Returns:
        True if successful, False otherwise
    """
    cmd = ["docker", "compose", "stop", "api", "arq-worker"]

    success = await run_command(
        cmd,
        "Stop API and worker containers",
        cwd=project_root,
    )

    return success


async def start_docker_services(project_root: Path) -> bool:
    """
    Start API and worker containers after migration completes.

    Args:
        project_root: Project root directory

    Returns:
        True if successful, False otherwise
    """
    cmd = ["docker", "compose", "start", "api", "arq-worker"]

    success = await run_command(
        cmd,
        "Start API and worker containers",
        cwd=project_root,
    )

    return success


async def reindex_search(project_root: Path) -> bool:
    """
    Rebuild the Meilisearch tags index from the database.

    A restore repopulates Postgres and leaves Meilisearch untouched, so search
    silently returns stale or empty results until the index is rebuilt — the
    failure is invisible from the API, which answers normally with nothing in
    it.

    Runs inside the api container: reindex_search.py reads
    settings.DATABASE_URL / settings.MEILISEARCH_URL directly, with no host
    rewriting, and those compose hostnames only resolve on the network.

    Returns:
        True if successful, False otherwise. Callers treat failure as a
        warning: a stale index is worth flagging loudly but is not a reason to
        fail an otherwise complete restore.
    """
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        API_SERVICE,
        "uv",
        "run",
        "--no-project",
        "python",
        "scripts/reindex_search.py",
    ]

    return await run_command(
        cmd,
        "Reindex Meilisearch from the restored database",
        cwd=project_root,
    )


async def create_test_users(project_root: Path) -> bool:
    """
    Create the dev test accounts (scripts/create_test_users.py).

    Runs in a one-off api container: the accounts go in through the Users
    model so its Python-side defaults fill the NOT NULL columns that have no
    database default, and that needs the app and its DATABASE_URL. Re-running
    is harmless; existing accounts are skipped.

    Returns:
        True if successful, False otherwise
    """
    cmd = [
        "docker",
        "compose",
        "run",
        "--rm",
        "-T",
        "--no-deps",
        API_SERVICE,
        "uv",
        "run",
        "--no-project",
        "python",
        "scripts/create_test_users.py",
    ]
    return await run_command(cmd, "Create test users", cwd=project_root)
