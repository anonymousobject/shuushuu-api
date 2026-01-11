"""drop unused columns from privmsgs, users, images. Set date non-nullable


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
    # Drop unused user columns
    op.drop_column('users', 'last_login_new')
    op.drop_column('users', 'timezone')  # No longer needed - frontend handles timezone conversion
    op.drop_column('users', 'aim')  # Deprecated field
    op.drop_column('users', 'rating_ratio')
    op.drop_column('users', 'infected')
    op.drop_column('users', 'infected_by')
    op.drop_column('users', 'date_infected')

    # Drop unused privmsg columns
    op.drop_column('privmsgs', 'type')
    op.drop_column('privmsgs', 'card')
    op.drop_column('privmsgs', 'cardpath')
    op.alter_column('privmsgs', 'date',
               existing_type=sa.DATETIME(),
               server_default=sa.text('current_timestamp()'),
               nullable=False)

    # Drop unused images columns
    op.drop_column('images', 'artist') # Tags now handle artist info
    op.drop_column('images', 'characters') # Tags now handle character info
    op.drop_column('images', 'change_id')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('users', sa.Column('last_login_new', sa.DATETIME(), nullable=True))
    op.add_column('users', sa.Column('timezone', sa.DECIMAL(5, 2), nullable=False, server_default='0.00'))
    op.add_column('users', sa.Column('aim', sa.String(50), nullable=True))
    op.add_column('users', sa.Column('rating_ratio', sa.DECIMAL(5, 2), nullable=False, server_default='0.00'))
    op.add_column('users', sa.Column('infected', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('infected_by', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('date_infected', sa.DATETIME(), nullable=True))
    op.add_column('privmsgs', sa.Column('type', sa.Integer(), nullable=True))
    op.add_column('privmsgs', sa.Column('card', sa.Integer(), nullable=True))
    op.add_column('privmsgs', sa.Column('cardpath', sa.String(255), nullable=True))
    op.alter_column('privmsgs', 'date',
               existing_type=sa.DATETIME(),
               nullable=True,
               server_default=sa.text('current_timestamp()'))
    op.add_column('images', sa.Column('artist', sa.String(255), nullable=True))
    op.add_column('images', sa.Column('characters', sa.String(255), nullable=True))
    op.add_column('images', sa.Column('change_id', sa.Integer(), nullable=False, server_default='0'))
    op.create_index('ix_images_change_id', 'images', ['change_id'])
