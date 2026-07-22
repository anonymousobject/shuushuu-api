"""add_forum_tables

Revision ID: f565e631d8c2
Revises: 12bf25199415
Create Date: 2026-07-07 06:39:25.595816

Forum timestamps use DATETIME(6) (microsecond precision): unread tracking
compares last_read_at < last_post_at, and second-precision DATETIME cannot
order events that land within the same wall-clock second.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = 'f565e631d8c2'
# Re-pointed from 25cad69549de onto main's head (12bf25199415, user_tag_affinity)
# when the forum stack was rebased onto main, so the migration graph stays linear
# and single-headed (the forum and taste-profile tables are independent).
down_revision: str | Sequence[str] | None = '7d98087eabcb'
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
        sa.Column(
            "category_id", mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False
        ),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("view_perm", sa.String(64), nullable=True),
        sa.Column("thread_create_perm", sa.String(64), nullable=True),
        sa.Column("reply_perm", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("category_id"),
    )
    op.create_index("uq_forum_categories_title", "forum_categories", ["title"], unique=True)

    op.create_table(
        "forum_threads",
        sa.Column("thread_id", mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("category_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column(
            "date",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("current_timestamp(6)"),
            nullable=False,
        ),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("post_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_post_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("last_post_user_id", mysql.INTEGER(unsigned=True), nullable=True),
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
    op.create_index(
        "fk_forum_threads_last_post_user_id", "forum_threads", ["last_post_user_id"]
    )
    op.create_index("ix_forum_threads_deleted", "forum_threads", ["deleted"])

    op.create_table(
        "forum_posts",
        sa.Column("post_id", mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("thread_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("post_text", sa.Text(), nullable=False),
        sa.Column(
            "date",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("current_timestamp(6)"),
            nullable=False,
        ),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("update_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("last_updated", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("last_updated_user_id", mysql.INTEGER(unsigned=True), nullable=True),
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
    op.create_index(
        "fk_forum_posts_last_updated_user_id", "forum_posts", ["last_updated_user_id"]
    )
    op.create_index("ix_forum_posts_deleted", "forum_posts", ["deleted"])

    op.create_table(
        "forum_thread_reads",
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("thread_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("last_read_at", mysql.DATETIME(fsp=6), nullable=False),
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
    # Same pattern as c0cb8f931041_add_news_permissions.py.
    for title, desc in FORUM_PERMS:
        op.execute(
            f"INSERT INTO perms (title, `desc`) "
            f"SELECT '{title}', '{desc}' FROM DUAL "
            f"WHERE NOT EXISTS (SELECT 1 FROM perms WHERE title = '{title}')"
        )

    for title, groups in GROUP_GRANTS.items():
        group_list = ", ".join(f"'{g}'" for g in groups)
        op.execute(f"""
            INSERT IGNORE INTO group_perms (group_id, perm_id, permvalue)
            SELECT g.group_id, (SELECT MIN(perm_id) FROM perms WHERE title = '{title}'), 1
            FROM `groups` g
            WHERE g.title IN ({group_list})
        """)


def downgrade() -> None:
    for title, _ in FORUM_PERMS:
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

    op.drop_table("forum_thread_reads")
    op.drop_table("forum_posts")
    op.drop_table("forum_threads")
    op.drop_table("forum_categories")
