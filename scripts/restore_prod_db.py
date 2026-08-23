#!/usr/bin/env python3
"""
Restore a plain-format pg_dump of production into the local dev environment.

Every database step runs inside the compose 'postgres' service via
`docker compose exec`, so no host psql is needed (and an older host psql
would reject the \\restrict guard a pg_dump 18 emits). The target database is
the one compose created from POSTGRES_DB / POSTGRES_USER (.env or environment;
defaults match docker-compose.yml). Only the api and arq-worker services are
stopped during the restore.

Workflow:
1. Stop Docker API/worker services
2. Drop and recreate database
3. Import the dump (single transaction; the dump's owner roles are created
   NOLOGIN first so its ALTER ... OWNER TO statements succeed)
4. Reassign ownership to the dev role and ANALYZE (a dump carries no planner
   statistics)
5. alembic upgrade head (apply any Postgres-chain migrations not yet on prod)
6. Create test users
7. Restart Docker services
8. Reindex Meilisearch from the restored database (--skip-reindex to opt out)

A dump restores the database and nothing else, so the derived indexes are left
describing the data that was there before. Step 8 rebuilds the Meilisearch tags
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
    analyze_database,
    create_test_users,
    drop_and_create_database,
    dump_owner_roles,
    ensure_roles,
    import_sql_dump,
    load_db_config,
    print_header,
    reassign_ownership,
    reindex_search,
    run_alembic_upgrade,
    start_docker_services,
    stop_docker_services,
)


def _step(number: int, total: int, title: str) -> None:
    print("\n" + "=" * 80)
    print(f"[{number}/{total}] {title}")
    print("=" * 80)


async def _abort(project_root: Path, message: str) -> bool:
    print(f"❌ {message}")
    print("\n⚠️  Attempting to restart Docker services...")
    await start_docker_services(project_root)
    return False


async def restore_prod_db(
    sql_file: Path,
    dry_run: bool = False,
    auto_confirm: bool = False,
    skip_reindex: bool = False,
) -> bool:
    """
    Restore a production database dump.

    Args:
        sql_file: Path to the prod pg_dump (plain format) file
        dry_run: If True, only show what would be done
        auto_confirm: If True, skip confirmation prompts
        skip_reindex: If True, leave the Meilisearch index untouched

    Returns:
        True if successful, False otherwise
    """
    print_header("Restore Production Database", width=80)

    project_root = Path(__file__).parent.parent
    db_config = load_db_config(project_root)
    foreign_roles = dump_owner_roles(sql_file) - {db_config["user"]}

    print(f"SQL dump:        {sql_file}")
    print(f"Target database: {db_config['database']} (compose service 'postgres')")
    print(f"Owner role:      {db_config['user']}")
    if foreign_roles:
        print(f"Dump owner(s):   {', '.join(sorted(foreign_roles))} (objects will be reassigned)")
    print()

    if dry_run:
        print("Mode: 🔍 DRY RUN (no changes will be made)\n")
        print("Steps that would be executed:")
        print("  1. Stop Docker API/worker services")
        print(f"  2. Drop and recreate database '{db_config['database']}'")
        print(f"  3. Import SQL dump: {sql_file}")
        print(f"  4. Reassign ownership to '{db_config['user']}' and ANALYZE")
        print("  5. Run alembic upgrade head (Postgres chain)")
        print("  6. Create test users")
        print("  7. Restart Docker services")
        if skip_reindex:
            print("  8. Reindex Meilisearch (SKIPPED via --skip-reindex)")
        else:
            print("  8. Reindex Meilisearch from the restored database")
        return True

    if not auto_confirm:
        print(f"⚠️  WARNING: This will DROP and recreate database '{db_config['database']}'")
        print("⚠️  WARNING: This will STOP the API and worker containers during restore")
        response = input("\nContinue? (yes/no): ")
        if response.lower() != "yes":
            print("Restore cancelled.")
            return False

    total_steps = 8
    success = True

    _step(1, total_steps, "Stopping Docker services")
    if not await stop_docker_services(project_root):
        print("⚠️  Warning: Failed to stop Docker services (continuing anyway)")

    _step(2, total_steps, "Dropping and recreating database")
    if not await drop_and_create_database(db_config):
        return await _abort(project_root, "Failed to drop/create database")

    _step(3, total_steps, "Importing SQL dump")
    if not await ensure_roles(foreign_roles, db_config):
        return await _abort(project_root, "Failed to create the dump's owner roles")
    if not await import_sql_dump(sql_file, db_config):
        return await _abort(project_root, "Failed to import SQL dump")

    _step(4, total_steps, "Reassigning ownership and analyzing")
    if not await reassign_ownership(foreign_roles, db_config):
        return await _abort(project_root, "Failed to reassign ownership")
    if not await analyze_database(db_config):
        print("⚠️  Warning: ANALYZE failed (continuing; autovacuum will catch up)")

    _step(5, total_steps, "Running alembic migrations")
    if not await run_alembic_upgrade(project_root):
        return await _abort(project_root, "Failed to run alembic migrations")

    _step(6, total_steps, "Creating test users")
    if not await create_test_users(project_root):
        print("⚠️  Warning: Failed to create test users (continuing anyway)")

    _step(7, total_steps, "Restarting Docker services")
    services_up = await start_docker_services(project_root)
    if services_up:
        print("✅ Docker services restarted successfully")
    else:
        print("⚠️  Warning: Failed to restart Docker services")
        print("You may need to manually restart: docker compose start api arq-worker")
        success = False

    # Step 8: Rebuild the search index. Runs after the restart because it
    # executes inside the api container. Never fatal: the database is already
    # restored by this point, and a stale index is a warning, not a rollback.
    search_reindexed = False
    if skip_reindex:
        _step(total_steps, total_steps, "Reindexing search (skipped)")
        print("Skipped via --skip-reindex.")
    elif not services_up:
        _step(total_steps, total_steps, "Reindexing search (skipped)")
        print("⚠️  Skipped: the api container is not running.")
    else:
        _step(total_steps, total_steps, "Reindexing search")
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
        description="Restore a production pg_dump into the local dev environment",
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
        help="Path to the production pg_dump file (plain format)",
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
