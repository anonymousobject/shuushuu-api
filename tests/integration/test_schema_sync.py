"""
Test to verify SQLModel models are in sync with Alembic migrations.

This test creates two databases:
1. One using SQLModel.metadata.create_all() (from models)
2. One using Alembic migrations

Then compares the schemas to detect drift between models and migrations.

Run with:
    pytest tests/integration/test_schema_sync.py --schema-sync -v

Uses default credentials matching CI, or override via environment variables.
"""

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

# Import defaults from conftest (single source of truth)
from tests.conftest import (
    DEFAULT_ROOT_PASSWORD,
    DEFAULT_TEST_DB_HOST,
    DEFAULT_TEST_DB_PASSWORD,
    DEFAULT_TEST_DB_PORT,
    DEFAULT_TEST_DB_USER,
)


def get_foreign_keys(inspector, table_name: str) -> dict[str, dict]:
    """Get foreign keys for a table, normalized for comparison."""
    fks = {}
    for fk in inspector.get_foreign_keys(table_name):
        # Key by constrained columns for comparison
        key = tuple(sorted(fk["constrained_columns"]))
        fks[key] = {
            "referred_table": fk["referred_table"],
            "referred_columns": tuple(sorted(fk["referred_columns"])),
            "ondelete": fk.get("options", {}).get("ondelete"),
            "onupdate": fk.get("options", {}).get("onupdate"),
        }
    return fks


def get_column_types(inspector, table_name: str) -> dict[str, dict]:
    """Get column types for a table, normalized for comparison.

    Compares the reflected type class (INTEGER vs TINYINT, etc.), signedness,
    and length. Display width is ignored (cosmetic in MariaDB); nullability and
    defaults are out of scope here (this targets type drift specifically).
    """
    columns = {}
    for column in inspector.get_columns(table_name):
        column_type = column["type"]
        columns[column["name"]] = {
            "type": type(column_type).__name__,
            "unsigned": getattr(column_type, "unsigned", False),
            "length": getattr(column_type, "length", None),
        }
    return columns


def normalize_fk_action(action: str | None) -> str | None:
    """Normalize FK action for comparison (handle NO ACTION vs None)."""
    if action is None or action == "NO ACTION":
        return None
    return action.upper()


