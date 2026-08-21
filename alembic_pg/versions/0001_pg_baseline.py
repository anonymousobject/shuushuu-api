"""Postgres baseline: the full application schema as of the transition.

Squashes nothing less than everything — 45 tables (citext natural keys,
deduped index names), length CHECK constraints, and the counter triggers —
from the frozen companion file 0001_pg_baseline.sql (generated once by
scripts/gen_pg_baseline.py; see the design doc and ADR-0008/0009). Statements
are separated by `-- ==stmt==` marker lines because asyncpg cannot execute
multi-command prepared statements.

Revision ID: 0001_pg_baseline
Revises:
Create Date: 2026-08-21
"""

from pathlib import Path

from alembic import op

revision = "0001_pg_baseline"
down_revision = None
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).with_name("0001_pg_baseline.sql")
_MARKER = "-- ==stmt=="


def upgrade() -> None:
    for statement in _SQL_FILE.read_text().split(f"{_MARKER}\n"):
        statement = statement.strip()
        if statement:
            op.execute(statement)


def downgrade() -> None:
    # The baseline IS the schema; downgrading it means an empty database.
    op.execute("DROP SCHEMA public CASCADE")
    op.execute("CREATE SCHEMA public")
