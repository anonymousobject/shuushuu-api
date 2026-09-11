"""Verify FK constraints in the migrated schema: one constraint per column set, and the agreed delete rules on the user-reference columns."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.integration
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
            "alembic migration.\n\n" + "\n".join(duplicates)
        )


# The FK coverage decided 2026-08-29 (users cleanup after PR #370): membership
# and grant links die with the user/group/perm; donations outlive the donor.
# user_tag_affinity stays FK-less BY DESIGN (see its model docstring): the
# nightly full rebuild keeps it consistent without per-row FK checks.
_EXPECTED_USER_REFERENCE_FKS = [
    ("user_groups", "user_id", "users", "CASCADE"),
    ("user_groups", "group_id", "groups", "CASCADE"),
    ("user_perms", "user_id", "users", "CASCADE"),
    ("user_perms", "perm_id", "perms", "CASCADE"),
    ("donations", "user_id", "users", "SET NULL"),
]


@pytest.mark.integration
async def test_user_reference_fks_have_delete_rules(db_session: AsyncSession) -> None:
    """
    The historically FK-less user-reference columns must be constrained.

    These tables predate FK discipline (the legacy PHP schema had none), so
    user deletion either left orphans (user_perms, donations) or was vetoed
    by an unnamed NO ACTION constraint (user_groups.group_id). Each expected
    FK must exist, follow the fk_<table>_<column> naming convention, and
    carry the agreed ON DELETE rule.
    """
    result = await db_session.execute(
        text(
            """
            SELECT conrelid::regclass::text,
                   (SELECT att.attname FROM pg_attribute att
                     WHERE att.attrelid = conrelid AND att.attnum = conkey[1]),
                   confrelid::regclass::text,
                   conname,
                   CASE confdeltype WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL'
                        WHEN 'r' THEN 'RESTRICT' WHEN 'd' THEN 'SET DEFAULT'
                        ELSE 'NO ACTION' END
            FROM pg_constraint
            WHERE contype = 'f' AND connamespace = 'public'::regnamespace
              AND cardinality(conkey) = 1
            """
        )
    )
    actual = {(t, col): (ref, name, rule) for t, col, ref, name, rule in result}

    problems: list[str] = []
    for table, column, ref_table, on_delete in _EXPECTED_USER_REFERENCE_FKS:
        found = actual.get((table, column))
        if found is None:
            problems.append(f"{table}.{column}: no FK (expected -> {ref_table} {on_delete})")
            continue
        ref, name, rule = found
        if ref != ref_table or rule != on_delete:
            problems.append(
                f"{table}.{column}: {name} -> {ref} ON DELETE {rule} "
                f"(expected -> {ref_table} {on_delete})"
            )
        elif name != f"fk_{table}_{column}":
            problems.append(f"{table}.{column}: named {name}, expected fk_{table}_{column}")

    if problems:
        pytest.fail("User-reference FK constraints missing or wrong:\n" + "\n".join(problems))
