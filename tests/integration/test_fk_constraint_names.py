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
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TEST_DATABASE_URL_SYNC


@pytest.mark.integration
@pytest.mark.mariadb_only  # guards hand-written migration FK names against the
# InnoDB per-schema namespace; on Postgres, create_all makes name parity trivial
def test_all_fks_use_fk_prefix_convention():
    """
    Every FK in the migrated schema must have a name starting with ``fk_``.

    This is a regression guard for the numeric-naming bug fixed in PR #209
    and a forward-looking guard against any new migration that forgets
    ``name=`` on a ``ForeignKeyConstraint``. The whole schema is checked
    rather than a hardcoded subset so a new offender in any table fails the
    suite immediately.
    """
    engine = create_engine(TEST_DATABASE_URL_SYNC)
    try:
        inspector = inspect(engine)
        failures: list[str] = []

        for table in inspector.get_table_names():
            for fk in inspector.get_foreign_keys(table):
                name = fk.get("name") or ""
                cols = ",".join(fk.get("constrained_columns") or [])
                if not name.startswith("fk_"):
                    failures.append(f"{table}({cols}): expected fk_-prefixed name, got {name!r}")
    finally:
        engine.dispose()

    if failures:
        pytest.fail(
            "FK constraints without explicit fk_-prefixed names found. "
            "Add `name=` to the ForeignKeyConstraint(...) in the migration "
            "that created the table. Convention: fk_<table>_<column>.\n\n" + "\n".join(failures)
        )


@pytest.mark.integration
@pytest.mark.postgres_only  # guards the PG chain against doubled FK enforcement;
# on MariaDB the chain never rendered model metadata, so the hazard doesn't exist
async def test_one_fk_constraint_per_column_set(db_session: AsyncSession) -> None:
    """
    Each FK column set must be enforced by exactly ONE constraint.

    Regression guard for the doubled model declarations
    (``Field(foreign_key=...)`` alongside a named ``ForeignKeyConstraint``)
    that the frozen PG baseline rendered as 34 duplicate pairs: a named
    ``fk_*`` carrying the intended ON DELETE plus an auto-named ``*_fkey``
    defaulting to NO ACTION — and NO ACTION vetoes the cascade, so every
    ON DELETE rule involved was silently dead. Asserts on ``pg_constraint``
    directly: a models-vs-chain diff (test_pg_schema_sync) compares two
    renderings of the same metadata and is structurally blind to this class.
    """
    result = await db_session.execute(
        text(
            """
            SELECT conrelid::regclass::text AS child_table,
                   (SELECT string_agg(att.attname, ',' ORDER BY cols.ord)
                      FROM unnest(pg_constraint.conkey)
                           WITH ORDINALITY AS cols(attnum, ord)
                      JOIN pg_attribute att
                        ON att.attrelid = pg_constraint.conrelid
                       AND att.attnum = cols.attnum) AS child_columns,
                   string_agg(conname, ', ' ORDER BY conname) AS constraint_names
            FROM pg_constraint
            WHERE contype = 'f' AND connamespace = 'public'::regnamespace
            GROUP BY conrelid, conkey, confrelid, confkey
            HAVING count(*) > 1
            ORDER BY 1, 2
            """
        )
    )
    duplicates = [f"{table}({columns}): {names}" for table, columns, names in result]

    if duplicates:
        pytest.fail(
            "Duplicate FK constraints found (same child columns, same parent). "
            "The declaration is doubled: drop the `foreign_key=` from the "
            "Field() — the named ForeignKeyConstraint in __table_args__ is the "
            "one carrying ON DELETE — and drop the redundant constraint in an "
            "alembic_pg migration.\n\n" + "\n".join(duplicates)
        )
