"""drop unused columns from privmsgs, set date non-nullable


Revision ID: 8619a9fc7189
Revises: 6f22c3978270
Create Date: 2025-11-09 16:18:27.956105

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8619a9fc7189'
down_revision: Union[str, Sequence[str], None] = '6f22c3978270'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('privmsgs', 'type')
    op.drop_column('privmsgs', 'card')
    op.drop_column('privmsgs', 'cardpath')
    op.alter_column('privmsgs', 'date',
               existing_type=sa.DATETIME(),
               server_default=sa.text('current_timestamp()'),
               nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('privmsgs', sa.Column('type', sa.Integer(), nullable=True))
    op.add_column('privmsgs', sa.Column('card', sa.Integer(), nullable=True))
    op.add_column('privmsgs', sa.Column('cardpath', sa.String(255), nullable=True))
    op.alter_column('privmsgs', 'date',
               existing_type=sa.DATETIME(),
               nullable=True,
               server_default=sa.text('current_timestamp()'))
