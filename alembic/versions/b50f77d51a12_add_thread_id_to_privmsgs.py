"""add thread_id to privmsgs

Revision ID: b50f77d51a12
Revises: 92f9d7890c30
Create Date: 2026-03-01 15:19:53.515328

"""
import uuid
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'b50f77d51a12'
down_revision: str | Sequence[str] | None = '92f9d7890c30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add thread_id column and backport existing messages into threads."""
    # 1. Add nullable thread_id column
    op.add_column("privmsgs", sa.Column("thread_id", sa.String(36), nullable=True))

    # 2. Backport existing messages into threads
    conn = op.get_bind()

    rows = conn.execute(text("""
        SELECT privmsg_id, subject, from_user_id, to_user_id
        FROM privmsgs
        ORDER BY date ASC
    """)).fetchall()

    # Group by (normalized_subject, sorted user pair)
    threads: dict[tuple, str] = {}
    for privmsg_id, subject, from_uid, to_uid in rows:
        normalized = subject or ""
        while normalized.startswith("Re: ") or normalized.startswith("Re:"):
            if normalized.startswith("Re: "):
                normalized = normalized[4:]
            elif normalized.startswith("Re:"):
                normalized = normalized[3:]
        normalized = normalized.strip().lower()

        user_pair = tuple(sorted([from_uid, to_uid]))
        key = (normalized, user_pair)

        if key not in threads:
            threads[key] = str(uuid.uuid4())

        conn.execute(
            text("UPDATE privmsgs SET thread_id = :tid WHERE privmsg_id = :pid"),
            {"tid": threads[key], "pid": privmsg_id},
        )

    # 3. Assign unique thread_id to any remaining NULL rows
    remaining = conn.execute(text("SELECT privmsg_id FROM privmsgs WHERE thread_id IS NULL")).fetchall()
    for (privmsg_id,) in remaining:
        conn.execute(
            text("UPDATE privmsgs SET thread_id = :tid WHERE privmsg_id = :pid"),
            {"tid": str(uuid.uuid4()), "pid": privmsg_id},
        )

    # 4. Make column NOT NULL and add index
    op.alter_column("privmsgs", "thread_id", nullable=False, existing_type=sa.String(36))
    op.create_index("ix_privmsgs_thread_id", "privmsgs", ["thread_id"])


def downgrade() -> None:
    """Remove thread_id column."""
    op.drop_index("ix_privmsgs_thread_id", table_name="privmsgs")
    op.drop_column("privmsgs", "thread_id")
