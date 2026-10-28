"""add_forum_tables

Revision ID: 0006_add_forum_tables
Revises: 0005_image_metadata_history
Create Date: 2026-07-07 06:39:25.595816

Unread tracking compares last_read_at < last_post_at, so it relies on
sub-second ordering of timestamps (Postgres TIMESTAMP is microsecond).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_add_forum_tables"
down_revision: str | Sequence[str] | None = "0005_image_metadata_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORUM_PERMS = [
    ("forum_access_staff", "Access staff-only forum categories"),
    ("forum_access_tagger", "Access tagger forum categories"),
    ("forum_moderate", "Pin, lock, move, delete, and restore forum threads and posts"),
    ("forum_category_manage", "Create and edit forum categories"),
]

GROUP_GRANTS = {
    "forum_access_staff": ["Admins", "Mods"],
    "forum_access_tagger": ["Admins", "Mods", "Taggers"],
    "forum_moderate": ["Admins", "Mods"],
    "forum_category_manage": ["Admins"],
}


def upgrade() -> None:
    op.create_table(
        "forum_categories",
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("view_perm", sa.String(64), nullable=True),
        sa.Column("thread_create_perm", sa.String(64), nullable=True),
        sa.Column("reply_perm", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("category_id"),
        sa.UniqueConstraint("title"),
    )

    op.create_table(
        "forum_threads",
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "date",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("post_count", sa.Integer(), nullable=False),
        sa.Column("last_post_at", sa.DateTime(), nullable=True),
        sa.Column("last_post_user_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("thread_id"),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["forum_categories.category_id"],
            ondelete="RESTRICT",
            onupdate="CASCADE",
            name="fk_forum_threads_category_id",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_threads_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["last_post_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_forum_threads_last_post_user_id",
        ),
    )
    op.create_index(
        "idx_forum_threads_list", "forum_threads", ["category_id", "pinned", "last_post_at"]
    )
    op.create_index("fk_forum_threads_user_id", "forum_threads", ["user_id"])
    op.create_index("fk_forum_threads_last_post_user_id", "forum_threads", ["last_post_user_id"])
    op.create_index("ix_forum_threads_deleted", "forum_threads", ["deleted"])

    op.create_table(
        "forum_posts",
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("post_text", sa.Text(), nullable=False),
        sa.Column(
            "date",
            sa.DateTime(),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("update_count", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False),
        sa.Column("last_updated", sa.DateTime(), nullable=True),
        sa.Column("last_updated_user_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("post_id"),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["forum_threads.thread_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_posts_thread_id",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_posts_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["last_updated_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_forum_posts_last_updated_user_id",
        ),
    )
    op.create_index("idx_forum_posts_thread_date", "forum_posts", ["thread_id", "date"])
    op.create_index("fk_forum_posts_user_id", "forum_posts", ["user_id"])
    op.create_index("fk_forum_posts_last_updated_user_id", "forum_posts", ["last_updated_user_id"])
    op.create_index("ix_forum_posts_deleted", "forum_posts", ["deleted"])

    op.create_table(
        "forum_thread_reads",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("last_read_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "thread_id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_thread_reads_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["forum_threads.thread_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_thread_reads_thread_id",
        ),
    )
    op.create_index("fk_forum_thread_reads_thread_id", "forum_thread_reads", ["thread_id"])

    # Seed permissions (idempotent vs sync_permissions) and grant to groups.
    for title, desc in FORUM_PERMS:
        op.execute(
            f'INSERT INTO perms (title, "desc") '
            f"SELECT '{title}', '{desc}' "
            f"WHERE NOT EXISTS (SELECT 1 FROM perms WHERE title = '{title}')"
        )

    for title, groups in GROUP_GRANTS.items():
        group_list = ", ".join(f"'{g}'" for g in groups)
        op.execute(f"""
            INSERT INTO group_perms (group_id, perm_id, permvalue)
            SELECT g.group_id, (SELECT MIN(perm_id) FROM perms WHERE title = '{title}'), 1
            FROM groups g
            WHERE g.title IN ({group_list})
            ON CONFLICT (group_id, perm_id) DO NOTHING
        """)


def downgrade() -> None:
    for title, _ in FORUM_PERMS:
        op.execute(f"""
            DELETE FROM group_perms
            WHERE perm_id IN (SELECT perm_id FROM perms WHERE title = '{title}')
        """)
        op.execute(f"""
            DELETE FROM user_perms
            WHERE perm_id IN (SELECT perm_id FROM perms WHERE title = '{title}')
        """)
        op.execute(f"DELETE FROM perms WHERE title = '{title}'")

    op.drop_table("forum_thread_reads")
    op.drop_table("forum_posts")
    op.drop_table("forum_threads")
    op.drop_table("forum_categories")
