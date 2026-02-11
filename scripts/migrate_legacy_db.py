#!/usr/bin/env python3
"""
Complete legacy database migration workflow.

This script orchestrates the full migration process from a restored legacy database
(after Alembic migrations have been applied). It runs all necessary sub-scripts in
the correct order to ensure data consistency.

Migration steps:
1. (Optional) Import legacy SQL dump and run alembic migrations
2. convert_bbcode_to_markdown.py    - Convert BBCode formatting to markdown
3. migrate_quoted_comments.py       - Extract and migrate comment quotes to parent_comment_id
4. normalize_db_text.py             - Clean up remaining HTML entities and whitespace
5. backfill_tag_usage_counts.py     - Backfill tag usage counts from tag_links table
6. analyze_character_sources.py     - Create character-source links from co-occurrence patterns

Usage:
    # Import legacy SQL dump and run full migration
    uv run python scripts/migrate_legacy_db.py --sql-dump /path/to/legacy.sql --auto-confirm

    # Preview all changes without committing (assumes DB already restored)
    uv run python scripts/migrate_legacy_db.py --dry-run

    # Apply all migrations to existing database
    uv run python scripts/migrate_legacy_db.py

    # Run specific step only
    uv run python scripts/migrate_legacy_db.py --step 1
    uv run python scripts/migrate_legacy_db.py --step convert_bbcode_to_markdown

Prerequisites:
    - Database schema must be updated via: uv run alembic upgrade head
    - All sub-scripts must exist in scripts/ directory
    - For SQL import: mysql/mariadb client must be installed
"""

import argparse
import asyncio
import os

import bcrypt
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


# Development/test databases where test user should be created
DEV_TEST_DATABASES = {"shuushuu_dev", "shuushuu_test"}


MIGRATION_STEPS = [
    {
        "number": 1,
        "name": "convert_bbcode_to_markdown",
        "script": "convert_bbcode_to_markdown.py",
        "description": "Convert BBCode formatting to markdown",
    },
    {
        "number": 2,
        "name": "migrate_quoted_comments",
        "script": "migrate_quoted_comments.py",
        "description": "Extract quotes and establish parent-child comment relationships",
    },
    {
        "number": 3,
        "name": "normalize_db_text",
        "script": "normalize_db_text.py",
        "description": "Clean up HTML entities and normalize whitespace",
    },
    {
        "number": 4,
        "name": "backfill_tag_usage_counts",
        "script": "backfill_tag_usage_counts.py",
        "description": "Backfill tag usage counts from tag_links table",
        "skip_standard_flags": True,  # Script has no CLI args (idempotent operation)
    },
    {
        "number": 5,
        "name": "analyze_character_sources",
        "script": "analyze_character_sources.py",
        "description": "Create character-source links from co-occurrence patterns",
        # This script has different flags: --create-links instead of --dry-run
        # In dry-run mode, we run without --create-links (analysis only)
        # In live mode, we add --create-links to actually create the links
        "args": ["--threshold", "0.8", "--min-images", "5", "--user-id", "2"],
        "live_args": ["--create-links"],  # Additional args for live mode only
        "skip_standard_flags": True,  # Don't add --dry-run/--auto-confirm
    },
]

# Initial migration revision ID (from 8d66158eb568_initial_schema_from_existing_database.py)
INITIAL_MIGRATION_REVISION = "8d66158eb568"


def print_header(text: str, width: int = 80):
    """Print a formatted header."""
    print("\n" + "=" * width)
    print(text.center(width))
    print("=" * width + "\n")


def print_step(step_num: int, step: dict):
    """Print step information."""
    print(f"Step {step_num}: {step['name']}")
    print(f"  Script: {step['script']}")
    print(f"  Description: {step['description']}\n")


def find_script(script_name: str) -> Path | None:
    """Find script in scripts directory."""
    script_path = Path(__file__).parent / script_name
    if script_path.exists():
        return script_path
    return None


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


async def run_command(cmd: list[str], description: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> bool:
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
        )

        if result.returncode != 0:
            print(f"\n❌ Command failed with exit code {result.returncode}")
            return False

        print(f"\n✓ {description} completed successfully")
        return True

    except Exception as e:
        print(f"❌ Error running command: {e}")
        return False


