#!/usr/bin/env python3
"""
Restore a production database dump into the local dev environment.

Unlike migrate_legacy_db.py, this script does NOT run legacy data migrations
(BBCode conversion, comment quote migration, text normalization, etc.) because
the prod database has already been migrated to the new schema.

The mariadb client steps (drop/create, import, test users) run inside the
running 'mariadb' Docker Compose service via `docker compose exec`, so a host
mariadb binary is not required. The mariadb container must be up; only the api
and arq-worker services are stopped during the restore. The alembic step still
runs on the host and connects to the published port on localhost.

Workflow:
1. Stop Docker API/worker services
2. Drop and recreate database
3. Import SQL dump (prod already has alembic_version stamped)
4. alembic upgrade head (apply any migrations not yet on prod)
5. Create test users (dev/test databases only)
6. Restart Docker services
7. Reindex Meilisearch from the restored database (--skip-reindex to opt out)

A dump restores the database and nothing else, so the derived indexes are left
describing the data that was there before. Step 7 rebuilds the Meilisearch tags
index for that reason; it runs inside the api container and never fails the
restore, since the database is already in place by then.

IQDB is NOT rebuilt here — it is a long job over every image, so it stays an
explicit choice. The summary says so, because an empty index is invisible in
normal use: search and duplicate detection simply return nothing.

    docker compose exec api uv run --no-project python scripts/populate_iqdb.py

Usage:
    uv run python scripts/restore_prod_db.py /path/to/prod.sql
    uv run python scripts/restore_prod_db.py /path/to/prod.sql --dry-run
    uv run python scripts/restore_prod_db.py /path/to/prod.sql --auto-confirm
    uv run python scripts/restore_prod_db.py /path/to/prod.sql --skip-reindex
"""

import argparse
import asyncio
import sys
from pathlib import Path

from db_utils import (  # type: ignore[import-not-found]
    create_test_user,
    drop_and_create_database,
    get_database_url,
    import_sql_dump,
    parse_database_url,
    print_header,
    reindex_search,
    run_alembic_upgrade,
    start_docker_services,
    stop_docker_services,
)


async def restore_prod_db(
    sql_file: Path,
    dry_run: bool = False,
    auto_confirm: bool = False,
    skip_reindex: bool = False,
) -> bool:
    """
    Restore a production database dump.

    Args:
        sql_file: Path to the prod SQL dump file
        dry_run: If True, only show what would be done
        auto_confirm: If True, skip confirmation prompts
        skip_reindex: If True, leave the Meilisearch index untouched

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
        print("  6. Restart Docker services")
        if skip_reindex:
            print("  7. Reindex Meilisearch (SKIPPED via --skip-reindex)")
        else:
            print("  7. Reindex Meilisearch from the restored database")
        return True

    if not auto_confirm:
        print(f"⚠️  WARNING: This will DROP and recreate database '{db_config['database']}'")
        print("⚠️  WARNING: This will STOP the API and worker containers during restore")
        response = input("\nContinue? (yes/no): ")
        if response.lower() != "yes":
            print("Restore cancelled.")
            return False

    total_steps = 7
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
    if not await drop_and_create_database(db_config, use_docker=True):
        print("❌ Failed to drop/create database")
        print("\n⚠️  Attempting to restart Docker services...")
        await start_docker_services(project_root)
        return False

    # Step 3: Import SQL dump
    print("\n" + "=" * 80)
    print(f"[3/{total_steps}] Importing SQL dump")
    print("=" * 80)
    if not await import_sql_dump(sql_file, db_config, use_docker=True):
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
    if not await create_test_user(db_config, use_docker=True):
        print("⚠️  Warning: Failed to create test users (continuing anyway)")

    # Step 6: Restart Docker services
    print("\n" + "=" * 80)
    print(f"[6/{total_steps}] Restarting Docker services")
    print("=" * 80)
    services_up = await start_docker_services(project_root)
    if services_up:
        print("✅ Docker services restarted successfully")
    else:
        print("⚠️  Warning: Failed to restart Docker services")
        print("You may need to manually restart: docker compose start api arq-worker")
        success = False

    # Step 7: Rebuild the search index. Runs after the restart because it
    # executes inside the api container. Never fatal: the database is already
    # restored by this point, and a stale index is a warning, not a rollback.
    search_reindexed = False
    if skip_reindex:
        print("\n" + "=" * 80)
        print(f"[{total_steps}/{total_steps}] Reindexing search (skipped)")
        print("=" * 80)
        print("Skipped via --skip-reindex.")
    elif not services_up:
        print("\n" + "=" * 80)
        print(f"[{total_steps}/{total_steps}] Reindexing search (skipped)")
        print("=" * 80)
        print("⚠️  Skipped: the api container is not running.")
    else:
        print("\n" + "=" * 80)
        print(f"[{total_steps}/{total_steps}] Reindexing search")
        print("=" * 80)
        search_reindexed = await reindex_search(project_root)
        if not search_reindexed:
            print("⚠️  Warning: search reindex failed (continuing anyway)")

    # Summary
    print_header("Restore Summary", width=80)
    if success:
        print("✓ Production database restored successfully!")
        print(f"\nDatabase '{db_config['database']}' is ready for use.")
    else:
        print("⚠️  Restore completed with warnings (see above)")

    # Derived indexes do not come back with the dump. Say so plainly either
    # way: an empty index is invisible in normal use — search and duplicate
    # detection just quietly return nothing.
    print("\nDerived indexes:")
    if search_reindexed:
        print("  ✓ Meilisearch reindexed from the restored database")
    else:
        print("  ⚠️  Meilisearch NOT reindexed — search will return stale or no results.")
        print("     Rebuild: docker compose exec api uv run --no-project \\")
        print("              python scripts/reindex_search.py")
    print("  ⚠️  IQDB is not rebuilt by this script — duplicate detection will")
    print("     find nothing until it is populated. This is a long job over every")
    print("     image, so run it deliberately:")
    print("     docker compose exec api uv run --no-project python scripts/populate_iqdb.py")

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

  # Restore without rebuilding the search index
  uv run python scripts/restore_prod_db.py /path/to/prod.sql --skip-reindex
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

    parser.add_argument(
        "--skip-reindex",
        action="store_true",
        help="Leave the Meilisearch index untouched (it will be stale)",
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
        skip_reindex=args.skip_reindex,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
