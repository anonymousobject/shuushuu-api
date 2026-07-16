"""backfill delete suggestions for ineligible images

Data migration (ADR-0002): suggestion rows may exist only on
suggestion-eligible images (status ACTIVE=1, SPOILER=2). Reposts (-1) leave
review scope permanently and lose ALL rows (matching the favorites/ratings/
tags wipe at repost-marking); other ineligible statuses (DEACTIVATED=0,
legacy -2/-3, REVIEW=-4) lose only pending rows — reviewed rows keep
provenance for tags still applied to those images.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7d98087eabcb'
down_revision: str | Sequence[str] | None = 'a61c09c0f331'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DELETE s FROM ml_tag_suggestions s
        JOIN images i ON i.image_id = s.image_id
        WHERE i.status = -1
        """
    )
    op.execute(
        """
        DELETE s FROM ml_tag_suggestions s
        JOIN images i ON i.image_id = s.image_id
        WHERE i.status NOT IN (1, 2) AND i.status <> -1
          AND s.status = 'pending'
        """
    )


def downgrade() -> None:
    # Irreversible data migration: deleted rows are re-seedable from the
    # raw-prediction store on restore (ADR-0002). Intentionally a no-op.
    pass