async def drop_and_create_database(db_config: dict[str, str]) -> bool:
    """
    Drop and recreate the database.

    Args:
        db_config: Database connection parameters

    Returns:
        True if successful, False otherwise
    """
    database_name = db_config["database"]

    print(f"⚠️  Dropping database '{database_name}' if it exists...")

    # Replace Docker hostname 'mariadb' with 'localhost' when running from host
    host = db_config['host']
    if host == 'mariadb':
        host = 'localhost'
        print(f"Note: Replacing Docker hostname 'mariadb' with 'localhost' for host execution")

    # Build mysql command for drop/create
    mysql_cmd = [
        "mariadb",
        f"--host={host}",
        f"--port={db_config['port']}",
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
        drop_cmd,
        f"Drop and create database '{database_name}'",
    )

    return success


async def import_sql_dump(sql_file: Path, db_config: dict[str, str]) -> bool:
    """
    Import SQL dump file into database.

    Args:
        sql_file: Path to SQL dump file
        db_config: Database connection parameters

    Returns:
        True if successful, False otherwise
    """
    if not sql_file.exists():
        print(f"❌ SQL dump file not found: {sql_file}")
        return False

    print(f"Importing SQL dump from: {sql_file}")
    print(f"Into database: {db_config['database']}")

    # Replace Docker hostname 'mariadb' with 'localhost' when running from host
    host = db_config['host']
    if host == 'mariadb':
        host = 'localhost'

    # Build mysql import command with optimizations for large imports
    # Use 1GB for max-allowed-packet (in bytes)
    mysql_cmd = [
        "mariadb",
        f"--host={host}",
        f"--port={db_config['port']}",
        f"--user={db_config['user']}",
        f"--password={db_config['password']}" if db_config["password"] else None,
        db_config["database"],
        "--init-command=SET SESSION FOREIGN_KEY_CHECKS=0; SET SESSION UNIQUE_CHECKS=0; SET autocommit=0;",
        "--max-allowed-packet=1073741824",  # 1GB in bytes
    ]

    # Filter out None values
    mysql_cmd = [arg for arg in mysql_cmd if arg is not None]

    # Build the full command with proper shell quoting
    # Use stdin redirection for the SQL file
    cmd_str = " ".join(f'"{arg}"' if " " in arg or ";" in arg else arg for arg in mysql_cmd)
    import_cmd = f"{cmd_str} < {sql_file}"

    success = await run_command(
        ["bash", "-c", import_cmd],
        f"Import SQL dump into '{db_config['database']}'",
    )

    return success


