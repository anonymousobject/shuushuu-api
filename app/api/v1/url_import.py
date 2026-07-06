"""URL-import endpoints: resolve external post URLs and proxy vetted image fetches.

Design: docs/plans/2026-07-06-url-import-design.md (frontend repo).
"""

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import VerifiedUser
from app.core.redis import get_redis
from app.schemas.url_import import ResolvedImageOut, UrlResolveRequest, UrlResolveResponse
from app.services.rate_limit import check_url_resolve_rate_limit
from app.services.url_import import (
    PostNotFoundError,
    RestrictedContentError,
    UpstreamError,
    get_resolver,
    supported_sites,
)
from app.services.url_import.tokens import mint_token

router = APIRouter(prefix="/images", tags=["url-import"])

RESOLVE_TIMEOUT_SECONDS = 15.0
FETCH_TIMEOUT_SECONDS = 30.0


def _make_http_client(timeout: float) -> httpx.AsyncClient:
    """Factory indirection so tests can substitute a MockTransport client."""
    return httpx.AsyncClient(timeout=timeout)


@router.post("/resolve-url", response_model=UrlResolveResponse)
async def resolve_url(
    payload: UrlResolveRequest,
    current_user: VerifiedUser,
    redis_client: redis.Redis = Depends(get_redis),
) -> UrlResolveResponse:
    """Resolve a supported external post URL to importable image candidates."""
    await check_url_resolve_rate_limit(current_user.id, redis_client)
    resolver = get_resolver(payload.url)
    if resolver is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Unsupported site. Supported: "
                + ", ".join(site for site in supported_sites() if site != "fixture")
            ),
        )
    try:
        async with _make_http_client(RESOLVE_TIMEOUT_SECONDS) as client:
            post = await resolver.resolve(payload.url, client)
    except PostNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RestrictedContentError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except UpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return UrlResolveResponse(
        site=post.site,
        canonical_url=post.canonical_url,
        title=post.title,
        artist_name=post.artist_name,
        images=[
            ResolvedImageOut(
                token=mint_token(image.full_url, image.headers),
                thumb_token=(
                    mint_token(image.thumb_url, image.headers) if image.thumb_url else None
                ),
                width=image.width,
                height=image.height,
            )
            for image in post.images
        ],
    )
