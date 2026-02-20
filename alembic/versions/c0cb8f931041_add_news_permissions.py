"""add_news_permissions

Revision ID: c0cb8f931041
Revises: e8e9d4e6b553
Create Date: 2026-02-20 11:51:07.506230

"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c0cb8f931041'
down_revision: str | Sequence[str] | None = 'e8e9d4e6b553'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEWS_PERMS = [
    ('news_create', 'Create news posts'),
    ('news_edit', 'Edit news posts'),
    ('news_delete', 'Delete news posts'),
]


def upgrade() -> None:
    """Add news permissions and grant them to Admins and Mods groups."""
    # Insert permissions only if they don't already exist (sync_permissions may
    # have created them). perms.title has no unique index so INSERT IGNORE
    # would silently create duplicates.
    for title, desc in NEWS_PERMS:
        op.execute(
            f"INSERT INTO perms (title, `desc`) "
            f"SELECT '{title}', '{desc}' FROM DUAL "
            f"WHERE NOT EXISTS (SELECT 1 FROM perms WHERE title = '{title}')"
        )

    # Grant to Admins and Mods groups.  Use MIN(p.perm_id) to avoid duplicates
    # if multiple perm rows somehow exist for the same title.
    for title, _ in NEWS_PERMS:
        op.execute(f"""
            INSERT IGNORE INTO group_perms (group_id, perm_id, permvalue)
            SELECT g.group_id, (SELECT MIN(perm_id) FROM perms WHERE title = '{title}'), 1
            FROM `groups` g
            WHERE g.title IN ('Admins', 'Mods')
        """)


def downgrade() -> None:
    """Remove news permissions and group grants."""
    for title, _ in NEWS_PERMS:
        op.execute(f"""
            DELETE gp FROM group_perms gp
            JOIN perms p ON gp.perm_id = p.perm_id
            WHERE p.title = '{title}'
        """)
        op.execute(f"""
            DELETE up FROM user_perms up
            JOIN perms p ON up.perm_id = p.perm_id
            WHERE p.title = '{title}'
        """)
        op.execute(f"DELETE FROM perms WHERE title = '{title}'")
