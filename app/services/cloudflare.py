"""Cloudflare API helpers.

Currently covers cache purge only. Expand here as more Cloudflare features
are needed (e.g., WAF, DNS) — don't sprinkle Cloudflare calls across services.
"""

import httpx

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Free plan caps purge-by-URL calls at 30 URLs per request.
_BATCH_SIZE = 30


async def purge_cache_by_urls(urls: list[str]) -> None:
    """Purge Cloudflare's edge cache for the given URLs.

    Batches into groups of 30 (free-plan limit). No-op when list is empty.
    Raises httpx.HTTPStatusError on non-2xx responses so the caller can
    surface the failure to its retry/alerting machinery.
    """
    if not urls:
        return

    if not settings.CLOUDFLARE_ZONE_ID or not settings.CLOUDFLARE_API_TOKEN:
        # Fail loudly — a silent no-op here would mask misconfiguration and
        # stale CDN content for minutes or hours.
        raise RuntimeError(
            "purge_cache_by_urls called but CLOUDFLARE_ZONE_ID / "
            "CLOUDFLARE_API_TOKEN are not configured"
        )

    api_url = (
        f"https://api.cloudflare.com/client/v4/zones/{settings.CLOUDFLARE_ZONE_ID}/purge_cache"
    )
    headers = {
        "Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        for start in range(0, len(urls), _BATCH_SIZE):
            batch = urls[start : start + _BATCH_SIZE]
            logger.info("r2_cdn_purge_started", batch_size=len(batch))
            response = await client.post(
                api_url,
                json={"files": batch},
                headers=headers,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                logger.error(
                    "r2_cdn_purge_failed",
                    status_code=response.status_code,
                    body=response.text,
                )
                raise
            logger.info("r2_cdn_purge_succeeded", batch_size=len(batch))
