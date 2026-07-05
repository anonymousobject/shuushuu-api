"""purge pending character ml suggestions

Revision ID: b61cc2bcee90
Revises: 18dcd44b530d
Create Date: 2026-07-05 10:18:14.402851

"""
from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b61cc2bcee90"
down_revision: str | Sequence[str] | None = "18dcd44b530d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One-off cleanup: mods flagged mapped character suggestions as unreliable
    # (cross-franchise collisions), so pending ones are noise. Approved/rejected
    # rows are real review history and stay. MariaDB multi-table DELETE.
    op.execute(
        "DELETE s FROM ml_tag_suggestions s "
        "JOIN tags t ON t.tag_id = s.tag_id "
        "WHERE s.status = 'pending' AND t.type = 4"
    )


def downgrade() -> None:
    # Irreversible data cleanup. Purged rows are regenerable from
    # ml_raw_predictions via scripts/ml_remap.py once
    # ML_CHARACTER_SUGGESTIONS_ENABLED is re-enabled.
    pass