@pytest.fixture(scope="class")
def schema_inspectors():
    """Build one database from models (create_all) and one from migrations.

    Yields (models_inspector, migrations_inspector) for schema comparison.
    """
    # Get credentials with defaults matching CI
    db_user = os.getenv("TEST_DB_USER", DEFAULT_TEST_DB_USER)
    db_password = os.getenv("TEST_DB_PASSWORD", DEFAULT_TEST_DB_PASSWORD)
    db_host = os.getenv("TEST_DB_HOST", DEFAULT_TEST_DB_HOST)
    db_port = os.getenv("TEST_DB_PORT", DEFAULT_TEST_DB_PORT)
    root_password = os.getenv("MYSQL_ROOT_PASSWORD", DEFAULT_ROOT_PASSWORD)

    admin_url = f"mysql+pymysql://root:{root_password}@{db_host}:{db_port}/mysql"

    # Database names for comparison
    db_models = "shuushuu_schema_models"
    db_migrations = "shuushuu_schema_migrations"

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    try:
        # Create fresh databases
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{db_models}`"))
            conn.execute(text(f"DROP DATABASE IF EXISTS `{db_migrations}`"))
            conn.execute(
                text(
                    f"CREATE DATABASE `{db_models}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            conn.execute(
                text(
                    f"CREATE DATABASE `{db_migrations}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            conn.execute(
                text(f"GRANT ALL PRIVILEGES ON `{db_models}`.* TO :db_user@'%'"),
                {"db_user": db_user},
            )
            conn.execute(
                text(f"GRANT ALL PRIVILEGES ON `{db_migrations}`.* TO :db_user@'%'"),
                {"db_user": db_user},
            )
            conn.execute(text("FLUSH PRIVILEGES"))

        # Create schema from models
        models_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_models}"
        models_engine = create_engine(models_url)
        SQLModel.metadata.create_all(models_engine)

        # Create schema from migrations
        # Run alembic via subprocess so it picks up the env var fresh
        # (app.config.settings caches DATABASE_URL_SYNC at import time)
        migrations_url = (
            f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_migrations}"
        )

        env = os.environ.copy()
        env["DATABASE_URL_SYNC"] = migrations_url
        result = subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        if result.returncode != 0:
            pytest.fail(f"Alembic migrations failed:\n{result.stderr}\n{result.stdout}")

        migrations_engine = create_engine(migrations_url)

        yield inspect(models_engine), inspect(migrations_engine)

    finally:
        # Cleanup
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{db_models}`"))
            conn.execute(text(f"DROP DATABASE IF EXISTS `{db_migrations}`"))
        admin_engine.dispose()


@pytest.mark.integration
@pytest.mark.schema_sync
# These tests rebuild fixed-name databases (shuushuu_schema_models/_migrations),
# so under xdist they must all run on the same worker (--dist loadgroup).
@pytest.mark.xdist_group("schema_sync")
class TestSchemaSync:
    """Tests to verify models match migrations.

    Run with: pytest tests/integration/test_schema_sync.py --schema-sync -v
    """

    def test_foreign_key_cascade_behavior_matches(self, schema_inspectors):
        """
        Verify that foreign key CASCADE behavior in models matches migrations.

        This catches issues where Field(foreign_key=...) doesn't include
        CASCADE behavior that ForeignKeyConstraint in migrations specifies.
        """
        models_inspector, migrations_inspector = schema_inspectors

        # Tables to check (the ones we fixed)
        tables_to_check = ["image_reports", "image_reviews", "review_votes"]

        differences = []
        for table in tables_to_check:
            models_fks = get_foreign_keys(models_inspector, table)
            migrations_fks = get_foreign_keys(migrations_inspector, table)

            for col_key, model_fk in models_fks.items():
                if col_key not in migrations_fks:
                    differences.append(
                        f"{table}: FK on {col_key} exists in models but not migrations"
                    )
                    continue

                migration_fk = migrations_fks[col_key]

                # Compare CASCADE behavior
                model_ondelete = normalize_fk_action(model_fk["ondelete"])
                migration_ondelete = normalize_fk_action(migration_fk["ondelete"])

                if model_ondelete != migration_ondelete:
                    differences.append(
                        f"{table}.{col_key}: ondelete mismatch - "
                        f"models={model_ondelete}, migrations={migration_ondelete}"
                    )

                model_onupdate = normalize_fk_action(model_fk["onupdate"])
                migration_onupdate = normalize_fk_action(migration_fk["onupdate"])

                if model_onupdate != migration_onupdate:
                    differences.append(
                        f"{table}.{col_key}: onupdate mismatch - "
                        f"models={model_onupdate}, migrations={migration_onupdate}"
                    )

        if differences:
            pytest.fail(
                "Schema differences between models and migrations:\n" + "\n".join(differences)
            )

    def test_column_types_match(self, schema_inspectors):
        """
        Verify that column types (including signedness) in models match migrations.

        This catches drift like the report_id/review_id family: the DB has them
        as INT UNSIGNED (legacy schema), but models declared them signed. The FK
        CASCADE test never compared column types, so this went unnoticed.

        Scoped to the report_id/review_id column family. A whole-table diff also
        flags image_id/user_id/tag_id signedness and TINYINT/MEDIUMTEXT drift,
        but fixing those cascades into the images/users/tags parent PKs — that
        full-schema type audit is tracked separately
        (docs/plans/2026-06-10-schema-sync-signed-unsigned-drift.md).
        """
        models_inspector, migrations_inspector = schema_inspectors

        # The report_id/review_id family: the PKs and every FK to them
        columns_to_check = [
            ("image_reports", "report_id"),
            ("image_reviews", "review_id"),
            ("image_reviews", "source_report_id"),
            ("image_status_history", "report_id"),
            ("image_status_history", "review_id"),
            ("image_report_tag_suggestions", "report_id"),
            ("admin_actions", "report_id"),
            ("admin_actions", "review_id"),
            ("review_votes", "review_id"),
        ]

        differences = []
        for table in sorted({table for table, _ in columns_to_check}):
            models_columns = get_column_types(models_inspector, table)
            migrations_columns = get_column_types(migrations_inspector, table)

            for check_table, name in columns_to_check:
                if check_table != table:
                    continue

                model_col = models_columns.get(name)
                migration_col = migrations_columns.get(name)
                if model_col is None or migration_col is None:
                    differences.append(
                        f"{table}.{name}: missing - models={model_col}, migrations={migration_col}"
                    )
                elif model_col != migration_col:
                    differences.append(
                        f"{table}.{name}: type mismatch - "
                        f"models={model_col}, migrations={migration_col}"
                    )

        if differences:
            pytest.fail(
                "Column type differences between models and migrations:\n" + "\n".join(differences)
            )
