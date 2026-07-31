"""add composite indexes for tag and user history scans

Revision ID: acc9162c52ae
Revises: b6f974207eb7
Create Date: 2026-07-31 08:21:07.959406

"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'acc9162c52ae'
down_revision: str | Sequence[str] | None = 'b6f974207eb7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add composite indexes for date-ordered tag/user history scans.

    tag_links has ~14.7M rows in prod; each of its two indexes took ~17-24s
    to build on dev hardware (282 MB and 340 MB respectively). tag_history
    is much smaller (320k rows) and its two indexes build in under a second
    (7.5 MB each). All four use InnoDB online DDL (ALGORITHM INPLACE, LOCK
    NONE) -- no table lock expected on MariaDB 11.8.

    tag_links (user_id, date_linked, image_id) keeps image_id explicit on
    purpose: with just (user_id, date_linked), InnoDB's implicit PK suffix
    makes the on-disk index order (date_linked, tag_id, image_id), which
    mismatches the query's ORDER BY date_linked, image_id and leaves a
    1.13M-row filesort (measured 216ms vs 0.7ms with the explicit column).
    Same index size either way -- do not drop it to two columns.
    """
    op.execute(
        "CREATE INDEX idx_tag_links_tag_date ON tag_links (tag_id, date_linked) "
        "ALGORITHM INPLACE LOCK NONE"
    )
    op.execute(
        "CREATE INDEX idx_tag_links_user_date_image ON tag_links "
        "(user_id, date_linked, image_id) ALGORITHM INPLACE LOCK NONE"
    )
    op.execute(
        "CREATE INDEX idx_tag_history_tag_date ON tag_history (tag_id, date) "
        "ALGORITHM INPLACE LOCK NONE"
    )
    op.execute(
        "CREATE INDEX idx_tag_history_user_date ON tag_history (user_id, date) "
        "ALGORITHM INPLACE LOCK NONE"
    )


def downgrade() -> None:
    """Drop the four composite indexes."""
    op.execute("DROP INDEX idx_tag_history_user_date ON tag_history")
    op.execute("DROP INDEX idx_tag_history_tag_date ON tag_history")
    op.execute("DROP INDEX idx_tag_links_user_date_image ON tag_links")
    op.execute("DROP INDEX idx_tag_links_tag_date ON tag_links")
