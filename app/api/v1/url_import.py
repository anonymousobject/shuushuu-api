"""URL-import endpoints: resolve external post URLs and proxy vetted image fetches.

Design: docs/plans/2026-07-06-url-import-design.md (frontend repo).
"""

import io
import os

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Response, status
from PIL import Image as PILImage

from app.config import settings
from app.core.auth import VerifiedUser
from app.core.redis import get_redis
from app.schemas.url_import import ResolvedImageOut, UrlResolveRequest, UrlResolveResponse
from app.services.rate_limit import check_external_fetch_rate_limit, check_url_resolve_rate_limit
from app.services.url_import import (
    BROWSER_USER_AGENT,
    PostNotFoundError,
    RestrictedContentError,
    UpstreamError,
    get_resolver,
    supported_sites,
)
from app.services.url_import.tokens import InvalidTokenError, mint_token, verify_token

router = APIRouter(prefix="/images", tags=["url-import"])

RESOLVE_TIMEOUT_SECONDS = 15.0
FETCH_TIMEOUT_SECONDS = 30.0


def _make_http_client(timeout: float) -> httpx.AsyncClient:
    """Factory indirection so tests can substitute a MockTransport client.

    HTTP/2 is required because danbooru's Cloudflare front door challenges
    HTTP/1.1 requests from non-browser TLS stacks (e.g. Python's httpx).
    """
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False, http2=True)


@router.post("/resolve-url", response_model=UrlResolveResponse)
async def resolve_url(
    payload: UrlResolveRequest,
    current_user: VerifiedUser,
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> UrlResolveResponse:
    """Resolve a supported external post URL to importable image candidates."""
    await check_url_resolve_rate_limit(current_user.id, redis_client)
    resolver = get_resolver(payload.url)
    if resolver is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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


@router.get("/fetch-external")
async def fetch_external(
    token: str,
    current_user: VerifiedUser,
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> Response:
    """Proxy a resolver-vetted external image to the client.

    Only HMAC tokens minted by resolve-url are accepted — this is not an
    open proxy (design doc §Security posture).
    """
    await check_external_fetch_rate_limit(current_user.id, redis_client)
    try:
        ref = verify_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired token"
        ) from exc

    request_headers = {"User-Agent": BROWSER_USER_AGENT, **ref.headers}
    try:
        async with _make_http_client(FETCH_TIMEOUT_SECONDS) as client:
            async with client.stream("GET", ref.url, headers=request_headers) as upstream:
                if upstream.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Upstream returned HTTP {upstream.status_code}",
                    )
                content_type = upstream.headers.get("content-type", "")
                if not content_type.startswith("image/"):
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Upstream did not return an image",
                    )
                declared = upstream.headers.get("content-length")
                if declared:
                    try:
                        declared_size = int(declared)
                    except ValueError:
                        declared_size = None
                    if declared_size is not None and declared_size > settings.MAX_IMAGE_SIZE:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail="Image exceeds the maximum upload size",
                        )
                chunks: list[bytes] = []
                total = 0
                async for chunk in upstream.aiter_bytes():
                    total += len(chunk)
                    if total > settings.MAX_IMAGE_SIZE:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail="Image exceeds the maximum upload size",
                        )
                    chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch image from upstream",
        ) from exc
    return Response(
        content=b"".join(chunks),
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


if settings.ENVIRONMENT == "development":

    @router.get("/url-import-fixture/{name}")
    async def url_import_fixture_image(name: str) -> Response:
        """Dev-only: random-noise PNG so e2e imports never MD5-collide."""
        image = PILImage.frombytes("RGB", (64, 64), os.urandom(64 * 64 * 3))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return Response(content=buffer.getvalue(), media_type="image/png")
