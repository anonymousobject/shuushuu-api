#!/usr/bin/env python3
"""
Compare database schemas between different environments.

This script compares:
- shuushuu_migration_test (fresh from SQLModel + migrations)
- shuushuu (current dev/prod database)
- Optional: schema.sql (original PHP schema)

Usage:
    uv run python scripts/compare_schemas.py
"""

import os
import sys
from collections import defaultdict
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text


def get_table_schema(engine, db_name: str) -> dict:
    """Get schema information for all tables in a database."""
    schema = {}

    with engine.connect() as conn:
        # Get all tables
        result = conn.execute(text("SHOW TABLES"))
        tables = [row[0] for row in result]

        for table in tables:
            # Get columns for this table
            result = conn.execute(text(f"DESCRIBE {table}"))
            columns = []
            for row in result:
                columns.append(
                    {
                        "field": row[0],
                        "type": row[1],
                        "null": row[2],
                        "key": row[3],
                        "default": row[4],
                        "extra": row[5],
                    }
                )

            # Get indexes
            result = conn.execute(text(f"SHOW INDEX FROM {table}"))
            indexes = defaultdict(list)
            for row in result:
                idx_name = row[2]  # Key_name
                if idx_name != "PRIMARY":  # Skip PRIMARY (shown in column info)
                    indexes[idx_name].append(row[4])  # Column_name

            schema[table] = {"columns": columns, "indexes": dict(indexes)}

    return schema


def compare_tables(test_schema: dict, dev_schema: dict) -> None:
    """Compare tables between two schemas."""
    test_tables = set(test_schema.keys())
    dev_tables = set(dev_schema.keys())

    # Tables only in test
    only_test = test_tables - dev_tables
    if only_test:
        print("📋 Tables ONLY in migration_test (new tables):")
        for table in sorted(only_test):
            print(f"   + {table}")
        print()

    # Tables only in dev
    only_dev = dev_tables - test_tables
    if only_dev:
        print("📋 Tables ONLY in dev (missing from SQLModel):")
        for table in sorted(only_dev):
            print(f"   - {table}")
        print()

    # Common tables
    common = test_tables & dev_tables
    if not only_test and not only_dev:
        print(f"✅ All {len(common)} tables present in both databases")
        print()


def compare_columns(table: str, test_cols: list, dev_cols: list, show_all: bool = False) -> None:
    """Compare columns for a specific table."""
    test_fields = {col["field"]: col for col in test_cols}
    dev_fields = {col["field"]: col for col in dev_cols}

    test_field_names = set(test_fields.keys())
    dev_field_names = set(dev_fields.keys())

    differences = []

    # Fields only in test
    only_test = test_field_names - dev_field_names
    if only_test:
        for field in sorted(only_test):
            differences.append(f"   + {field} ({test_fields[field]['type']}) - NEW")

    # Fields only in dev
    only_dev = dev_field_names - test_field_names
    if only_dev:
        for field in sorted(only_dev):
            differences.append(f"   - {field} ({dev_fields[field]['type']}) - REMOVED")

    # Common fields with differences
    common = test_field_names & dev_field_names
    for field in sorted(common):
        test_col = test_fields[field]
        dev_col = dev_fields[field]

        # Check for type differences
        if test_col["type"] != dev_col["type"]:
            differences.append(
                f"   ≠ {field}: type changed from {dev_col['type']} → {test_col['type']}"
            )

        # Check for null differences
        if test_col["null"] != dev_col["null"]:
            differences.append(
                f"   ≠ {field}: null changed from {dev_col['null']} → {test_col['null']}"
            )

        # Check for default differences
        if str(test_col["default"]) != str(dev_col["default"]):
            # Only show if significantly different (not just formatting)
            if test_col["default"] is not None or dev_col["default"] is not None:
                differences.append(
                    f"   ≠ {field}: default changed from {dev_col['default']} → {test_col['default']}"
                )

    if differences or show_all:
        print(f"\n🔍 Table: {table}")
        if differences:
            for diff in differences:
                print(diff)
        else:
            print("   ✅ Identical")


def main() -> None:
    """Compare schemas between databases."""
    print("=" * 70)
    print("Database Schema Comparison")
    print("=" * 70)
    print()

    # Get credentials
    root_password = os.getenv("MYSQL_ROOT_PASSWORD", "root_password")
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "3306")

    # Database names
    test_db = "shuushuu_migration_test"
    dev_db = os.getenv("DB_NAME", "shuushuu")

    print(f"Comparing:")
    print(f"  📦 Test DB:  {test_db} (fresh from SQLModel + migrations)")
    print(f"  🔧 Dev DB:   {dev_db} (current development database)")
    print()

    # Connect to databases
    test_url = f"mysql+pymysql://root:{root_password}@{db_host}:{db_port}/{test_db}"
    dev_url = f"mysql+pymysql://root:{root_password}@{db_host}:{db_port}/{dev_db}"

    print("🔌 Connecting to databases...")
    test_engine = create_engine(test_url, echo=False)
    dev_engine = create_engine(dev_url, echo=False)

    try:
        # Get schemas
        print("📊 Fetching schema information...")
        test_schema = get_table_schema(test_engine, test_db)
        dev_schema = get_table_schema(dev_engine, dev_db)
        print()

        # Compare tables
        print("=" * 70)
        print("TABLE COMPARISON")
        print("=" * 70)
        print()
        compare_tables(test_schema, dev_schema)

        # Compare columns for common tables
        common_tables = set(test_schema.keys()) & set(dev_schema.keys())

        if common_tables:
            print("=" * 70)
            print("COLUMN COMPARISON (Common Tables)")
            print("=" * 70)

            tables_with_diffs = []
            for table in sorted(common_tables):
                test_cols = test_schema[table]["columns"]
                dev_cols = dev_schema[table]["columns"]

                # Check if there are differences
                test_fields = {col["field"] for col in test_cols}
                dev_fields = {col["field"] for col in dev_cols}

                if test_fields != dev_fields:
                    tables_with_diffs.append(table)
                    compare_columns(table, test_cols, dev_cols)
                else:
                    # Check column properties
                    has_diff = False
                    for test_col, dev_col in zip(
                        sorted(test_cols, key=lambda c: c["field"]),
                        sorted(dev_cols, key=lambda c: c["field"]),
                    ):
                        if (
                            test_col["type"] != dev_col["type"]
                            or test_col["null"] != dev_col["null"]
                        ):
                            has_diff = True
                            break

                    if has_diff:
                        tables_with_diffs.append(table)
                        compare_columns(table, test_cols, dev_cols)

            print()
            if not tables_with_diffs:
                print("✅ All common tables have identical columns!")
            else:
                print(f"📊 Found differences in {len(tables_with_diffs)} tables")

        # Summary
        print()
        print("=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print()
        print(f"Test DB tables: {len(test_schema)}")
        print(f"Dev DB tables:  {len(dev_schema)}")
        print(f"Common tables:  {len(set(test_schema.keys()) & set(dev_schema.keys()))}")
        print()

        # Generate SQL for differences
        test_tables = set(test_schema.keys())
        dev_tables = set(dev_schema.keys())
        only_test = test_tables - dev_tables

        if only_test:
            print("💡 New tables from migrations/SQLModel:")
            for table in sorted(only_test):
                print(f"   - {table}")
            print()

        print("✅ Comparison complete!")
        print()
        print("Next steps:")
        print("  1. Review differences above")
        print(f"  2. If satisfied, delete test DB: DROP DATABASE {test_db};")
        print("  3. Or keep it for further inspection")

    finally:
        test_engine.dispose()
        dev_engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Comparison interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
