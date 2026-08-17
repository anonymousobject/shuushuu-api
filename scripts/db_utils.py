"""
Shared database utilities for migration and restore scripts.

Extracted from migrate_legacy_db.py to avoid duplication between
the legacy migration script and the prod restore script.
"""

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import bcrypt

# Development/test databases where test users should be created
DEV_TEST_DATABASES = {"shuushuu_dev", "shuushuu_test"}

# Test accounts to create in dev/test databases
# Typed as list[dict[str, Any]] to avoid mypy issues with mixed value types
TEST_ACCOUNTS: list[dict[str, Any]] = [
    {
        "username": "testUser",
        "password": "shuutestuser",
        "email": "test1@shuushuu.com",
        "admin": 0,
        "group": None,
    },
    {
        "username": "testadmin",
        "password": "shuutestadmin",
        "email": "testadmin@shuushuu.com",
        "admin": 1,
        "group": "Admins",
    },
    {
        "username": "testmod",
        "password": "shuutestmod",
        "email": "testmod@shuushuu.com",
        "admin": 0,
        "group": "Mods",
    },
    {
        "username": "testtagger",
        "password": "shuutesttagger",
        "email": "testtagger@shuushuu.com",
        "admin": 0,
        "group": "Taggers",
    },
]


def print_header(text: str, width: int = 80) -> None:
    """Print a formatted header."""
    print("\n" + "=" * width)
    print(text.center(width))
    print("=" * width + "\n")


def parse_database_url(database_url: str) -> dict[str, str]:
    """
    Parse DATABASE_URL into connection components.

    Example: mysql+aiomysql://user:pass@localhost:3306/dbname
    Returns: {host, port, user, password, database}
    """
    # Remove the driver prefix if present
    url = database_url.replace("mysql+aiomysql://", "mysql://")
    parsed = urlparse(url)

    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 3306),
        "user": parsed.username or "root",
        "password": parsed.password or "",
        "database": parsed.path.lstrip("/") if parsed.path else "",
    }


def get_database_url() -> str:
    """Get DATABASE_URL from environment."""
    # Try to load from .env file
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip('"').strip("'")

    # Fall back to environment variable
    return os.environ.get("DATABASE_URL", "")


async def run_command(
    cmd: list[str], description: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> bool:
    """
    Run a shell command and return success status.

    Args:
        cmd: Command and arguments as list
        description: Human-readable description for logging
        cwd: Working directory (defaults to project root)
        env: Optional environment variables to override/add

    Returns:
        True if successful, False otherwise
    """
    print(f"Running: {description}")
    safe_cmd = [arg if not arg.startswith("--password=") else "--password=***" for arg in cmd]
    print(f"Command: {' '.join(safe_cmd)}\n")

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
        )

        if result.returncode != 0:
            print(f"\n❌ Command failed with exit code {result.returncode}")
            return False

        print(f"\n✓ {description} completed successfully")
        return True

    except Exception as e:
        print(f"❌ Error running command: {e}")
        return False


# Compose service name of the MariaDB container (see docker-compose.yml)
MARIADB_SERVICE = "mariadb"

# Port MariaDB listens on inside the container. The published port can differ
# (a host running a second dev instance remaps it, e.g. 13306), so anything
# executing inside the container must dial this one, not the published one.
MARIADB_INTERNAL_PORT = "3306"


def _resolve_connection(db_config: dict[str, str], use_docker: bool) -> tuple[str, str]:
    """
    Resolve the (host, port) the mariadb client should dial.

    Inside the compose service the server is reached on its internal port; the
    published port from DATABASE_URL is meaningless there. From the host the
    Docker hostname 'mariadb' does not resolve, so dial localhost on the
    published port instead.
    """
    if use_docker:
        return "127.0.0.1", MARIADB_INTERNAL_PORT

    host = db_config["host"]
    if host == MARIADB_SERVICE:
        host = "localhost"
    return host, db_config["port"]


def _maybe_docker_exec(cmd: list[str], use_docker: bool) -> list[str]:
    """
    Optionally run a mariadb client command inside the compose MariaDB service.

    When use_docker is True the command is prefixed with
    `docker compose exec -T mariadb` so the client bundled in the running
    container is used instead of a host-installed binary. `-T` disables
    pseudo-TTY allocation so piped stdin (e.g. a SQL dump) is forwarded.

    The command must run from the project root so docker compose resolves the
    correct project; run_command defaults cwd to the project root.
    """
    if use_docker:
        return ["docker", "compose", "exec", "-T", MARIADB_SERVICE, *cmd]
    return cmd


