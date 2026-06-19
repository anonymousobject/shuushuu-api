"""ml_tag_suggestions (status, tag_id) index

Revision ID: 2696017fe1f1
Revises: edb3f5912896
Create Date: 2026-06-19 11:04:42.852809

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2696017fe1f1'
down_revision: str | Sequence[str] | None = 'edb3f5912896'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("idx_ml_suggestion_status_tag", "ml_tag_suggestions", ["status", "tag_id"])
    op.drop_index("idx_ml_suggestion_status", table_name="ml_tag_suggestions")


def downgrade() -> None:
    op.create_index("idx_ml_suggestion_status", "ml_tag_suggestions", ["status"])
    op.drop_index("idx_ml_suggestion_status_tag", table_name="ml_tag_suggestions")
