"""HMAC-signed, short-lived references to resolver-vetted upstream URLs.

The fetch-external proxy only honors these tokens, so it can never be used
as an open proxy: every fetchable URL was minted by a resolver.
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from app.config import settings

DEFAULT_TTL_SECONDS = 600


class InvalidTokenError(Exception):
    """Token is malformed, tampered with, or expired."""


@dataclass
class ExternalFetchRef:
    url: str
    headers: dict[str, str]
    expires_at: int


def _sign(payload: bytes) -> bytes:
    return hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).digest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def mint_token(
    url: str,
    headers: dict[str, str] | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    payload = json.dumps(
        {"u": url, "h": headers or {}, "e": int(time.time()) + ttl_seconds},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"{_b64encode(payload)}.{_b64encode(_sign(payload))}"


def verify_token(token: str) -> ExternalFetchRef:
    try:
        payload_part, signature_part = token.split(".")
        payload = _b64decode(payload_part)
        signature = _b64decode(signature_part)
    except (ValueError, TypeError) as exc:
        raise InvalidTokenError("malformed token") from exc
    if not hmac.compare_digest(_sign(payload), signature):
        raise InvalidTokenError("bad signature")
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise InvalidTokenError("malformed payload") from exc
    try:
        url, headers, expires_at = data["u"], data["h"], data["e"]
    except (KeyError, TypeError) as exc:
        raise InvalidTokenError("malformed payload") from exc
    if expires_at < time.time():
        raise InvalidTokenError("expired")
    return ExternalFetchRef(url=url, headers=headers, expires_at=expires_at)
