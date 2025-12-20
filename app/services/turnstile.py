"""Cloudflare Turnstile verification service."""

import httpx
from fastapi import HTTPException, status

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def verify_turnstile_token(token: str, ip_address: str | None = None) -> None:
    """
    Verify Cloudflare Turnstile challenge response.

    Args:
        token: The turnstile_token from the request body
        ip_address: Optional user IP for additional verification

    Raises:
        HTTPException: 400 if verification fails
    """
    # Bypass for testing
    if settings.ENVIRONMENT == "development" and token == "test-token":
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = {
                "secret": settings.TURNSTILE_SECRET_KEY,
                "response": token,
            }
            if ip_address:
                payload["remoteip"] = ip_address

            response = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data=payload,
            )
            response.raise_for_status()
            result = response.json()

            if not result.get("success"):
                error_codes = result.get("error-codes", [])
                logger.warning(
                    "turnstile_verification_failed",
                    error_codes=error_codes,
                    ip_address=ip_address,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="CAPTCHA verification failed. Please try again.",
                )

            logger.info("turnstile_verification_success", ip_address=ip_address)

    except httpx.HTTPError as e:
        logger.error("turnstile_api_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CAPTCHA service temporarily unavailable. Please try again.",
        ) from e
