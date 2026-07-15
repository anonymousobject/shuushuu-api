"""backfill approve pending ml suggestions for applied tags

Revision ID: a61c09c0f331
Revises: 12bf25199415
Create Date: 2026-07-15 16:17:46.379492

Data migration: mark pending ML tag suggestions approved when their tag is
already applied to the image. Tags applied outside the ML review flow (manual
tag add, batch tagging, report resolution) used to leave suggestion rows
'pending' forever, inflating the review-queue worklist counts. The write
paths now resolve suggestions as tags are applied; this backfills rows
stranded before that fix. reviewed_by_user_id stays NULL (the actual tagger
is unknown).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a61c09c0f331'
down_revision: str | Sequence[str] | None = '12bf25199415'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE ml_tag_suggestions s
        JOIN tag_links tl
          ON tl.image_id = s.image_id AND tl.tag_id = s.tag_id
        SET s.status = 'approved',
            s.reviewed_at = CURRENT_TIMESTAMP()
        WHERE s.status = 'pending'
        """
    )


def downgrade() -> None:
    # Irreversible data migration: backfilled rows are indistinguishable from
    # suggestions approved through the review flow. Intentionally a no-op.
    pass
