"""Fixtures for unit tests."""

import time

import aioboto3
import pytest
from moto.server import ThreadedMotoServer

from app.services.r2_storage import R2Storage

# Note: redis_client comes from tests/conftest.py (it previously had a
# near-identical copy here that hardcoded Redis DB 15, which would break
# per-worker isolation under pytest-xdist).


# =============================================================================
# moto / R2 storage fixtures
# =============================================================================
# Lifted from tests/unit/test_r2_storage.py so multiple test modules can reuse
# them. mock_aws() patches the sync botocore HTTP layer but aiobotocore 2.x
# awaits async HTTP responses — incompatible on this stack. ThreadedMotoServer
# starts a real Flask-based HTTP server so aiobotocore can make genuine async
# HTTP calls against a local mock endpoint.

_R2_TEST_CREDS = {
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
    "region_name": "us-east-1",
}


@pytest.fixture(scope="module")
def moto_server():
    """Start a ThreadedMotoServer for the duration of the test module."""
    server = ThreadedMotoServer(port=0)
    server.start()
    time.sleep(0.2)  # let the server come up
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture
def moto_session(moto_server):
    """aioboto3 session pointed at the moto server."""
    return aioboto3.Session(**_R2_TEST_CREDS)


@pytest.fixture
async def storage(moto_session, moto_server):
    """R2Storage instance wired to the moto server endpoint."""
    return R2Storage(session=moto_session, endpoint_url=moto_server)


@pytest.fixture
async def setup_buckets(storage, moto_session, moto_server):
    """Create public and private buckets and clean them up after the test."""
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