async def stamp_initial_migration(project_root: Path, database_url: str) -> bool:
    """
    Stamp database with initial migration revision.

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
    cmd = [
        "uv", "run", "alembic",
        "-x", f"dbUrl={sync_url}",
        "stamp", INITIAL_MIGRATION_REVISION
    ]

    success = await run_command(
        cmd,
        f"Stamp database with initial migration ({INITIAL_MIGRATION_REVISION})",
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
    cmd = [
        "uv", "run", "alembic",
        "-x", f"dbUrl={sync_url}",
        "upgrade", "head"
    ]

    success = await run_command(
        cmd,
        "Run alembic migrations (upgrade head)",
        cwd=project_root,
    )

    return success


def _build_mysql_cmd(db_config: dict[str, str]) -> list[str]:
    """Build base mariadb CLI command from db config."""
    host = db_config["host"]
    if host == "mariadb":
        host = "localhost"

    cmd = [
        "mariadb",
        f"--host={host}",
        f"--port={db_config['port']}",
        f"--user={db_config['user']}",
    ]

    if db_config["password"]:
        cmd.append(f"--password={db_config['password']}")

    cmd.append(db_config["database"])
    return cmd


def _hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


# Test accounts to create in dev/test databases
TEST_ACCOUNTS = [
    {
        "username": "test1",
        "password": "shuutest1",
        "email": "test1@shuushuu.com",
        "admin": 0,
        "group": None,
    },
    {
        "username": "testadmin",
        "password": "shuutestadmin",
        "email": "testadmin@shuushuu.com",
        "admin": 1,
        "group": "mods",
    },
]


async def create_test_user(db_config: dict[str, str], dry_run: bool = False) -> bool:
    """
    Create test users for development/test databases.

    Only creates users if the database name is in DEV_TEST_DATABASES.
    Uses INSERT IGNORE to avoid errors if users already exist.

    Args:
        db_config: Database connection parameters
        dry_run: If True, only show what would be done

    Returns:
        True if successful (or skipped for non-dev databases), False on error
    """
    database_name = db_config["database"]

    # Only create test users for dev/test databases
    if database_name not in DEV_TEST_DATABASES:
        print(f"Skipping test user creation (database '{database_name}' is not a dev/test database)")
        return True

    print(f"Creating test users for database '{database_name}'...")

    if dry_run:
        for account in TEST_ACCOUNTS:
            role = f"admin, group={account['group']}" if account["admin"] else "regular user"
            print(f"  Would create user: {account['username']} ({role})")
            print(f"  Email: {account['email']}")
            print(f"  Password: {account['password']}")
        return True

    mysql_cmd = _build_mysql_cmd(db_config)

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
            role = f"admin, group={account['group']}" if account["admin"] else "regular user"
            print(f"  ✓ Test user '{account['username']}' created ({role}, password: {account['password']})")
        else:
            return False

    return True


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


async def run_step(step: dict, dry_run: bool = False, auto_confirm: bool = False, database_url: str | None = None) -> bool:
    """
    Run a migration step script.

    Args:
        step: Migration step configuration
        dry_run: Preview mode flag
        auto_confirm: Skip confirmation prompts
        database_url: Optional database URL override (for localhost)

    Returns True if successful, False otherwise.
    """
    script_path = find_script(step["script"])

    if not script_path:
        print(f"❌ Error: Script not found: {step['script']}")
        return False

    try:
        # Build command
        cmd = ["uv", "run", "python", str(script_path)]

        # Add standard flags unless step opts out
        if not step.get("skip_standard_flags", False):
            if dry_run:
                cmd.append("--dry-run")
            else:
                cmd.append("--no-dry-run")
            if auto_confirm:
                cmd.append("--auto-confirm")

        # Add step-specific base args
        if "args" in step:
            cmd.extend(step["args"])

        # Add live-mode-only args (e.g., --create-links for analyze_character_sources)
        if not dry_run and "live_args" in step:
            cmd.extend(step["live_args"])

        print(f"Running: {' '.join(cmd)}\n")

        # Run subprocess from project root (not scripts directory)
        # This ensures .env file is found correctly
        project_root = script_path.parent.parent

        # Prepare environment variables
        command_env = os.environ.copy()
        if database_url:
            # Override DATABASE_URL to use localhost instead of mariadb
            sync_url = database_url.replace("mysql+aiomysql://", "mysql+pymysql://")
            command_env["DATABASE_URL"] = database_url
            command_env["DATABASE_URL_SYNC"] = sync_url

        result = subprocess.run(cmd, cwd=project_root, check=False, env=command_env)

        if result.returncode != 0:
            print(f"\n❌ Step failed with exit code {result.returncode}")
            return False

        print(f"\n✓ Step completed successfully")
        return True

    except Exception as e:
        print(f"❌ Error running step: {e}")
        return False


async def run_migration(
    step_filter: int | str | None = None,
    dry_run: bool = False,
    auto_confirm: bool = False,
    sql_dump: Path | None = None,
) -> bool:
    """
    Run the complete migration workflow or a specific step.

    Args:
        step_filter: Run specific step (by number or name), or None for all
        dry_run: Preview changes without committing
        auto_confirm: Skip confirmation prompts (for CI/CD)
        sql_dump: Path to SQL dump file for import (optional)

    Returns:
        True if all steps succeeded, False otherwise
    """
    print_header("Legacy Database Migration Workflow", width=80)

    project_root = Path(__file__).parent.parent

    # ===== SQL Import and Schema Migration (if requested) =====
    if sql_dump:
        print_header("Phase 1: Import Legacy SQL Dump", width=80)

        # Get database configuration
        database_url = get_database_url()
        if not database_url:
            print("❌ Error: DATABASE_URL not found in .env or environment")
            return False

        db_config = parse_database_url(database_url)
        print(f"Target database: {db_config['database']}")
        print(f"Host: {db_config['host']}:{db_config['port']}")
        print(f"User: {db_config['user']}\n")

        if not auto_confirm:
            print(f"⚠️  WARNING: This will DROP and recreate database '{db_config['database']}'")
            print("⚠️  WARNING: This will STOP the API and worker containers during migration")
            response = input("Continue? (yes/no): ")
            if response.lower() != "yes":
                print("Migration cancelled.")
                return False

        # Step 0: Stop Docker services to prevent connection conflicts
        print("\n" + "=" * 80)
        print("[0/4] Stopping Docker services")
        print("=" * 80)
        if not await stop_docker_services(project_root):
            print("⚠️  Warning: Failed to stop Docker services (continuing anyway)")

        # Step 1: Drop and create database
        print("\n" + "=" * 80)
        print("[1/4] Dropping and recreating database")
        print("=" * 80)
        if not await drop_and_create_database(db_config):
            print("❌ Failed to drop/create database")
            return False

        # Step 2: Import SQL dump
        print("\n" + "=" * 80)
        print("[2/4] Importing SQL dump")
        print("=" * 80)
        if not await import_sql_dump(sql_dump, db_config):
            print("❌ Failed to import SQL dump")
            return False

        # Step 3: Stamp with initial migration
        print("\n" + "=" * 80)
        print("[3/4] Stamping database with initial migration")
        print("=" * 80)

        # Create localhost version of DATABASE_URL for alembic
        localhost_db_url = database_url.replace("@mariadb:", "@localhost:")
        print(f"Using DATABASE_URL: {localhost_db_url.replace(db_config['password'], '***')}\n")

        if not await stamp_initial_migration(project_root, localhost_db_url):
            print("❌ Failed to stamp initial migration")
            return False

        # Step 4: Run remaining alembic migrations
        print("\n" + "=" * 80)
        print("[4/4] Running alembic migrations")
        print("=" * 80)
        if not await run_alembic_upgrade(project_root, localhost_db_url):
            print("❌ Failed to run alembic migrations")
            return False

        # Create test user for dev/test databases
        print("\n" + "=" * 80)
        print("Creating test user (if dev/test database)")
        print("=" * 80)
        if not await create_test_user(db_config, dry_run=dry_run):
            print("⚠️  Warning: Failed to create test user (continuing anyway)")

        print_header("Phase 1 Complete: Database Schema Ready", width=80)
        print()

    # ===== Create test user for dev/test databases (if not done in Phase 1) =====
    if not sql_dump:
        database_url = get_database_url()
        if database_url:
            db_config = parse_database_url(database_url)
            if db_config["database"] in DEV_TEST_DATABASES:
                print_header("Creating Test User", width=80)
                if not await create_test_user(db_config, dry_run=dry_run):
                    print("⚠️  Warning: Failed to create test user (continuing anyway)")
                print()

    # ===== Data Migration Steps =====
    print_header("Phase 2: Data Migrations", width=80)

    # Determine which steps to run
    if step_filter is not None:
        if isinstance(step_filter, int):
            steps_to_run = [s for s in MIGRATION_STEPS if s["number"] == step_filter]
        else:
            steps_to_run = [s for s in MIGRATION_STEPS if s["name"] == step_filter]

        if not steps_to_run:
            print(f"❌ Error: Step '{step_filter}' not found")
            print("\nAvailable steps:")
            for step in MIGRATION_STEPS:
                print(f"  {step['number']}: {step['name']}")
            return False
    else:
        steps_to_run = MIGRATION_STEPS

    # Show what will be run
    print("Steps to run:\n")
    for step in steps_to_run:
        print_step(step["number"], step)

    # Show mode
    if dry_run:
        print("Mode: 🔍 DRY RUN (no changes will be committed)")
    else:
        print("Mode: ⚠️  LIVE MODE (changes will be committed)")
    print()

    # Get confirmation unless auto-confirmed
    if not auto_confirm:
        if dry_run:
            response = input("Continue with dry run? (yes/no): ")
        else:
            response = input("Continue with LIVE migration? (yes/no): ")

        if response.lower() != "yes":
            print("Migration cancelled.")
            return False

    # Run each step
    print_header("Running Migration Steps", width=80)

    # Get localhost database URL if we did a SQL import, otherwise None
    localhost_db_url = None
    if sql_dump:
        database_url = get_database_url()
        localhost_db_url = database_url.replace("@mariadb:", "@localhost:")

    all_succeeded = True
    for i, step in enumerate(steps_to_run, 1):
        print(f"\n[{i}/{len(steps_to_run)}] Running: {step['name']}")
        print("-" * 80)

        success = await run_step(
            step,
            dry_run=dry_run,
            auto_confirm=auto_confirm,
            database_url=localhost_db_url
        )

        if not success:
            all_succeeded = False
            print(f"\n⚠️  Migration stopped at step {step['number']}")
            break

    # Summary
    print_header("Migration Summary", width=80)

    if all_succeeded:
        if dry_run:
            print("✓ Dry run completed successfully!")
            print("\nRun without --dry-run to apply these changes:")
            print(f"  uv run python {Path(__file__).name}")
        else:
            print("✓ Migration completed successfully!")

            # Restart Docker services if we stopped them
            if sql_dump:
                print("\n" + "=" * 80)
                print("Restarting Docker services")
                print("=" * 80)
                if await start_docker_services(project_root):
                    print("✅ Docker services restarted successfully")
                else:
                    print("⚠️  Warning: Failed to restart Docker services")
                    print("You may need to manually restart: docker compose start api arq-worker")

            print("\nNext steps:")
            print("  1. Verify data integrity in database")
            print("  2. Check frontend rendering of comments")
            print("  3. Review any failed/unmatched records")
    else:
        print("❌ Migration failed!")
        print("\nPlease investigate the errors above and retry.")

        # Try to restart Docker services even if migration failed
        if sql_dump:
            print("\n⚠️  Attempting to restart Docker services...")
            await start_docker_services(project_root)

    return all_succeeded


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Orchestrate complete legacy database migration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Import legacy SQL dump and run full migration pipeline (non-interactive)
  uv run python scripts/migrate_legacy_db.py --sql-dump /path/to/legacy.sql --auto-confirm

  # Import legacy SQL dump with dry-run preview of data migrations
  uv run python scripts/migrate_legacy_db.py --sql-dump /path/to/legacy.sql --dry-run --auto-confirm

  # Preview data migrations only (assumes DB already restored)
  uv run python scripts/migrate_legacy_db.py --dry-run

  # Apply all data migrations to existing database
  uv run python scripts/migrate_legacy_db.py --auto-confirm

  # Run only step 1
  uv run python scripts/migrate_legacy_db.py --step 1 --auto-confirm

  # Run only BBCode conversion
  uv run python scripts/migrate_legacy_db.py --step convert_bbcode_to_markdown --auto-confirm

  # List all available migration steps
  uv run python scripts/migrate_legacy_db.py --list-steps
        """,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview all changes without committing to database",
    )

    parser.add_argument(
        "--step",
        type=str,
        help="Run specific step (by number 1-5 or name)",
    )

    parser.add_argument(
        "--auto-confirm",
        action="store_true",
        help="Skip confirmation prompts (useful for CI/CD)",
    )

    parser.add_argument(
        "--list-steps",
        action="store_true",
        help="List all available migration steps",
    )

    parser.add_argument(
        "--sql-dump",
        type=str,
        help="Path to legacy SQL dump file. If provided, will drop/create DB, import dump, and run alembic migrations before data migrations",
    )

    args = parser.parse_args()

    # List steps and exit
    if args.list_steps:
        print_header("Available Migration Steps")
        for step in MIGRATION_STEPS:
            print_step(step["number"], step)
        return

    # Convert step argument to int if it's a number
    step_filter = None
    if args.step:
        try:
            step_filter = int(args.step)
        except ValueError:
            step_filter = args.step

    # Convert SQL dump path to Path object if provided
    sql_dump_path = None
    if args.sql_dump:
        sql_dump_path = Path(args.sql_dump)
        if not sql_dump_path.exists():
            print(f"❌ Error: SQL dump file not found: {sql_dump_path}")
            sys.exit(1)

    # Run migration
    success = await run_migration(
        step_filter=step_filter,
        dry_run=args.dry_run,
        auto_confirm=args.auto_confirm,
        sql_dump=sql_dump_path,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
