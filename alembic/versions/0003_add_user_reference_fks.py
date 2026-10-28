"""FK coverage for the historically FK-less user-reference columns.

These tables predate FK discipline (the legacy PHP schema had none):
user_groups/user_perms rows either outlived their user as orphans or — for
user_groups.group_id — were guarded only by an unnamed NO ACTION constraint
that vetoed group deletion outright. Decision (2026-08-29, follow-up to
PR #370): membership and grant links die with the user/group/perm; donations
outlive the donor (SET NULL). user_tag_affinity stays FK-less by design —
its nightly staging-table swap would shed any FK (see
app/services/user_tag_affinity.py).

Data fixes first: prod had 41 donations pointing at deleted users (NULLed —
irreversible, which is also what SET NULL would have done); the orphan
deletes are defensive no-ops (prod counts were 0).

Revision ID: 0003_add_user_reference_fks
Revises: 0002_drop_redundant_fk_twins
Create Date: 2026-08-29
"""

from alembic import op

revision = "0003_add_user_reference_fks"
down_revision = "0002_drop_redundant_fk_twins"
branch_labels = None
depends_on = None

# (name, table, column, referent table, referent column, ondelete)
_FKS = [
    ("fk_user_groups_user_id", "user_groups", "user_id", "users", "user_id", "CASCADE"),
    ("fk_user_groups_group_id", "user_groups", "group_id", "groups", "group_id", "CASCADE"),
    ("fk_user_perms_user_id", "user_perms", "user_id", "users", "user_id", "CASCADE"),
    ("fk_user_perms_perm_id", "user_perms", "perm_id", "perms", "perm_id", "CASCADE"),
    ("fk_donations_user_id", "donations", "user_id", "users", "user_id", "SET NULL"),
]


def upgrade() -> None:
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

    # The baseline's stray on user_groups.group_id: unnamed, NO ACTION, and the
    # only FK these tables had. Replaced by fk_user_groups_group_id below.
    op.drop_constraint("user_groups_group_id_fkey", "user_groups", type_="foreignkey")

    for name, table, column, ref_table, ref_column, ondelete in _FKS:
        op.create_foreign_key(
            name, table, ref_table, [column], [ref_column], ondelete=ondelete, onupdate="CASCADE"
        )


def downgrade() -> None:
    # The NULLed donations.user_id values are not recoverable.
    for name, table, _column, _ref_table, _ref_column, _ondelete in _FKS:
        op.drop_constraint(name, table, type_="foreignkey")
    op.create_foreign_key(
        "user_groups_group_id_fkey", "user_groups", "groups", ["group_id"], ["group_id"]
    )