def _build_mysql_cmd(db_config: dict[str, str], use_docker: bool = False) -> list[str]:
    """Build base mariadb CLI command from db config."""
    host, port = _resolve_connection(db_config, use_docker)

    cmd = [
        "mariadb",
        f"--host={host}",
        f"--port={port}",
        f"--user={db_config['user']}",
    ]

    if db_config["password"]:
        cmd.append(f"--password={db_config['password']}")

    cmd.append(db_config["database"])
    return _maybe_docker_exec(cmd, use_docker)


def _hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


async def drop_and_create_database(db_config: dict[str, str], use_docker: bool = False) -> bool:
    """
    Drop and recreate the database.

    Args:
        db_config: Database connection parameters
        use_docker: If True, run the mariadb client inside the compose
            MariaDB service instead of using the host binary

    Returns:
        True if successful, False otherwise
    """
    database_name = db_config["database"]

    print(f"⚠️  Dropping database '{database_name}' if it exists...")

    host, port = _resolve_connection(db_config, use_docker)
    if not use_docker and db_config["host"] == MARIADB_SERVICE:
        print("Note: Replacing Docker hostname 'mariadb' with 'localhost' for host execution")

    # Build mysql command for drop/create (no database name appended: the
    # target database is being dropped, so connect to the server itself)
    mysql_cmd = [
        "mariadb",
        f"--host={host}",
        f"--port={port}",
        f"--user={db_config['user']}",
    ]

    if db_config["password"]:
        mysql_cmd.append(f"--password={db_config['password']}")

    # Drop database
    drop_cmd = mysql_cmd + [
        "-e",
        f"DROP DATABASE IF EXISTS `{database_name}`; CREATE DATABASE `{database_name}` CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci;",
    ]

    success = await run_command(
        _maybe_docker_exec(drop_cmd, use_docker),
        f"Drop and create database '{database_name}'",
    )

    return success


async def import_sql_dump(
    sql_file: Path, db_config: dict[str, str], use_docker: bool = False
) -> bool:
    """
    Import SQL dump file into database.

    Args:
        sql_file: Path to SQL dump file
        db_config: Database connection parameters
        use_docker: If True, run the mariadb client inside the compose
            MariaDB service instead of using the host binary

    Returns:
        True if successful, False otherwise
    """
    if not sql_file.exists():
        print(f"❌ SQL dump file not found: {sql_file}")
        return False

    print(f"Importing SQL dump from: {sql_file}")
    print(f"Into database: {db_config['database']}")

    host, port = _resolve_connection(db_config, use_docker)

    # Build mysql import command with optimizations for large imports
    # Use 1GB for max-allowed-packet (in bytes)
    # Strip LOCK/UNLOCK TABLES from dump to avoid locking conflicts with
    # triggers or foreign keys that reference tables not in the current lock set.
    mysql_cmd = [
        "mariadb",
        f"--host={host}",
        f"--port={port}",
        f"--user={db_config['user']}",
    ]

    if db_config["password"]:
        mysql_cmd.append(f"--password={db_config['password']}")

    mysql_cmd.extend(
        [
            db_config["database"],
            "--max-allowed-packet=1073741824",  # 1GB in bytes
        ]
    )

    # Run the client inside the container when requested; sed still runs on the
    # host (the dump file lives on the host) and pipes into the container's
    # stdin via `docker compose exec -T`.
    mysql_cmd = _maybe_docker_exec(mysql_cmd, use_docker)

    database = db_config["database"]

    # Build the full command with proper shell quoting via shlex.quote
    # Pipe through sed to:
    # - Strip LOCK/UNLOCK TABLES (avoid locking conflicts with triggers/FKs)
    # - Strip DEFINER clauses (prod user may differ from local dev user)
    # - Rewrite ALTER DATABASE to target the local database name (prod dumps
    #   emit ALTER DATABASE `prod_db` around trigger definitions for charset
    #   changes; if the prod db name doesn't exist locally, these fail and
    #   abort the import in batch mode)
    cmd_str = " ".join(shlex.quote(arg) for arg in mysql_cmd)
    alter_db_sed = f"s/^ALTER DATABASE `[^`]*`/ALTER DATABASE `{database}`/g"
    import_cmd = (
        f"sed"
        f" -e '/^LOCK TABLES/d; /^UNLOCK TABLES/d'"
        f" -e 's/ DEFINER[ ]*=[ ]*[^ ]*@[^ ]* / /g'"
        f" -e {shlex.quote(alter_db_sed)}"
        f" {shlex.quote(str(sql_file))} | {cmd_str}"
    )

    success = await run_command(
        ["bash", "-c", import_cmd],
        f"Import SQL dump into '{db_config['database']}'",
    )

    return success


