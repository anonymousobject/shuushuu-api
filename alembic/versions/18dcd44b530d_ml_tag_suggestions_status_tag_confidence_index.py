"""ml_tag_suggestions status_tag_confidence index

Revision ID: 18dcd44b530d
Revises: 2696017fe1f1
Create Date: 2026-07-05 08:16:06.230442

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '18dcd44b530d'
down_revision: str | Sequence[str] | None = '2696017fe1f1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("idx_ml_suggestion_status_tag", table_name="ml_tag_suggestions")
    op.create_index(
        "idx_ml_suggestion_status_tag",
        "ml_tag_suggestions",
        ["status", "tag_id", "confidence"],
    )


def downgrade() -> None:
    op.drop_index("idx_ml_suggestion_status_tag", table_name="ml_tag_suggestions")
    op.create_index("idx_ml_suggestion_status_tag", "ml_tag_suggestions", ["status", "tag_id"])
