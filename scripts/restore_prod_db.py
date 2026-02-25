#!/usr/bin/env python3
"""
Restore a production database dump into the local dev environment.

Unlike migrate_legacy_db.py, this script does NOT run legacy data migrations
(BBCode conversion, comment quote migration, text normalization, etc.) because
the prod database has already been migrated to the new schema.

Workflow:
1. Stop Docker API/worker services
2. Drop and recreate database
3. Import SQL dump (prod already has alembic_version stamped)
4. alembic upgrade head (apply any migrations not yet on prod)
5. Create test users (dev/test databases only)
6. Start Docker services

Usage:
    uv run python scripts/restore_prod_db.py /path/to/prod.sql
    uv run python scripts/restore_prod_db.py /path/to/prod.sql --dry-run
    uv run python scripts/restore_prod_db.py /path/to/prod.sql --auto-confirm
"""

import argparse
import asyncio
import sys
from pathlib import Path

from db_utils import (
    create_test_user,
    drop_and_create_database,
    get_database_url,
    import_sql_dump,
    parse_database_url,
    print_header,
    run_alembic_upgrade,
    start_docker_services,
    stop_docker_services,
)


async def restore_prod_db(
    sql_file: Path,
    dry_run: bool = False,
    auto_confirm: bool = False,
) -> bool:
    """
    Restore a production database dump.

    Args:
        sql_file: Path to the prod SQL dump file
        dry_run: If True, only show what would be done
        auto_confirm: If True, skip confirmation prompts

    Returns:
        True if successful, False otherwise
    """
    print_header("Restore Production Database", width=80)

    project_root = Path(__file__).parent.parent

    # Get database configuration
    database_url = get_database_url()
    if not database_url:
        print("❌ Error: DATABASE_URL not found in .env or environment")
        return False

    db_config = parse_database_url(database_url)
    localhost_db_url = database_url.replace("@mariadb:", "@localhost:")

    print(f"SQL dump:        {sql_file}")
    print(f"Target database: {db_config['database']}")
    print(f"Host:            {db_config['host']}:{db_config['port']}")
    print(f"User:            {db_config['user']}\n")

    if dry_run:
        print("Mode: 🔍 DRY RUN (no changes will be made)\n")
        print("Steps that would be executed:")
        print("  1. Stop Docker API/worker services")
        print(f"  2. Drop and recreate database '{db_config['database']}'")
        print(f"  3. Import SQL dump: {sql_file}")
        print("  4. Run alembic upgrade head")
        print("  5. Create test users (if dev/test database)")
        print("  6. Start Docker services")
        return True

    if not auto_confirm:
        print(f"⚠️  WARNING: This will DROP and recreate database '{db_config['database']}'")
        print("⚠️  WARNING: This will STOP the API and worker containers during restore")
        response = input("\nContinue? (yes/no): ")
        if response.lower() != "yes":
            print("Restore cancelled.")
            return False

    total_steps = 5
    success = True

    # Step 1: Stop Docker services
    print("\n" + "=" * 80)
    print(f"[1/{total_steps}] Stopping Docker services")
    print("=" * 80)
    if not await stop_docker_services(project_root):
        print("⚠️  Warning: Failed to stop Docker services (continuing anyway)")

    # Step 2: Drop and recreate database
    print("\n" + "=" * 80)
    print(f"[2/{total_steps}] Dropping and recreating database")
    print("=" * 80)
    if not await drop_and_create_database(db_config):
        print("❌ Failed to drop/create database")
        print("\n⚠️  Attempting to restart Docker services...")
        await start_docker_services(project_root)
        return False

    # Step 3: Import SQL dump
    print("\n" + "=" * 80)
    print(f"[3/{total_steps}] Importing SQL dump")
    print("=" * 80)
    if not await import_sql_dump(sql_file, db_config):
        print("❌ Failed to import SQL dump")
        print("\n⚠️  Attempting to restart Docker services...")
        await start_docker_services(project_root)
        return False

    # Step 4: Run alembic upgrade head
    print("\n" + "=" * 80)
    print(f"[4/{total_steps}] Running alembic migrations")
    print("=" * 80)
    print(f"Using DATABASE_URL: {localhost_db_url.replace(db_config['password'], '***')}\n")
    if not await run_alembic_upgrade(project_root, localhost_db_url):
        print("❌ Failed to run alembic migrations")
        print("\n⚠️  Attempting to restart Docker services...")
        await start_docker_services(project_root)
        return False

    # Step 5: Create test users (dev/test databases only)
    print("\n" + "=" * 80)
    print(f"[5/{total_steps}] Creating test users (if dev/test database)")
    print("=" * 80)
    if not await create_test_user(db_config):
        print("⚠️  Warning: Failed to create test users (continuing anyway)")

    # Restart Docker services
    print("\n" + "=" * 80)
    print("Restarting Docker services")
    print("=" * 80)
    if await start_docker_services(project_root):
        print("✅ Docker services restarted successfully")
    else:
        print("⚠️  Warning: Failed to restart Docker services")
        print("You may need to manually restart: docker compose start api arq-worker")
        success = False

    # Summary
    print_header("Restore Summary", width=80)
    if success:
        print("✓ Production database restored successfully!")
        print(f"\nDatabase '{db_config['database']}' is ready for use.")
    else:
        print("⚠️  Restore completed with warnings (see above)")

    return success


async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Restore a production database dump into the local dev environment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Restore prod dump (interactive)
  uv run python scripts/restore_prod_db.py /path/to/prod.sql

  # Restore prod dump (non-interactive)
  uv run python scripts/restore_prod_db.py /path/to/prod.sql --auto-confirm

  # Preview what would happen
  uv run python scripts/restore_prod_db.py /path/to/prod.sql --dry-run
        """,
    )

    parser.add_argument(
        "sql_file",
        type=str,
        help="Path to the production SQL dump file",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would happen without making changes",
    )

    parser.add_argument(
        "--auto-confirm",
        action="store_true",
        help="Skip confirmation prompts",
    )

    args = parser.parse_args()

    sql_file = Path(args.sql_file)
    if not sql_file.exists():
        print(f"❌ Error: SQL dump file not found: {sql_file}")
        sys.exit(1)

    success = await restore_prod_db(
        sql_file=sql_file,
        dry_run=args.dry_run,
        auto_confirm=args.auto_confirm,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
