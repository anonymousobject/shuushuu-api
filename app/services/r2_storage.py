"""Thin async wrapper around aioboto3 for Cloudflare R2 (S3-compatible).

A single shared aioboto3.Session is reused for the life of the app.
The adapter exposes only the operations the app needs; no leaky AWS details.
"""

from pathlib import Path

import aioboto3

from app.core.logging import get_logger

logger = get_logger(__name__)


class R2Storage:
    """R2 storage adapter. Wraps aioboto3 client calls with our semantics."""

    def __init__(
        self,
        session: aioboto3.Session,
        endpoint_url: str | None,
    ) -> None:
        self._session = session
        self._endpoint_url = endpoint_url

    def _client(self):
        """Yield a short-lived aioboto3 S3 client (async context manager)."""
        return self._session.client("s3", endpoint_url=self._endpoint_url)

    async def upload_file(self, bucket: str, key: str, path: Path) -> None:
        """Upload a local file to `{bucket}/{key}`."""
        async with self._client() as s3:
            await s3.upload_file(str(path), bucket, key)

    async def copy_object(self, src_bucket: str, dst_bucket: str, key: str) -> None:
        """Copy an object between buckets, preserving the key."""
        async with self._client() as s3:
            await s3.copy_object(
                Bucket=dst_bucket,
                Key=key,
                CopySource={"Bucket": src_bucket, "Key": key},
            )

    async def delete_object(self, bucket: str, key: str) -> None:
        """Delete an object. Idempotent — S3 treats missing keys as success."""
        async with self._client() as s3:
            await s3.delete_object(Bucket=bucket, Key=key)

    async def object_exists(self, bucket: str, key: str) -> bool:
        """Return True iff the object exists."""
        async with self._client() as s3:
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
        async with self._client() as s3:
            return await s3.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=ttl,
            )
