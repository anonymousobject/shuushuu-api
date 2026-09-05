"""add user reference fk constraints

MariaDB half of the pair (ADR-0010) for alembic_pg
0003_add_user_reference_fks: FK coverage for the historically FK-less
user-reference columns. Membership and grant links die with the
user/group/perm; donations outlive the donor (SET NULL).
user_tag_affinity stays FK-less by design — its nightly staging-table swap
(CREATE TABLE ... LIKE) would shed any FK.

Unlike the Postgres half there is nothing to drop: these tables never had
any FK on this chain. The data fixes mirror the PG half and are no-ops on
chain-built (empty) databases.

InnoDB requires exact type matches across an FK, and the legacy parents
(users.user_id, groups.group_id, perms.perm_id) are INT UNSIGNED while the
junction columns were signed int(11) — so those move to INT UNSIGNED first
(values are all positive; donations.user_id is already unsigned). The
models keep plain (signed) ints: a create_all schema is signed on BOTH
sides of these FKs and therefore self-consistent; the full signed/unsigned
audit is tracked in
docs/plans/2026-Q2/2026-06-10-schema-sync-signed-unsigned-drift.md.

Revision ID: 28e56662b975
Revises: 10eef13f525a
Create Date: 2026-08-29 17:06:59.135451

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "28e56662b975"
down_revision: str | Sequence[str] | None = "10eef13f525a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (name, table, column, referent table, referent column, ondelete)
_FKS = [
    ("fk_user_groups_user_id", "user_groups", "user_id", "users", "user_id", "CASCADE"),
    ("fk_user_groups_group_id", "user_groups", "group_id", "groups", "group_id", "CASCADE"),
    ("fk_user_perms_user_id", "user_perms", "user_id", "users", "user_id", "CASCADE"),
    ("fk_user_perms_perm_id", "user_perms", "perm_id", "perms", "perm_id", "CASCADE"),
    ("fk_donations_user_id", "donations", "user_id", "users", "user_id", "SET NULL"),
]


def upgrade() -> None:
    """Upgrade schema."""
    # Detach donations from deleted users; the rows themselves are kept.
    op.execute(
        "UPDATE donations SET user_id = NULL WHERE user_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM users WHERE users.user_id = donations.user_id)"
    )
    # Defensive orphan sweep before the CASCADE FKs can be validated.
    op.execute(
        "DELETE FROM user_groups WHERE NOT EXISTS "
        "(SELECT 1 FROM users WHERE users.user_id = user_groups.user_id) "
        "OR NOT EXISTS (SELECT 1 FROM groups WHERE groups.group_id = user_groups.group_id)"
    )
    op.execute(
        "DELETE FROM user_perms WHERE NOT EXISTS "
        "(SELECT 1 FROM users WHERE users.user_id = user_perms.user_id) "
        "OR NOT EXISTS (SELECT 1 FROM perms WHERE perms.perm_id = user_perms.perm_id)"
    )

    # Match the unsigned legacy parents before InnoDB will accept the FKs.
    op.execute(
        "ALTER TABLE user_groups "
        "MODIFY user_id INT UNSIGNED NOT NULL, MODIFY group_id INT UNSIGNED NOT NULL"
    )
    op.execute(
        "ALTER TABLE user_perms "
        "MODIFY user_id INT UNSIGNED NOT NULL, MODIFY perm_id INT UNSIGNED NOT NULL"
    )

    for name, table, column, ref_table, ref_column, ondelete in _FKS:
        op.create_foreign_key(
            name, table, ref_table, [column], [ref_column], ondelete=ondelete, onupdate="CASCADE"
        )


def downgrade() -> None:
    """Downgrade schema."""
    # The NULLed donations.user_id values are not recoverable.
    for name, table, _column, _ref_table, _ref_column, _ondelete in _FKS:
        op.drop_constraint(name, table, type_="foreignkey")
    op.execute("ALTER TABLE user_groups MODIFY user_id INT NOT NULL, MODIFY group_id INT NOT NULL")
    op.execute("ALTER TABLE user_perms MODIFY user_id INT NOT NULL, MODIFY perm_id INT NOT NULL")
