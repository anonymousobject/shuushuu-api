"""Thin async wrapper around aioboto3 for Cloudflare R2 (S3-compatible).

A single shared aioboto3.Session is reused for the life of the app.
The adapter exposes only the operations the app needs; no leaky AWS details.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aioboto3
from aiobotocore.config import AioConfig

from app.core.logging import get_logger

logger = get_logger(__name__)

# Bound every R2 request so a stalled connection can't hang the caller forever.
# read_timeout covers server-side copy_object for large objects too; retries
# cover transient network blips and 5xx responses from R2's edge.
_R2_CLIENT_CONFIG = AioConfig(
    connect_timeout=10,
    read_timeout=60,
    retries={"max_attempts": 3, "mode": "standard"},
)


class R2Storage:
    """R2 storage adapter. Wraps aioboto3 client calls with our semantics."""

    def __init__(
        self,
        session: aioboto3.Session,
        endpoint_url: str | None,
    ) -> None:
        self._session = session
        self._endpoint_url = endpoint_url
        self._shared_client_stack: list[Any] = []

    def _client(self) -> Any:
        """Yield a short-lived aioboto3 S3 client (async context manager)."""
        return self._session.client("s3", endpoint_url=self._endpoint_url, config=_R2_CLIENT_CONFIG)

    @asynccontextmanager
    async def bulk_session(self):  # type: ignore[no-untyped-def]
        """Open one long-lived client for a burst of R2 ops.

        While active, all R2Storage methods reuse the shared client instead of
        opening a fresh one per call — eliminating TCP+TLS setup per request.
        Designed for scripts (split-existing, reconcile) that issue hundreds
        or thousands of ops in a row; app request paths should not use this.
        Safe under concurrent coroutine use — aiobotocore clients multiplex.
        Nests: inner blocks reuse the outer client.
        """
        if self._shared_client_stack:
            # Nested bulk_session: reuse outer client, no new connection.
            self._shared_client_stack.append(self._shared_client_stack[-1])
            try:
                yield
            finally:
                self._shared_client_stack.pop()
            return
        async with self._client() as s3:
            self._shared_client_stack.append(s3)
            try:
                yield
            finally:
                self._shared_client_stack.pop()

    @asynccontextmanager
    async def _acquire_client(self):  # type: ignore[no-untyped-def]
        """Yield the bulk-session client if one is active, else a one-shot client."""
        if self._shared_client_stack:
            yield self._shared_client_stack[-1]
            return
        async with self._client() as s3:
            yield s3

    async def upload_file(self, bucket: str, key: str, path: Path) -> None:
        """Upload a local file to `{bucket}/{key}`."""
        async with self._acquire_client() as s3:
            await s3.upload_file(str(path), bucket, key)

    async def copy_object(self, src_bucket: str, dst_bucket: str, key: str) -> None:
        """Copy an object between buckets, preserving the key."""
        async with self._acquire_client() as s3:
            await s3.copy_object(
                Bucket=dst_bucket,
                Key=key,
                CopySource={"Bucket": src_bucket, "Key": key},
            )

    async def delete_object(self, bucket: str, key: str) -> None:
        """Delete an object. Idempotent — S3 treats missing keys as success."""
        async with self._acquire_client() as s3:
            await s3.delete_object(Bucket=bucket, Key=key)

    async def object_exists(self, bucket: str, key: str) -> bool:
        """Return True iff the object exists."""
        async with self._acquire_client() as s3:
            try:
                await s3.head_object(Bucket=bucket, Key=key)
                return True
            except s3.exceptions.ClientError as e:
                # botocore may stringify head_object 404 as "404", "NotFound",
                # or "NoSuchKey" depending on version — accept all three.
                if e.response["Error"]["Code"] in {"404", "NotFound", "NoSuchKey"}:
                    return False
                raise

    async def generate_presigned_url(self, bucket: str, key: str, ttl: int) -> str:
        """Generate a short-lived GET URL for a private-bucket object."""
        async with self._acquire_client() as s3:
            return await s3.generate_presigned_url(  # type: ignore[no-any-return]
                ClientMethod="get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=ttl,
            )


class DummyR2Storage:
    """No-op R2 storage for R2_ENABLED=false mode.

    Every method raises RuntimeError so any accidental call surfaces
    loudly rather than silently succeeding.
    """

    _ERR = (
        "R2 is disabled (R2_ENABLED=false). This code path should not have "
        "reached the R2 storage adapter."
    )

    @asynccontextmanager
    async def bulk_session(self):  # type: ignore[no-untyped-def]
        raise RuntimeError(self._ERR)
        yield  # pragma: no cover  (unreachable; satisfies generator typing)

    async def upload_file(self, bucket: str, key: str, path: Path) -> None:
        raise RuntimeError(self._ERR)

    async def copy_object(self, src_bucket: str, dst_bucket: str, key: str) -> None:
        raise RuntimeError(self._ERR)

    async def delete_object(self, bucket: str, key: str) -> None:
        raise RuntimeError(self._ERR)

    async def object_exists(self, bucket: str, key: str) -> bool:
        raise RuntimeError(self._ERR)

    async def generate_presigned_url(self, bucket: str, key: str, ttl: int) -> str:
        raise RuntimeError(self._ERR)
