"""drop user_sessions table

The user_sessions table was used by the legacy PHP system for server-side
session management. The FastAPI backend uses stateless JWT authentication
instead, so this table is no longer needed.

Revision ID: 9efa03a1b318
Revises: 7863e125095a
Create Date: 2026-01-11 22:02:46.341769

"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9efa03a1b318'
down_revision: str | Sequence[str] | None = '7863e125095a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the user_sessions table."""
    op.drop_table('user_sessions')


def downgrade() -> None:
    """Recreate the user_sessions table."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS `user_sessions` (
          `session_id` varchar(50) NOT NULL DEFAULT '',
          `user_id` int(10) unsigned NOT NULL,
          `last_used` datetime NOT NULL DEFAULT current_timestamp(),
          `last_view_date` datetime DEFAULT current_timestamp(),
          `ip` varchar(16) NOT NULL DEFAULT '',
          `lastpage` varchar(200) DEFAULT NULL,
          `last_search` datetime DEFAULT current_timestamp(),
          PRIMARY KEY (`session_id`),
          KEY `ip` (`ip`),
          KEY `fk_user_sessions_user_id` (`user_id`),
          CONSTRAINT `fk_user_sessions_user_id` FOREIGN KEY (`user_id`)
            REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)
