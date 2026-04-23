"""Side effects for image status transitions.

Currently just the R2 bucket-move enqueue. Any status-change code path must
route through this helper (or enqueue `sync_image_status_job` directly) so
the canonical R2 object follows the public/protected boundary — otherwise
a public→protected transition leaves the image reachable via CDN until the
edge TTL expires.
"""

from app.config import settings
from app.core.r2_constants import PUBLIC_IMAGE_STATUSES_FOR_R2
from app.tasks.queue import enqueue_job


async def enqueue_r2_sync_on_status_change(
    image_id: int,
    old_status: int,
    new_status: int,
) -> None:
    """Enqueue R2 bucket move for an image whose status changed.

    No-op when R2 is disabled, the status is unchanged, or the transition
    stays on the same side of the public/protected boundary (e.g.
    ACTIVE→SPOILER, both public) — the worker would early-return in those
    cases, so skipping here saves an enqueue + DB round-trip per hop. MUST
    be called AFTER the DB commit that persists `new_status`; the worker
    loads the row in a fresh session and derives the destination bucket
    from its current status.
    """
    if not settings.R2_ENABLED or old_status == new_status:
        return
    if (old_status in PUBLIC_IMAGE_STATUSES_FOR_R2) == (new_status in PUBLIC_IMAGE_STATUSES_FOR_R2):
        return
    await enqueue_job(
        "sync_image_status_job",
        image_id=image_id,
        old_status=old_status,
        new_status=new_status,
    )
