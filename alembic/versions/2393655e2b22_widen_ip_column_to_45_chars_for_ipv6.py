"""widen ip column to 45 chars for ipv6

Revision ID: 2393655e2b22
Revises: c25a53d7e1e6
Create Date: 2026-05-05 20:35:04.887562

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2393655e2b22'
down_revision: str | Sequence[str] | None = 'c25a53d7e1e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, nullable) pairs. `posts` is the on-disk name for the Comments model
# (PHP heritage). The legacy `bans` table was dropped by migration
# d1e2f3a4b5c6 (user suspensions system) so it's intentionally not listed.
IP_TABLES: tuple[tuple[str, bool], ...] = (
    ("images", False),
    ("posts", False),
)


def upgrade() -> None:
    """Widen ip columns from VARCHAR(15) (IPv4-only) to VARCHAR(45) (IPv6 + zone-id).

    The legacy schema sized these columns for IPv4 dotted-quad strings. With
    Cloudflare in front of the API, X-Forwarded-For carries the real client
    address, which is IPv6 for a sizable share of users -- those uploads/
    comments fail Pydantic validation before the DB write.
    """
    # has_table is defensive against an out-of-sync environment but in practice
    # both tables always exist on every shuushuu-api deployment.
    inspector = sa.inspect(op.get_bind())
    for table, nullable in IP_TABLES:
        if not inspector.has_table(table):
            continue
        op.alter_column(
            table,
            "ip",
            existing_type=sa.String(length=15),
            type_=sa.String(length=45),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    """Revert ip columns to VARCHAR(15).

    MariaDB/MySQL silently truncates oversized values on `MODIFY COLUMN`
    unless `STRICT_TRANS_TABLES` / `STRICT_ALL_TABLES` is set in `sql_mode`.
    Count any IPv6 rows first and refuse to proceed if found; the operator
    must explicitly clear or rewrite them before re-running the downgrade.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, nullable in IP_TABLES:
        if not inspector.has_table(table):
            continue
        oversized = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM `{table}` WHERE LENGTH(ip) > 15")
        ).scalar()
        if oversized:
            raise RuntimeError(
                f"Cannot downgrade: {oversized} row(s) in `{table}` have ip "
                f"values longer than 15 characters (IPv6). Truncate or rewrite "
                f"those rows before re-running this downgrade."
            )
        op.alter_column(
            table,
            "ip",
            existing_type=sa.String(length=45),
            type_=sa.String(length=15),
            existing_nullable=nullable,
        )
