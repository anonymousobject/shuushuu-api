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

# Import all models to populate SQLModel.metadata
# This single import loads all models via app/models/__init__.py
import app.models  # noqa: F401

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
            "ondelete": (fk.get("options", {}).get("ondelete") or "").upper() or None,
            "onupdate": (fk.get("options", {}).get("onupdate") or "").upper() or None,
        }
    return fks


def normalize_fk_action(action: str | None) -> str | None:
    """Normalize FK action for comparison (handle NO ACTION vs None)."""
    if action is None or action == "NO ACTION":
        return None
    return action.upper()


@pytest.mark.integration
@pytest.mark.schema_sync
class TestSchemaSync:
    """Tests to verify models match migrations.

    Run with: pytest tests/integration/test_schema_sync.py --schema-sync -v
    """

    def test_foreign_key_cascade_behavior_matches(self):
        """
        Verify that foreign key CASCADE behavior in models matches migrations.

        This test:
        1. Creates a database using SQLModel.metadata.create_all()
        2. Creates a database using Alembic migrations
        3. Compares foreign key constraints between them

        This catches issues where Field(foreign_key=...) doesn't include
        CASCADE behavior that ForeignKeyConstraint in migrations specifies.
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
                conn.execute(text(f"DROP DATABASE IF EXISTS {db_models}"))
                conn.execute(text(f"DROP DATABASE IF EXISTS {db_migrations}"))
                conn.execute(
                    text(f"CREATE DATABASE {db_models} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                )
                conn.execute(
                    text(f"CREATE DATABASE {db_migrations} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                )
                conn.execute(text(f"GRANT ALL PRIVILEGES ON {db_models}.* TO '{db_user}'@'%'"))
                conn.execute(text(f"GRANT ALL PRIVILEGES ON {db_migrations}.* TO '{db_user}'@'%'"))
                conn.execute(text("FLUSH PRIVILEGES"))

            # Create schema from models
            models_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_models}"
            models_engine = create_engine(models_url)
            SQLModel.metadata.create_all(models_engine)

            # Create schema from migrations
            # Run alembic via subprocess so it picks up the env var fresh
            # (app.config.settings caches DATABASE_URL_SYNC at import time)
            migrations_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_migrations}"

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

            # Compare foreign keys
            models_inspector = inspect(models_engine)
            migrations_inspector = inspect(migrations_engine)

            # Tables to check (the ones we fixed)
            tables_to_check = ["image_reports", "image_reviews", "review_votes"]

            differences = []
            for table in tables_to_check:
                models_fks = get_foreign_keys(models_inspector, table)
                migrations_fks = get_foreign_keys(migrations_inspector, table)

                for col_key, model_fk in models_fks.items():
                    if col_key not in migrations_fks:
                        differences.append(f"{table}: FK on {col_key} exists in models but not migrations")
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

        finally:
            # Cleanup
            with admin_engine.connect() as conn:
                conn.execute(text(f"DROP DATABASE IF EXISTS {db_models}"))
                conn.execute(text(f"DROP DATABASE IF EXISTS {db_migrations}"))
            admin_engine.dispose()
