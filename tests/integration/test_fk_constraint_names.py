"""
Verify alembic migrations produce explicitly-named FK constraints.

Background: an unnamed ``ForeignKeyConstraint(...)`` in a migration leaves
naming up to MariaDB, which has been observed to assign numeric names like
``1``, ``2``, ``3``. FK constraint names are unique per schema in InnoDB, so
those numeric names collide between tables on dump restore (errno 121). Every
``ForeignKeyConstraint`` in a migration must therefore pass ``name=`` so
resulting names are predictable and unique.

This test runs against the autouse-rebuilt session test DB, so it reflects the
actual names alembic produced on a fresh schema.
"""

import pytest
from sqlalchemy import create_engine, inspect

from tests.conftest import TEST_DATABASE_URL_SYNC

TABLES_REQUIRING_NAMED_FKS = [
    "comment_reports",
    "user_banner_pins",
    "user_banner_preferences",
]


@pytest.mark.integration
def test_affected_tables_have_explicitly_named_fks():
    engine = create_engine(TEST_DATABASE_URL_SYNC)
    try:
        inspector = inspect(engine)
        failures: list[str] = []

        for table in TABLES_REQUIRING_NAMED_FKS:
            for fk in inspector.get_foreign_keys(table):
                name = fk.get("name") or ""
                cols = ",".join(fk.get("constrained_columns") or [])
                if not name.startswith("fk_"):
                    failures.append(
                        f"{table}({cols}): expected fk_-prefixed name, got {name!r}"
                    )
    finally:
        engine.dispose()

    if failures:
        pytest.fail(
            "FK constraints without explicit fk_-prefixed names found. "
            "Add `name=` to the ForeignKeyConstraint(...) in the migration "
            "that created the table:\n\n" + "\n".join(failures)
        )
