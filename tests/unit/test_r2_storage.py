"""Tests for R2Storage adapter (backed by moto ThreadedMotoServer).

Note: mock_aws() patches the sync botocore HTTP layer but aiobotocore 2.x
awaits async HTTP responses — incompatible on this stack. ThreadedMotoServer
starts a real Flask-based HTTP server so aiobotocore can make genuine async
HTTP calls against a local mock endpoint.
"""

import time
from pathlib import Path

import aioboto3
import pytest
from moto.server import ThreadedMotoServer

from app.services.r2_storage import R2Storage

_CREDS = {
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
    "region_name": "us-east-1",
}


@pytest.fixture(scope="module")
def moto_server():
    """Start a ThreadedMotoServer for the duration of this test module."""
    server = ThreadedMotoServer(port=0)
    server.start()
    time.sleep(0.2)  # let the server come up
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture
def moto_session(moto_server):
    """aioboto3 session pointed at the moto server."""
    return aioboto3.Session(**_CREDS)


@pytest.fixture
async def storage(moto_session, moto_server):
    """R2Storage instance wired to the moto server endpoint."""
    return R2Storage(session=moto_session, endpoint_url=moto_server)


@pytest.fixture
async def setup_buckets(storage, moto_session, moto_server):
    async with moto_session.client("s3", endpoint_url=moto_server) as s3:
        await s3.create_bucket(Bucket="public")
        await s3.create_bucket(Bucket="private")
    yield storage
    # Cleanup: empty and delete buckets so tests are isolated
    async with moto_session.client("s3", endpoint_url=moto_server) as s3:
        for bucket in ("public", "private"):
            try:
                resp = await s3.list_objects_v2(Bucket=bucket)
                for obj in resp.get("Contents", []):
                    await s3.delete_object(Bucket=bucket, Key=obj["Key"])
                await s3.delete_bucket(Bucket=bucket)
            except Exception:
                pass


@pytest.mark.unit
class TestR2Storage:
    async def test_upload_and_exists(self, setup_buckets, tmp_path: Path):
        storage = setup_buckets
        src = tmp_path / "a.bin"
        src.write_bytes(b"hello")
        await storage.upload_file(bucket="public", key="fullsize/a.bin", path=src)
        assert await storage.object_exists(bucket="public", key="fullsize/a.bin") is True
        assert await storage.object_exists(bucket="public", key="fullsize/missing.bin") is False

    async def test_copy_object(self, setup_buckets, tmp_path: Path):
        storage = setup_buckets
        src = tmp_path / "a.bin"
        src.write_bytes(b"data")
        await storage.upload_file(bucket="public", key="fullsize/a.bin", path=src)
        await storage.copy_object(
            src_bucket="public", dst_bucket="private", key="fullsize/a.bin"
        )
        assert await storage.object_exists(bucket="private", key="fullsize/a.bin") is True

    async def test_delete_object(self, setup_buckets, tmp_path: Path):
        storage = setup_buckets
        src = tmp_path / "a.bin"
        src.write_bytes(b"data")
        await storage.upload_file(bucket="public", key="fullsize/a.bin", path=src)
        await storage.delete_object(bucket="public", key="fullsize/a.bin")
        assert await storage.object_exists(bucket="public", key="fullsize/a.bin") is False

    async def test_delete_missing_is_idempotent(self, setup_buckets):
        storage = setup_buckets
        # Deleting a key that doesn't exist must not raise — S3 returns 204.
        await storage.delete_object(bucket="public", key="fullsize/missing.bin")

    async def test_generate_presigned_url(self, setup_buckets, tmp_path: Path):
        storage = setup_buckets
        src = tmp_path / "a.bin"
        src.write_bytes(b"data")
        await storage.upload_file(bucket="private", key="fullsize/a.bin", path=src)
        url = await storage.generate_presigned_url(
            bucket="private", key="fullsize/a.bin", ttl=60
        )
        assert "a.bin" in url
        assert "Signature" in url or "X-Amz-Signature" in url
