"""image status deactivated

Revision ID: ba007b19d0f1
Revises: 301d283488cc
Create Date: 2026-06-03 14:18:15.002341

"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'ba007b19d0f1'
down_revision: str | Sequence[str] | None = '301d283488cc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add reason columns; convert legacy disable statuses to DEACTIVATED; backfill triage history."""
    # --- Schema: add columns ---
    # images is large: metadata-only adds, INSTANT algorithm, no lock.
    op.execute(
        "ALTER TABLE images "
        "ADD COLUMN reason_category INT NULL, "
        "ADD COLUMN status_reason VARCHAR(1000) NULL, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )
    op.execute(
        "ALTER TABLE image_status_history "
        "ADD COLUMN reason_category INT NULL, "
        "ADD COLUMN reason VARCHAR(1000) NULL, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )

    # --- Data: backfill reason_category for deactivated images ---
    # DEACTIVATED == 0, so existing status=0 (old OTHER/disabled) images stay at 0 and
    # only gain a reason_category; legacy -2/-3 images move to 0 + their category.
    # status_reason stays NULL: the old system never captured a reason.
    # ORDER MATTERS: backfill the existing 0-rows FIRST, then convert -2/-3 to 0 —
    # otherwise the cat-4 backfill would clobber the just-converted -2/-3 rows.
    #  0 (was OTHER)   -> stay 0 (DEACTIVATED) + cat 4 (Other)
    # -2 INAPPROPRIATE -> 0 (DEACTIVATED)      + cat 1 (Inappropriate)
    # -3 LOW_QUALITY   -> 0 (DEACTIVATED)      + cat 2 (Low Quality)
    op.execute("UPDATE images SET reason_category = 4 WHERE status = 0")
    op.execute("UPDATE images SET status = 0, reason_category = 1 WHERE status = -2")
    op.execute("UPDATE images SET status = 0, reason_category = 2 WHERE status = -3")

    # --- Data: backfill triage-gap history rows ---
    # action_report (admin_actions.action_type = 2 = REPORT_ACTION) historically updated
    # image status WITHOUT writing an image_status_history row. Reconstruct those rows from
    # the audit log so the public history is complete. Use the ORIGINAL recorded int values
    # (do NOT convert to DEACTIVATED) — history must reflect what happened at the time.
    op.execute(
        """
        INSERT INTO image_status_history (image_id, old_status, new_status, user_id, created_at)
        SELECT aa.image_id,
               CAST(JSON_EXTRACT(aa.details, '$.previous_status') AS SIGNED),
               CAST(JSON_EXTRACT(aa.details, '$.new_status') AS SIGNED),
               aa.user_id,
               aa.created_at
        FROM admin_actions aa
        WHERE aa.action_type = 2
          AND aa.image_id IS NOT NULL
          AND JSON_EXTRACT(aa.details, '$.new_status') IS NOT NULL
          AND JSON_EXTRACT(aa.details, '$.previous_status') IS NOT NULL
          AND CAST(JSON_EXTRACT(aa.details, '$.previous_status') AS SIGNED)
              <> CAST(JSON_EXTRACT(aa.details, '$.new_status') AS SIGNED)
        """
    )


def downgrade() -> None:
    """Drop the added columns. Legacy status conversion and backfilled history rows are
    not reverted — they represent real, historically-accurate events."""
    op.execute("ALTER TABLE image_status_history DROP COLUMN reason, DROP COLUMN reason_category")
    op.execute("ALTER TABLE images DROP COLUMN status_reason, DROP COLUMN reason_category")