async def stamp_initial_migration(project_root: Path, database_url: str, revision: str) -> bool:
    """
    Stamp database with a migration revision.

    Args:
        project_root: Project root directory
        database_url: Database URL to use (with localhost instead of mariadb)
        revision: Alembic revision ID to stamp

    Returns:
        True if successful, False otherwise
    """
    # Convert async URL to sync URL for alembic
    sync_url = database_url.replace("mysql+aiomysql://", "mysql+pymysql://")

    # Use alembic's -x option to override the database URL directly
    # This avoids issues with environment variables being overridden by .env
    cmd = ["uv", "run", "alembic", "-x", f"dbUrl={sync_url}", "stamp", revision]

    success = await run_command(
        cmd,
        f"Stamp database with migration ({revision})",
        cwd=project_root,
    )

    return success


async def run_alembic_upgrade(project_root: Path, database_url: str) -> bool:
    """
    Run alembic upgrade head to apply all migrations.

    Args:
        project_root: Project root directory
        database_url: Database URL to use (with localhost instead of mariadb)

    Returns:
        True if successful, False otherwise
    """
    # Convert async URL to sync URL for alembic
    sync_url = database_url.replace("mysql+aiomysql://", "mysql+pymysql://")

    # Use alembic's -x option to override the database URL directly
    # This avoids issues with environment variables being overridden by .env
    cmd = ["uv", "run", "alembic", "-x", f"dbUrl={sync_url}", "upgrade", "head"]

    success = await run_command(
        cmd,
        "Run alembic migrations (upgrade head)",
        cwd=project_root,
    )

    return success


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

    A restore repopulates MySQL and leaves Meilisearch untouched, so search
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
        "api",
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


async def create_test_user(
    db_config: dict[str, str], dry_run: bool = False, use_docker: bool = False
) -> bool:
    """
    Create test users for development/test databases.

    Only creates users if the database name is in DEV_TEST_DATABASES.
    Uses INSERT IGNORE to avoid errors if users already exist.

    Args:
        db_config: Database connection parameters
        dry_run: If True, only show what would be done
        use_docker: If True, run the mariadb client inside the compose
            MariaDB service instead of using the host binary

    Returns:
        True if successful (or skipped for non-dev databases), False on error
    """
    database_name = db_config["database"]

    # Only create test users for dev/test databases
    if database_name not in DEV_TEST_DATABASES:
        print(
            f"Skipping test user creation (database '{database_name}' is not a dev/test database)"
        )
        return True

    print(f"Creating test users for database '{database_name}'...")

    if dry_run:
        for account in TEST_ACCOUNTS:
            role = f"admin={account['admin']}, group={account['group'] or 'none'}"
            print(f"  Would create user: {account['username']} ({role})")
            print(f"  Email: {account['email']}")
            print(f"  Password: {account['password']}")
        return True

    mysql_cmd = _build_mysql_cmd(db_config, use_docker=use_docker)

    for account in TEST_ACCOUNTS:
        hashed_password = _hash_password(account["password"])

        # Insert user (INSERT IGNORE to skip if exists)
        # Note: images_per_page DB default is 10 but app default is 20, so we set it explicitly
        sql = f"""
            INSERT IGNORE INTO users (username, password, password_type, salt, email, active, email_verified, admin, images_per_page)
            VALUES ('{account["username"]}', '{hashed_password}', 'bcrypt', '', '{account["email"]}', 1, 1, {account["admin"]}, 20);
        """

        # Add group assignment if specified
        if account["group"]:
            sql += f"""
            INSERT IGNORE INTO user_groups (user_id, group_id)
            SELECT u.user_id, g.group_id
            FROM users u
            JOIN `groups` g ON g.title = '{account["group"]}'
            WHERE u.username = '{account["username"]}';
            """

        insert_cmd = mysql_cmd + ["-e", sql]

        success = await run_command(
            insert_cmd,
            f"Create test user '{account['username']}'",
        )

        if success:
            role = f"admin={account['admin']}, group={account['group'] or 'none'}"
            print(
                f"  ✓ Test user '{account['username']}' created ({role}, password: {account['password']})"
            )
        else:
            return False

    return True
