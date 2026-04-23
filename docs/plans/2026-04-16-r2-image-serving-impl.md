# R2 Image Serving Implementation Plan

> **For Agents:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use `**Step N:**` format; tick them off by updating this file as you complete each one if desired.

**Goal:** Serve images from Cloudflare R2 (two buckets — public via CDN, private via presigned URLs) while keeping local filesystem as a fallback. Dual-write new uploads. Gated behind `R2_ENABLED` so dev stays local-only.

**Architecture:** Thin `R2Storage` adapter over `aioboto3`. A `r2_location` tri-state column on `images` drives URL generation: public-CDN, presigned-private, or local-FS fallback. Finalizer ARQ job uploads to R2 after variant jobs complete and flips `r2_location`. Status-change and delete flows enqueue their own ARQ jobs that move/purge objects and invalidate Cloudflare cache. A `scripts/r2_sync.py` CLI provides one-time migration and ongoing reconciliation.

**Tech Stack:** FastAPI, SQLModel, Alembic, aioboto3, ARQ, moto (S3 mocking), httpx (Cloudflare API), pytest

**Design doc:** `docs/plans/2026-04-16-r2-image-serving-design.md`

---

## Chunk 1: Foundation

Groundwork: dependencies, config, DB migration, model changes, shared constants. Everything after this assumes `r2_location` exists on `Images` and the `R2Location` enum is importable.

---

### Task 1: Add Dependencies

**Files:**
- Modify: `pyproject.toml` (dependencies list)

**Step 1: Add aioboto3 to main dependencies**

In `pyproject.toml`, add after the `arq>=0.27.0` entry in `dependencies`:

```toml
    "aioboto3>=13.0.0",  # Async S3-compatible client for Cloudflare R2
```

**Step 2: Add moto and types-aioboto3 to dev dependencies**

In `pyproject.toml` under `[dependency-groups]` `dev`, add:

```toml
    "moto[s3]>=5.0.0",  # Mock S3 for R2 adapter tests
    "types-aioboto3>=13.0.0",  # Type stubs
```

**Step 3: Install**

Run: `uv sync`
Expected: `aioboto3`, `moto`, `types-aioboto3` installed; no other changes.

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat(r2): add aioboto3 and moto dependencies"
```

---

### Task 2: Add R2 Settings to Config

**Files:**
- Modify: `app/config.py`

**Step 1: Add R2 and Cloudflare settings**

In `app/config.py`, add a new settings block after the `S3_REGION` field (around line 76). The existing `STORAGE_TYPE` and `S3_*` fields remain for now; they're removed in a later task to keep this step scoped.

```python
    # Cloudflare R2 (image storage)
    R2_ENABLED: bool = Field(
        default=False,
        description="Enable R2 image serving. When false, app uses local FS only.",
    )
    R2_ACCESS_KEY_ID: str = Field(default="")
    R2_SECRET_ACCESS_KEY: str = Field(default="")
    R2_ENDPOINT: str = Field(default="", description="R2 S3-compatible endpoint URL")
    R2_PUBLIC_BUCKET: str = Field(default="shuushuu-images")
    R2_PRIVATE_BUCKET: str = Field(default="shuushuu-images-private")
    R2_PUBLIC_CDN_URL: str = Field(
        default="",
        description="Custom domain attached to the public R2 bucket (no trailing slash)",
    )
    R2_PRESIGN_TTL_SECONDS: int = Field(default=900, ge=60, le=3600)
    R2_ALLOW_BULK_BACKFILL: bool = Field(
        default=False,
        description=(
            "Gate for r2_sync.py backfill-locations and reconcile. "
            "Set true permanently in prod; leave false on staging to prevent "
            "mass-uploading prod-imported images to the staging bucket."
        ),
    )

    # Cloudflare API (for CDN cache purge)
    CLOUDFLARE_API_TOKEN: str = Field(default="")
    CLOUDFLARE_ZONE_ID: str = Field(default="")
```

**Step 2: Add validator that requires R2_* and CLOUDFLARE_* when R2_ENABLED=true**

In `app/config.py`, add a new `@model_validator(mode="after")` method inside the `Settings` class. Place it below the existing `validate_smtp_tls_settings` validator:

```python
    @model_validator(mode="after")
    def validate_r2_enabled_requirements(self) -> Settings:
        """When R2_ENABLED=true, all R2_* and CLOUDFLARE_* credentials must be set."""
        if not self.R2_ENABLED:
            return self
        required = {
            "R2_ACCESS_KEY_ID": self.R2_ACCESS_KEY_ID,
            "R2_SECRET_ACCESS_KEY": self.R2_SECRET_ACCESS_KEY,
            "R2_ENDPOINT": self.R2_ENDPOINT,
            "R2_PUBLIC_BUCKET": self.R2_PUBLIC_BUCKET,
            "R2_PRIVATE_BUCKET": self.R2_PRIVATE_BUCKET,
            "R2_PUBLIC_CDN_URL": self.R2_PUBLIC_CDN_URL,
            "CLOUDFLARE_API_TOKEN": self.CLOUDFLARE_API_TOKEN,
            "CLOUDFLARE_ZONE_ID": self.CLOUDFLARE_ZONE_ID,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                f"R2_ENABLED=true but these required settings are empty: {', '.join(missing)}"
            )
        return self
```

**Step 3: Write validator tests**

Create `tests/unit/test_config_r2.py`:

```python
"""Tests for R2 config validation."""

import pytest

from app.config import Settings


@pytest.mark.unit
class TestR2ConfigValidation:
    """R2_ENABLED requires all R2_* and CLOUDFLARE_* credentials."""

    def test_r2_disabled_requires_no_credentials(self):
        """When R2_ENABLED=false, empty R2/Cloudflare fields are fine."""
        s = Settings(_env_file=None, R2_ENABLED=False)
        assert s.R2_ENABLED is False

    def test_r2_enabled_requires_all_credentials(self):
        """When R2_ENABLED=true, missing credentials fail validation."""
        with pytest.raises(ValueError, match="R2_ACCESS_KEY_ID"):
            Settings(
                _env_file=None,
                R2_ENABLED=True,
                R2_ACCESS_KEY_ID="",
                R2_SECRET_ACCESS_KEY="sk",
                R2_ENDPOINT="https://example.r2.cloudflarestorage.com",
                R2_PUBLIC_CDN_URL="https://cdn.example.com",
                CLOUDFLARE_API_TOKEN="tok",
                CLOUDFLARE_ZONE_ID="zone",
            )

    def test_r2_enabled_with_all_credentials_passes(self):
        """Full credentials pass validation."""
        s = Settings(
            _env_file=None,
            R2_ENABLED=True,
            R2_ACCESS_KEY_ID="ak",
            R2_SECRET_ACCESS_KEY="sk",
            R2_ENDPOINT="https://example.r2.cloudflarestorage.com",
            R2_PUBLIC_CDN_URL="https://cdn.example.com",
            CLOUDFLARE_API_TOKEN="tok",
            CLOUDFLARE_ZONE_ID="zone",
        )
        assert s.R2_ENABLED is True
```

**Step 4: Run tests**

Run: `uv run pytest tests/unit/test_config_r2.py -v`
Expected: 3 PASS

**Step 5: Commit**

```bash
git add app/config.py tests/unit/test_config_r2.py
git commit -m "feat(r2): add R2 config settings and validator"
```

---

### Task 3: Create R2Location Enum and Shared Constants

**Files:**
- Create: `app/core/r2_constants.py`
- Test: `tests/unit/test_r2_constants.py`

Rationale for a dedicated module: these constants are imported from models, schemas, services, and scripts. Putting them in a standalone module avoids import cycles.

**Step 1: Write failing test**

Create `tests/unit/test_r2_constants.py`:

```python
"""Tests for R2 shared constants."""

import pytest

from app.config import ImageStatus
from app.core.r2_constants import (
    PUBLIC_IMAGE_STATUSES_FOR_R2,
    R2_VARIANTS,
    R2Location,
)


@pytest.mark.unit
class TestR2Location:
    """R2Location enum values."""

    def test_values(self):
        assert R2Location.NONE == 0
        assert R2Location.PUBLIC == 1
        assert R2Location.PRIVATE == 2

    def test_int_comparison(self):
        """Enum is IntEnum so it compares to ints directly."""
        assert R2Location.NONE == 0
        assert int(R2Location.PUBLIC) == 1


@pytest.mark.unit
class TestPublicStatuses:
    """The set of statuses that map to the public bucket."""

    def test_members(self):
        assert PUBLIC_IMAGE_STATUSES_FOR_R2 == frozenset(
            {ImageStatus.ACTIVE, ImageStatus.SPOILER, ImageStatus.REPOST}
        )

    def test_is_frozen(self):
        with pytest.raises(AttributeError):
            PUBLIC_IMAGE_STATUSES_FOR_R2.add(999)  # type: ignore[attr-defined]


@pytest.mark.unit
class TestR2Variants:
    """The canonical list of variant prefixes used as R2 key prefixes."""

    def test_list(self):
        assert R2_VARIANTS == ("fullsize", "thumbs", "medium", "large")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_r2_constants.py -v`
Expected: FAIL (module not found)

**Step 3: Implement**

Create `app/core/r2_constants.py`:

```python
"""Shared constants for R2 integration.

Kept in its own module to avoid import cycles between models, schemas,
services, and the scripts/r2_sync.py CLI.
"""

from enum import IntEnum

from app.config import ImageStatus

# Image statuses served from the public R2 bucket. Any other status is protected
# and lives in the private bucket (accessed via presigned URLs).
PUBLIC_IMAGE_STATUSES_FOR_R2: frozenset[int] = frozenset(
    {ImageStatus.ACTIVE, ImageStatus.SPOILER, ImageStatus.REPOST}
)

# Key prefixes under each R2 bucket. Matches the local FS layout so the
# one-time bucket split requires no key rewriting.
R2_VARIANTS: tuple[str, ...] = ("fullsize", "thumbs", "medium", "large")


class R2Location(IntEnum):
    """Where an image's R2 objects physically live.

    NONE    — not yet synced to R2 (pending finalizer, or R2 disabled).
    PUBLIC  — canonical copy lives in R2_PUBLIC_BUCKET.
    PRIVATE — canonical copy lives in R2_PRIVATE_BUCKET.
    """

    NONE = 0
    PUBLIC = 1
    PRIVATE = 2
```

**Step 4: Run tests**

Run: `uv run pytest tests/unit/test_r2_constants.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/core/r2_constants.py tests/unit/test_r2_constants.py
git commit -m "feat(r2): add R2Location enum and shared constants"
```

---

### Task 4: Add `r2_location` Column Migration

**Files:**
- Create: `alembic/versions/<generated>_add_r2_location_to_images.py`

**Step 1: Generate migration**

Run: `uv run alembic revision -m "add r2_location to images"`
Expected: New revision file created in `alembic/versions/`. Note the revision id.

**Step 2: Edit migration**

Replace the generated file's body with (substitute `<generated>` and `<down_revision>` appropriately — the current head is `8c950e7fa6f2`):

```python
"""add r2_location to images

Revision ID: <generated>
Revises: 8c950e7fa6f2
Create Date: <generated>

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "<generated>"
down_revision: str | Sequence[str] | None = "8c950e7fa6f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add r2_location column to images.

    0 = NONE (not in R2 yet), 1 = PUBLIC bucket, 2 = PRIVATE bucket.
    Default NONE — backfill is a separate one-shot run.
    """
    op.add_column(
        "images",
        sa.Column(
            "r2_location",
            mysql.TINYINT(unsigned=True),
            nullable=False,
            server_default="0",
        ),
    )
    # Index supports reconcile/health queries (WHERE r2_location = 0)
    op.create_index(
        "idx_r2_location",
        "images",
        ["r2_location"],
        unique=False,
    )


def downgrade() -> None:
    """Remove r2_location column."""
    op.drop_index("idx_r2_location", table_name="images")
    op.drop_column("images", "r2_location")
```

**Step 3: Apply migration**

Run: `uv run alembic upgrade head`
Expected: Migration applies successfully against the dev DB.

**Step 4: Commit**

```bash
git add alembic/versions/*_add_r2_location_to_images.py
git commit -m "feat(r2): add r2_location column to images table"
```

---

### Task 5: Add `r2_location` Field to Images Model

**Files:**
- Modify: `app/models/image.py`

**Step 1: Add import and field**

In `app/models/image.py`, add the import near the top with the other app imports (around line 23):

```python
from app.core.r2_constants import R2Location
```

Add the `r2_location` field to the `Images` class. Place it after the `replacement_id` field (currently at line 243):

```python
    # R2 sync state. NONE=0 means object is not yet in R2; the finalizer
    # or `r2_sync.py reconcile` will flip it to PUBLIC or PRIVATE.
    r2_location: int = Field(default=R2Location.NONE)
```

**Step 2: Write a model test**

Create `tests/unit/test_image_r2_location.py`:

```python
"""Test r2_location field on Images model."""

import pytest

from app.core.r2_constants import R2Location
from app.models.image import Images


@pytest.mark.unit
class TestImageR2Location:
    def test_default_is_none(self):
        """New image instances default r2_location to NONE=0."""
        img = Images(ext="jpg", user_id=1)
        assert img.r2_location == R2Location.NONE == 0

    def test_can_set_public(self):
        img = Images(ext="jpg", user_id=1, r2_location=R2Location.PUBLIC)
        assert img.r2_location == 1

    def test_can_set_private(self):
        img = Images(ext="jpg", user_id=1, r2_location=R2Location.PRIVATE)
        assert img.r2_location == 2
```

**Step 3: Run tests**

Run: `uv run pytest tests/unit/test_image_r2_location.py -v`
Expected: 3 PASS

**Step 4: Run full unit suite to confirm no regressions**

Run: `uv run pytest -m unit -x --tb=short`
Expected: all pass (no changes to existing behaviour).

**Step 5: Commit**

```bash
git add app/models/image.py tests/unit/test_image_r2_location.py
git commit -m "feat(r2): add r2_location field to Images model"
```

---

## Chunk 2: Storage Adapter and Cloudflare Purge

The thin wrappers that every higher layer depends on. An explicit `DummyR2Storage` keeps disabled-mode safe — calling R2 when `R2_ENABLED=false` is a bug and must raise, not silently succeed.

---

### Task 6: R2Storage Adapter

**Files:**
- Create: `app/services/r2_storage.py`
- Test: `tests/unit/test_r2_storage.py`

**Step 1: Write failing tests**

Create `tests/unit/test_r2_storage.py`:

```python
"""Tests for R2Storage adapter (backed by moto S3 mock)."""

from pathlib import Path

import aioboto3
import pytest
from moto import mock_aws

from app.services.r2_storage import R2Storage


@pytest.fixture
def moto_s3():
    """Start a moto S3 mock and yield an aioboto3 session pointed at it."""
    with mock_aws():
        session = aioboto3.Session(
            aws_access_key_id="test",
            aws_secret_access_key="test",
            region_name="us-east-1",
        )
        yield session


@pytest.fixture
async def storage(moto_s3):
    """R2Storage instance wired to the moto session."""
    # moto's in-memory S3 endpoint is injected at the aioboto3 client level via env
    # or positional kwarg; moto.mock_aws() patches boto3's network calls transparently,
    # so no explicit endpoint_url override is required here.
    return R2Storage(session=moto_s3, endpoint_url=None)


@pytest.fixture
async def setup_buckets(storage, moto_s3):
    async with moto_s3.client("s3") as s3:
        await s3.create_bucket(Bucket="public")
        await s3.create_bucket(Bucket="private")
    return storage


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
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_r2_storage.py -v`
Expected: FAIL (module not found)

**Step 3: Implement adapter**

Create `app/services/r2_storage.py`:

```python
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
```

**Step 4: Run tests**

Run: `uv run pytest tests/unit/test_r2_storage.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add app/services/r2_storage.py tests/unit/test_r2_storage.py
git commit -m "feat(r2): add R2Storage adapter over aioboto3"
```

---

### Task 7: DummyR2Storage and DI Singleton

**Files:**
- Modify: `app/services/r2_storage.py` (add `DummyR2Storage`)
- Create: `app/core/r2_client.py` (singleton accessor)
- Test: `tests/unit/test_r2_client.py`

**Step 1: Add DummyR2Storage to r2_storage.py**

In `app/services/r2_storage.py`, append:

```python
class DummyR2Storage:
    """No-op R2 storage for R2_ENABLED=false mode.

    Every method raises RuntimeError so any accidental call surfaces
    loudly rather than silently succeeding.
    """

    _ERR = (
        "R2 is disabled (R2_ENABLED=false). This code path should not have "
        "reached the R2 storage adapter."
    )

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
```

**Step 2: Write failing tests**

Create `tests/unit/test_r2_client.py`:

```python
"""Tests for the R2 client singleton accessor."""

import pytest

from app.config import settings
from app.core.r2_client import get_r2_storage, reset_r2_storage
from app.services.r2_storage import DummyR2Storage, R2Storage


@pytest.fixture(autouse=True)
def _reset():
    reset_r2_storage()
    yield
    reset_r2_storage()


@pytest.mark.unit
class TestGetR2Storage:
    def test_returns_dummy_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        storage = get_r2_storage()
        assert isinstance(storage, DummyR2Storage)

    def test_returns_real_when_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "ak")
        monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "sk")
        monkeypatch.setattr(
            settings, "R2_ENDPOINT", "https://example.r2.cloudflarestorage.com"
        )
        storage = get_r2_storage()
        assert isinstance(storage, R2Storage)

    def test_singleton_stable_within_mode(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        assert get_r2_storage() is get_r2_storage()

    async def test_dummy_methods_raise(self):
        storage = DummyR2Storage()
        with pytest.raises(RuntimeError, match="R2 is disabled"):
            await storage.object_exists(bucket="b", key="k")
```

**Step 3: Run tests to verify fail**

Run: `uv run pytest tests/unit/test_r2_client.py -v`
Expected: FAIL (module not found)

**Step 4: Implement singleton**

Create `app/core/r2_client.py`:

```python
"""Process-wide R2 storage accessor.

Returns a real R2Storage when R2_ENABLED, or a DummyR2Storage when disabled.
The singleton is rebuilt after reset_r2_storage() — useful in tests.
"""

import aioboto3

from app.config import settings
from app.services.r2_storage import DummyR2Storage, R2Storage

_instance: R2Storage | DummyR2Storage | None = None


def get_r2_storage() -> R2Storage | DummyR2Storage:
    """Return the process-wide R2 storage adapter."""
    global _instance
    if _instance is None:
        if settings.R2_ENABLED:
            session = aioboto3.Session(
                aws_access_key_id=settings.R2_ACCESS_KEY_ID,
                aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
                region_name="auto",
            )
            _instance = R2Storage(session=session, endpoint_url=settings.R2_ENDPOINT)
        else:
            _instance = DummyR2Storage()
    return _instance


def reset_r2_storage() -> None:
    """Reset the singleton. Used by tests and on config reload."""
    global _instance
    _instance = None
```

**Step 5: Run tests**

Run: `uv run pytest tests/unit/test_r2_client.py tests/unit/test_r2_storage.py -v`
Expected: all PASS

**Step 6: Commit**

```bash
git add app/services/r2_storage.py app/core/r2_client.py tests/unit/test_r2_client.py
git commit -m "feat(r2): add DummyR2Storage and singleton accessor"
```

---

### Task 8: Cloudflare Cache Purge Service

**Files:**
- Create: `app/services/cloudflare.py`
- Test: `tests/unit/test_cloudflare_purge.py`

**Step 1: Write failing tests**

Create `tests/unit/test_cloudflare_purge.py`:

```python
"""Tests for Cloudflare cache purge service."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import settings
from app.services.cloudflare import purge_cache_by_urls


@pytest.mark.unit
class TestPurgeCacheByUrls:
    async def test_no_op_when_urls_empty(self):
        """Empty URL list is a no-op (no HTTP calls)."""
        with patch("app.services.cloudflare.httpx.AsyncClient") as client_cls:
            await purge_cache_by_urls([])
            client_cls.assert_not_called()

    async def test_raises_when_credentials_missing(self, monkeypatch):
        """Missing Cloudflare config is a misconfiguration — raise, don't silently no-op."""
        monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "")
        monkeypatch.setattr(settings, "CLOUDFLARE_ZONE_ID", "")
        with pytest.raises(RuntimeError, match="CLOUDFLARE_ZONE_ID"):
            await purge_cache_by_urls(["https://cdn.example.com/x.jpg"])

    async def test_posts_to_cloudflare_api(self, monkeypatch):
        monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "tok")
        monkeypatch.setattr(settings, "CLOUDFLARE_ZONE_ID", "zone")

        mock_client = AsyncMock()
        # httpx.Response is sync — use MagicMock, not AsyncMock, so .text/.json
        # don't get turned into coroutines.
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = ""
        mock_response.json = lambda: {"success": True, "errors": []}
        mock_response.raise_for_status = lambda: None
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False

        with patch("app.services.cloudflare.httpx.AsyncClient", return_value=mock_client):
            await purge_cache_by_urls(["https://cdn.example.com/x.jpg"])

        mock_client.post.assert_awaited_once()
        call = mock_client.post.await_args
        assert "zones/zone/purge_cache" in call.args[0]
        assert call.kwargs["json"] == {"files": ["https://cdn.example.com/x.jpg"]}
        assert call.kwargs["headers"]["Authorization"] == "Bearer tok"

    async def test_batches_urls_in_groups_of_30(self, monkeypatch):
        """Cloudflare free plan accepts max 30 URLs per call — we chunk accordingly."""
        monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "tok")
        monkeypatch.setattr(settings, "CLOUDFLARE_ZONE_ID", "zone")

        mock_client = AsyncMock()
        # httpx.Response is sync — use MagicMock, not AsyncMock, so .text/.json
        # don't get turned into coroutines.
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = ""
        mock_response.json = lambda: {"success": True, "errors": []}
        mock_response.raise_for_status = lambda: None
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False

        urls = [f"https://cdn.example.com/{i}.jpg" for i in range(65)]
        with patch("app.services.cloudflare.httpx.AsyncClient", return_value=mock_client):
            await purge_cache_by_urls(urls)

        # 65 URLs → 30 + 30 + 5 = 3 calls
        assert mock_client.post.await_count == 3
        first_batch = mock_client.post.await_args_list[0].kwargs["json"]["files"]
        last_batch = mock_client.post.await_args_list[2].kwargs["json"]["files"]
        assert len(first_batch) == 30
        assert len(last_batch) == 5

    async def test_logs_and_raises_on_cloudflare_error(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "tok")
        monkeypatch.setattr(settings, "CLOUDFLARE_ZONE_ID", "zone")

        mock_client = AsyncMock()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.text = '{"success": false, "errors": [{"message": "bad"}]}'
        mock_response.json = lambda: {"success": False, "errors": [{"message": "bad"}]}

        # httpx.HTTPStatusError requires a real Request object — passing None
        # TypeErrors at construction time on httpx ≥0.24.
        fake_request = httpx.Request("POST", "https://api.cloudflare.com/")

        def raise_for_status():
            raise httpx.HTTPStatusError("400", request=fake_request, response=mock_response)

        mock_response.raise_for_status = raise_for_status
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False

        with patch("app.services.cloudflare.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                await purge_cache_by_urls(["https://cdn.example.com/x.jpg"])
```

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_cloudflare_purge.py -v`
Expected: FAIL (module not found)

**Step 3: Implement**

Create `app/services/cloudflare.py`:

```python
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
        f"https://api.cloudflare.com/client/v4/zones/"
        f"{settings.CLOUDFLARE_ZONE_ID}/purge_cache"
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
```

**Step 4: Run tests**

Run: `uv run pytest tests/unit/test_cloudflare_purge.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add app/services/cloudflare.py tests/unit/test_cloudflare_purge.py
git commit -m "feat(r2): add Cloudflare cache-purge service"
```

---

## Chunk 3: URL Generation and Serving Endpoint

These two changes are user-visible on their own even before any R2 data is present — with `r2_location=NONE` everywhere and `R2_ENABLED=false` they must be no-ops.

---

### Task 9: Status-Aware URL Generation

**Files:**
- Modify: `app/schemas/image.py`
- Test: `tests/unit/test_image_url_generation.py`

**Step 1: Write failing tests**

Create `tests/unit/test_image_url_generation.py`:

```python
"""Tests for status-aware image URL generation.

Matrix: 7 statuses × 3 r2_location values × 2 R2_ENABLED values. A direct-CDN
URL must be emitted only when R2_ENABLED=true AND status is public AND
r2_location=PUBLIC. Every other combination falls back to the /images/ path.
"""

from datetime import datetime

import pytest

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from app.schemas.image import ImageResponse

ALL_STATUSES = [
    ImageStatus.REVIEW,
    ImageStatus.LOW_QUALITY,
    ImageStatus.INAPPROPRIATE,
    ImageStatus.REPOST,
    ImageStatus.OTHER,
    ImageStatus.ACTIVE,
    ImageStatus.SPOILER,
]
PUBLIC_STATUSES = [ImageStatus.ACTIVE, ImageStatus.SPOILER, ImageStatus.REPOST]


def _make_image(status: int, r2_location: int, medium: int = 0, large: int = 0) -> ImageResponse:
    return ImageResponse(
        image_id=1,
        user_id=1,
        filename="2026-04-17-1",
        ext="jpg",
        status=status,
        r2_location=r2_location,
        date_added=datetime(2026, 4, 17),
        locked=0,
        posts=0,
        favorites=0,
        bayesian_rating=0.0,
        num_ratings=0,
        medium=medium,
        large=large,
    )


@pytest.fixture
def r2_on(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://localhost:3000")


@pytest.fixture
def r2_off(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://localhost:3000")


@pytest.mark.unit
class TestUrlGenerationR2Off:
    """With R2 disabled, everything goes through /images/ — no exceptions."""

    @pytest.mark.parametrize("status", ALL_STATUSES)
    @pytest.mark.parametrize(
        "location", [R2Location.NONE, R2Location.PUBLIC, R2Location.PRIVATE]
    )
    def test_url_fallback(self, r2_off, status, location):
        img = _make_image(status=status, r2_location=location)
        assert img.url == "http://localhost:3000/images/2026-04-17-1.jpg"
        assert img.thumbnail_url == "http://localhost:3000/thumbs/2026-04-17-1.webp"


@pytest.mark.unit
class TestUrlGenerationR2On:
    @pytest.mark.parametrize("status", PUBLIC_STATUSES)
    def test_cdn_direct_for_public_status_public_location(self, r2_on, status):
        img = _make_image(status=status, r2_location=R2Location.PUBLIC)
        assert img.url == "https://cdn.example.com/fullsize/2026-04-17-1.jpg"
        assert img.thumbnail_url == "https://cdn.example.com/thumbs/2026-04-17-1.webp"

    @pytest.mark.parametrize("status", PUBLIC_STATUSES)
    def test_fallback_when_location_none(self, r2_on, status):
        img = _make_image(status=status, r2_location=R2Location.NONE)
        assert img.url.startswith("http://localhost:3000/images/")
        assert img.thumbnail_url.startswith("http://localhost:3000/thumbs/")

    @pytest.mark.parametrize("status", PUBLIC_STATUSES)
    def test_fallback_when_location_private(self, r2_on, status):
        """Public status with PRIVATE location means a transition is in flight."""
        img = _make_image(status=status, r2_location=R2Location.PRIVATE)
        assert img.url.startswith("http://localhost:3000/images/")

    @pytest.mark.parametrize(
        "status",
        [
            ImageStatus.REVIEW,
            ImageStatus.LOW_QUALITY,
            ImageStatus.INAPPROPRIATE,
            ImageStatus.OTHER,
        ],
    )
    @pytest.mark.parametrize(
        "location", [R2Location.NONE, R2Location.PUBLIC, R2Location.PRIVATE]
    )
    def test_protected_never_direct_cdn(self, r2_on, status, location):
        """Protected statuses must never emit a CDN URL, regardless of location."""
        img = _make_image(status=status, r2_location=location)
        assert "cdn.example.com" not in img.url
        assert img.url.startswith("http://localhost:3000/images/")


@pytest.mark.unit
class TestMediumLargeUrls:
    def test_medium_none_returns_none(self, r2_on):
        img = _make_image(
            status=ImageStatus.ACTIVE, r2_location=R2Location.PUBLIC, medium=0, large=0
        )
        assert img.medium_url is None
        assert img.large_url is None

    def test_medium_ready_uses_cdn(self, r2_on):
        img = _make_image(
            status=ImageStatus.ACTIVE, r2_location=R2Location.PUBLIC, medium=1, large=1
        )
        assert img.medium_url == "https://cdn.example.com/medium/2026-04-17-1.jpg"
        assert img.large_url == "https://cdn.example.com/large/2026-04-17-1.jpg"

    def test_medium_ready_fallback_when_none_location(self, r2_on):
        img = _make_image(
            status=ImageStatus.ACTIVE, r2_location=R2Location.NONE, medium=1, large=1
        )
        assert img.medium_url == "http://localhost:3000/medium/2026-04-17-1.jpg"
        assert img.large_url == "http://localhost:3000/large/2026-04-17-1.jpg"
```

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_image_url_generation.py -v`
Expected: FAIL (ImageResponse doesn't yet accept r2_location, no CDN logic)

**Step 3: Update ImageResponse**

In `app/schemas/image.py`:

Add imports near the existing ones:

```python
from app.core.r2_constants import PUBLIC_IMAGE_STATUSES_FOR_R2, R2Location
```

Add `r2_location` field to `ImageResponse` (insert after `large: int` at line 93):

```python
    r2_location: int = R2Location.NONE  # Tri-state: 0=NONE, 1=PUBLIC, 2=PRIVATE
```

Replace the four `@computed_field` URL properties (lines 97-123) with:

```python
    def _should_use_cdn(self) -> bool:
        """True when we can emit a direct-CDN URL for this image.

        All three must hold: R2 enabled, status is publicly-viewable, and the
        canonical object lives in the public bucket. A mismatch (e.g., public
        status but PRIVATE location during a bucket move) falls back to the
        /images/ path, which the endpoint routes based on current r2_location.
        """
        return (
            settings.R2_ENABLED
            and self.status in PUBLIC_IMAGE_STATUSES_FOR_R2
            and self.r2_location == R2Location.PUBLIC
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        """Fullsize URL — direct CDN when eligible, else protected path."""
        if self._should_use_cdn():
            return f"{settings.R2_PUBLIC_CDN_URL}/fullsize/{self.filename}.{self.ext}"
        return f"{settings.IMAGE_BASE_URL}/images/{self.filename}.{self.ext}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def thumbnail_url(self) -> str:
        """Thumbnail URL (always WebP)."""
        if self._should_use_cdn():
            return f"{settings.R2_PUBLIC_CDN_URL}/thumbs/{self.filename}.webp"
        return f"{settings.IMAGE_BASE_URL}/thumbs/{self.filename}.webp"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def medium_url(self) -> str | None:
        """Medium variant (1280px edge) URL, or None if variant is absent."""
        if not self.medium:
            return None
        if self._should_use_cdn():
            return f"{settings.R2_PUBLIC_CDN_URL}/medium/{self.filename}.{self.ext}"
        return f"{settings.IMAGE_BASE_URL}/medium/{self.filename}.{self.ext}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def large_url(self) -> str | None:
        """Large variant (2048px edge) URL, or None if variant is absent."""
        if not self.large:
            return None
        if self._should_use_cdn():
            return f"{settings.R2_PUBLIC_CDN_URL}/large/{self.filename}.{self.ext}"
        return f"{settings.IMAGE_BASE_URL}/large/{self.filename}.{self.ext}"
```

**Step 4: Run tests**

Run: `uv run pytest tests/unit/test_image_url_generation.py -v`
Expected: all PASS

**Step 5: Confirm no existing test regressions**

Run: `uv run pytest -m unit -x --tb=short`
Expected: all pass.

**Step 6: Commit**

```bash
git add app/schemas/image.py tests/unit/test_image_url_generation.py
git commit -m "feat(r2): status-aware URL generation with CDN fallback"
```

---

### Task 10: `/images/*` Endpoint R2 Branches

**Files:**
- Modify: `app/api/v1/media.py`
- Modify (extend): `tests/api/v1/test_media_serving.py` if present, else create it.

**Step 1: Locate existing media test or create one**

Run: `ls tests/api/v1/test_media_serving.py 2>/dev/null && echo exists || echo create`
If it doesn't exist, note the outcome — we'll create it below.

**Step 2: Write failing tests**

Create (or extend) `tests/api/v1/test_media_serving.py`:

```python
"""Tests for /images/* media-serving endpoint R2 branches."""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location


@pytest.mark.api
class TestMediaServingR2:
    async def test_public_bucket_302s_to_cdn(self, client, test_image_public, monkeypatch):
        """status=ACTIVE, r2_location=PUBLIC, R2 on → 302 to CDN URL with no-store."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")

        # test_image_public fixture must create an Images row with
        # status=ACTIVE and r2_location=PUBLIC.
        response = await client.get(
            f"/images/{test_image_public.filename}.{test_image_public.ext}",
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["Location"].startswith("https://cdn.example.com/fullsize/")
        assert response.headers["Cache-Control"] == "no-store"

    async def test_private_bucket_302s_to_presigned(self, client, test_image_private, monkeypatch):
        """Protected status + PRIVATE location → 302 to presigned URL."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)

        with patch(
            "app.api.v1.media.get_r2_storage",
            return_value=AsyncMock(
                generate_presigned_url=AsyncMock(
                    return_value="https://presigned.example.com/foo?sig=xxx"
                )
            ),
        ):
            response = await client.get(
                f"/images/{test_image_private.filename}.{test_image_private.ext}",
                follow_redirects=False,
                # Must provide auth as owner or moderator to view protected.
                headers={"Authorization": f"Bearer {test_image_private.owner_token}"},
            )
        assert response.status_code == 302
        assert response.headers["Location"].startswith("https://presigned.example.com/")
        assert response.headers["Cache-Control"] == "no-store"

    async def test_location_none_falls_back_to_xaccel(self, client, test_image_local, monkeypatch):
        """r2_location=NONE → X-Accel-Redirect to /internal/ (unchanged behaviour)."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)

        response = await client.get(
            f"/images/{test_image_local.filename}.{test_image_local.ext}",
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert response.headers["X-Accel-Redirect"].startswith("/internal/fullsize/")

    async def test_r2_disabled_always_xaccel(self, client, test_image_public, monkeypatch):
        """R2_ENABLED=false → X-Accel-Redirect regardless of r2_location."""
        monkeypatch.setattr(settings, "R2_ENABLED", False)

        response = await client.get(
            f"/images/{test_image_public.filename}.{test_image_public.ext}",
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert response.headers["X-Accel-Redirect"].startswith("/internal/")

    async def test_permission_check_still_runs(self, anon_client, test_image_private, monkeypatch):
        """Anonymous request for a protected image returns 404 before any R2 logic."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        response = await anon_client.get(
            f"/images/{test_image_private.filename}.{test_image_private.ext}",
        )
        assert response.status_code == 404
```

(Fixtures `test_image_public`, `test_image_private`, `test_image_local`, `client`, `anon_client` follow existing project conventions — inspect `tests/conftest.py` to reuse what's there or add minimal new fixtures. If the existing suite uses different fixture names, rename to match.)

**Step 3: Run tests**

Run: `uv run pytest tests/api/v1/test_media_serving.py -v`
Expected: FAIL (endpoint doesn't yet route by r2_location)

**Step 4: Update `_serve_image` in `app/api/v1/media.py`**

Replace the body of `_serve_image` (currently lines 123-165) with:

```python
async def _serve_image(
    filename: str,
    image_type: Literal["fullsize", "thumbs", "medium", "large"],
    db: AsyncSession,
    current_user: Users | None,
) -> Response:
    """Internal handler for serving images.

    Routing:
      - Permission check first (prevents leaking protected-image existence).
      - If R2 enabled and r2_location=PUBLIC → 302 to CDN URL.
      - If R2 enabled and r2_location=PRIVATE → 302 to presigned URL.
      - Otherwise → X-Accel-Redirect to local /internal/ path (legacy fallback).
    """
    image_id = parse_image_id_from_filename(filename)
    if image_id is None:
        raise HTTPException(status_code=404)

    image = await db.get(Images, image_id)
    if image is None:
        raise HTTPException(status_code=404)

    # Permission check — prevents leaking info about protected images
    if not await can_view_image_file(image, current_user, db):
        raise HTTPException(status_code=404)

    # Variant checks (unchanged)
    if image_type in ("medium", "large"):
        variant_status = image.medium if image_type == "medium" else image.large
        if variant_status == VariantStatus.NONE:
            raise HTTPException(status_code=404)
        if variant_status == VariantStatus.PENDING:
            fullsize_path = f"/internal/fullsize/{image.filename}.{image.ext}"
            return Response(
                status_code=200,
                headers={"X-Accel-Redirect": fullsize_path, "Cache-Control": "no-store"},
            )

    ext = "webp" if image_type == "thumbs" else image.ext
    key = f"{image_type}/{image.filename}.{ext}"

    # R2 branches (only active when R2_ENABLED)
    if settings.R2_ENABLED and image.r2_location == R2Location.PUBLIC:
        cdn_url = f"{settings.R2_PUBLIC_CDN_URL}/{key}"
        return Response(
            status_code=302,
            headers={"Location": cdn_url, "Cache-Control": "no-store"},
        )

    if settings.R2_ENABLED and image.r2_location == R2Location.PRIVATE:
        r2 = get_r2_storage()
        presigned = await r2.generate_presigned_url(
            bucket=settings.R2_PRIVATE_BUCKET,
            key=key,
            ttl=settings.R2_PRESIGN_TTL_SECONDS,
        )
        logger.debug(
            "r2_presigned_url_issued", image_id=image_id, variant=image_type
        )
        return Response(
            status_code=302,
            headers={"Location": presigned, "Cache-Control": "no-store"},
        )

    # Local FS fallback (r2_location=NONE, or R2 disabled)
    return Response(
        status_code=200,
        headers={"X-Accel-Redirect": f"/internal/{image_type}/{image.filename}.{ext}"},
    )
```

Add the required imports near the top of the file:

```python
from app.config import settings
from app.core.logging import get_logger
from app.core.r2_client import get_r2_storage
from app.core.r2_constants import R2Location

logger = get_logger(__name__)
```

**Step 5: Run tests**

Run: `uv run pytest tests/api/v1/test_media_serving.py -v`
Expected: all PASS

**Step 6: Run existing API tests for regressions**

Run: `uv run pytest tests/api/v1/ -x --tb=short`
Expected: all pass.

**Step 7: Commit**

```bash
git add app/api/v1/media.py tests/api/v1/test_media_serving.py
git commit -m "feat(r2): route /images/* by r2_location (CDN / presigned / local)"
```

---

## Chunk 4: ARQ Jobs

Three jobs: `r2_finalize_upload_job` (first-sync after upload), `sync_image_status_job` (bucket move on status change), `r2_delete_image_job` (full delete from R2 + CDN purge).

---

### Task 11: `r2_finalize_upload_job`

**Files:**
- Create: `app/tasks/r2_jobs.py`
- Test: `tests/unit/test_r2_finalize_job.py`

**Step 1: Write failing tests**

Create `tests/unit/test_r2_finalize_job.py`:

```python
"""Tests for r2_finalize_upload_job — first-sync of a newly uploaded image."""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from app.models.image import Images, VariantStatus
from app.tasks.r2_jobs import r2_finalize_upload_job


@pytest.fixture
async def fresh_image(db_session):
    img = Images(
        user_id=1,
        filename="2026-04-17-42",
        ext="jpg",
        status=ImageStatus.ACTIVE,
        medium=VariantStatus.NONE,
        large=VariantStatus.NONE,
        r2_location=R2Location.NONE,
    )
    db_session.add(img)
    await db_session.commit()
    await db_session.refresh(img)
    return img


@pytest.mark.unit
class TestR2FinalizeUploadJob:
    async def test_uploads_two_variants_and_flips_public(self, fresh_image, db_session, monkeypatch, tmp_path):
        """Image with no medium/large: uploads fullsize+thumbs to public bucket."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        # Create the local files that the finalizer expects
        (tmp_path / "fullsize").mkdir()
        (tmp_path / "thumbs").mkdir()
        (tmp_path / "fullsize" / "2026-04-17-42.jpg").write_bytes(b"x")
        (tmp_path / "thumbs" / "2026-04-17-42.webp").write_bytes(b"x")

        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_finalize_upload_job({}, image_id=fresh_image.image_id)

        assert mock_r2.upload_file.await_count == 2
        calls = {
            (c.kwargs["bucket"], c.kwargs["key"])
            for c in mock_r2.upload_file.await_args_list
        }
        assert (settings.R2_PUBLIC_BUCKET, "fullsize/2026-04-17-42.jpg") in calls
        assert (settings.R2_PUBLIC_BUCKET, "thumbs/2026-04-17-42.webp") in calls

        # Flip happened
        await db_session.refresh(fresh_image)
        assert fresh_image.r2_location == R2Location.PUBLIC

    async def test_protected_status_goes_to_private_bucket(
        self, fresh_image, db_session, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        (tmp_path / "fullsize").mkdir()
        (tmp_path / "thumbs").mkdir()
        (tmp_path / "fullsize" / "2026-04-17-42.jpg").write_bytes(b"x")
        (tmp_path / "thumbs" / "2026-04-17-42.webp").write_bytes(b"x")

        fresh_image.status = ImageStatus.REVIEW
        await db_session.commit()

        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_finalize_upload_job({}, image_id=fresh_image.image_id)

        for c in mock_r2.upload_file.await_args_list:
            assert c.kwargs["bucket"] == settings.R2_PRIVATE_BUCKET

        await db_session.refresh(fresh_image)
        assert fresh_image.r2_location == R2Location.PRIVATE

    async def test_uploads_medium_and_large_when_ready(
        self, fresh_image, db_session, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        for sub in ("fullsize", "thumbs", "medium", "large"):
            (tmp_path / sub).mkdir()
            suffix = "webp" if sub == "thumbs" else "jpg"
            (tmp_path / sub / f"2026-04-17-42.{suffix}").write_bytes(b"x")

        fresh_image.medium = VariantStatus.READY
        fresh_image.large = VariantStatus.READY
        await db_session.commit()

        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_finalize_upload_job({}, image_id=fresh_image.image_id)

        assert mock_r2.upload_file.await_count == 4

    async def test_retries_when_expected_variant_file_missing(
        self, fresh_image, db_session, monkeypatch, tmp_path
    ):
        """If medium=READY but file is missing on disk, the finalizer must retry."""
        from arq import Retry

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        (tmp_path / "fullsize").mkdir()
        (tmp_path / "thumbs").mkdir()
        (tmp_path / "medium").mkdir()
        (tmp_path / "fullsize" / "2026-04-17-42.jpg").write_bytes(b"x")
        (tmp_path / "thumbs" / "2026-04-17-42.webp").write_bytes(b"x")
        # medium dir exists but file does not

        fresh_image.medium = VariantStatus.READY
        await db_session.commit()

        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            with pytest.raises(Retry):
                await r2_finalize_upload_job(
                    {"job_try": 1}, image_id=fresh_image.image_id
                )

        await db_session.refresh(fresh_image)
        assert fresh_image.r2_location == R2Location.NONE  # no flip on retry

    async def test_no_op_when_r2_disabled(self, fresh_image, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_finalize_upload_job({}, image_id=fresh_image.image_id)
        mock_r2.upload_file.assert_not_awaited()

    async def test_no_op_when_already_synced(self, fresh_image, db_session, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        fresh_image.r2_location = R2Location.PUBLIC
        await db_session.commit()
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_finalize_upload_job({}, image_id=fresh_image.image_id)
        mock_r2.upload_file.assert_not_awaited()
```

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_r2_finalize_job.py -v`
Expected: FAIL (module not found)

**Step 3: Implement**

Create `app/tasks/r2_jobs.py`:

```python
"""ARQ jobs for R2 sync: finalize, status transitions, deletions."""

from pathlib import Path as FilePath
from typing import Any

from arq import Retry
from sqlalchemy import select, update

from app.config import settings
from app.core.database import get_async_session
from app.core.logging import bind_context, get_logger
from app.core.r2_client import get_r2_storage
from app.core.r2_constants import (
    PUBLIC_IMAGE_STATUSES_FOR_R2,
    R2_VARIANTS,
    R2Location,
)
from app.models.image import Images, VariantStatus

logger = get_logger(__name__)


def _bucket_for_status(status: int) -> str:
    """Public bucket for public statuses, private bucket otherwise."""
    return (
        settings.R2_PUBLIC_BUCKET
        if status in PUBLIC_IMAGE_STATUSES_FOR_R2
        else settings.R2_PRIVATE_BUCKET
    )


def _location_for_status(status: int) -> R2Location:
    return (
        R2Location.PUBLIC
        if status in PUBLIC_IMAGE_STATUSES_FOR_R2
        else R2Location.PRIVATE
    )


def _expected_variants(image: Images) -> list[str]:
    """Variants that should exist on disk for this image."""
    variants = ["fullsize", "thumbs"]
    if image.medium == VariantStatus.READY:
        variants.append("medium")
    if image.large == VariantStatus.READY:
        variants.append("large")
    return variants


def _variant_key(image: Images, variant: str) -> str:
    ext = "webp" if variant == "thumbs" else image.ext
    return f"{variant}/{image.filename}.{ext}"


def _local_path(image: Images, variant: str) -> FilePath:
    ext = "webp" if variant == "thumbs" else image.ext
    return FilePath(settings.STORAGE_PATH) / variant / f"{image.filename}.{ext}"


async def r2_finalize_upload_job(
    ctx: dict[str, Any], image_id: int
) -> dict[str, Any]:
    """First-time sync of a newly uploaded image to R2.

    - Reads current image row to pick the right bucket based on status.
    - Verifies all expected variant files exist on disk; retries if any are missing.
    - Uploads every expected variant to the chosen bucket.
    - Atomically flips r2_location to PUBLIC or PRIVATE.

    No-ops when R2 is disabled or the image is already synced.
    """
    bind_context(task="r2_finalize_upload", image_id=image_id)

    if not settings.R2_ENABLED:
        logger.debug("r2_finalize_skipped_disabled", image_id=image_id)
        return {"skipped": "disabled"}

    async with get_async_session() as db:
        result = await db.execute(
            select(Images).where(Images.image_id == image_id)
        )
        image = result.scalar_one_or_none()
        if image is None:
            logger.warning("r2_finalize_image_missing", image_id=image_id)
            return {"skipped": "image_missing"}
        if image.r2_location != R2Location.NONE:
            logger.debug(
                "r2_finalize_skipped_already_synced",
                image_id=image_id,
                r2_location=image.r2_location,
            )
            return {"skipped": "already_synced"}

        variants = _expected_variants(image)
        # Verify all expected files exist — otherwise let the job retry so
        # in-flight variant jobs have time to complete.
        for variant in variants:
            path = _local_path(image, variant)
            if not path.exists():
                logger.info(
                    "r2_finalize_retry_missing_variant",
                    image_id=image_id,
                    variant=variant,
                    path=str(path),
                )
                raise Retry(defer=ctx.get("job_try", 1) * 30)

        bucket = _bucket_for_status(image.status)
        r2 = get_r2_storage()
        for variant in variants:
            key = _variant_key(image, variant)
            path = _local_path(image, variant)
            logger.info(
                "r2_upload_started",
                image_id=image_id,
                bucket=bucket,
                key=key,
            )
            await r2.upload_file(bucket=bucket, key=key, path=path)
            logger.info(
                "r2_upload_succeeded",
                image_id=image_id,
                bucket=bucket,
                key=key,
            )

        # Atomic flip. Re-check r2_location to avoid clobbering a concurrent
        # finalize (arq at-most-once + DB row as lock).
        new_location = _location_for_status(image.status)
        await db.execute(
            update(Images)
            .where(Images.image_id == image_id)
            .where(Images.r2_location == R2Location.NONE)
            .values(r2_location=new_location)
        )
        await db.commit()

    logger.info(
        "r2_finalize_succeeded",
        image_id=image_id,
        r2_location=int(new_location),
    )
    return {"r2_location": int(new_location)}
```

**Step 4: Run tests**

Run: `uv run pytest tests/unit/test_r2_finalize_job.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add app/tasks/r2_jobs.py tests/unit/test_r2_finalize_job.py
git commit -m "feat(r2): add r2_finalize_upload_job for first-sync"
```

---

### Task 12: `sync_image_status_job`

**Files:**
- Modify: `app/tasks/r2_jobs.py`
- Test: `tests/unit/test_r2_status_sync_job.py`

**Step 1: Write failing tests**

Create `tests/unit/test_r2_status_sync_job.py`:

```python
"""Tests for sync_image_status_job — bucket move on status change."""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from app.models.image import Images, VariantStatus
from app.tasks.r2_jobs import sync_image_status_job


@pytest.fixture
async def synced_public_image(db_session):
    img = Images(
        user_id=1,
        filename="2026-04-17-7",
        ext="jpg",
        status=ImageStatus.ACTIVE,
        r2_location=R2Location.PUBLIC,
        medium=VariantStatus.READY,
        large=VariantStatus.NONE,
    )
    db_session.add(img)
    await db_session.commit()
    await db_session.refresh(img)
    return img


@pytest.mark.unit
class TestSyncImageStatusJob:
    async def test_early_return_when_location_none(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        img = Images(
            user_id=1,
            filename="2026-04-17-8",
            ext="jpg",
            status=ImageStatus.REVIEW,
            r2_location=R2Location.NONE,
        )
        db_session.add(img)
        await db_session.commit()
        await db_session.refresh(img)

        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await sync_image_status_job(
                {},
                image_id=img.image_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.REVIEW,
            )
        mock_r2.copy_object.assert_not_awaited()
        mock_r2.delete_object.assert_not_awaited()

    async def test_no_op_when_public_to_public(self, synced_public_image, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await sync_image_status_job(
                {},
                image_id=synced_public_image.image_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.SPOILER,
            )
        mock_r2.copy_object.assert_not_awaited()

    async def test_public_to_protected_copies_deletes_and_purges(
        self, synced_public_image, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")
        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=True)

        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2), patch(
            "app.tasks.r2_jobs.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await sync_image_status_job(
                {},
                image_id=synced_public_image.image_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.REVIEW,
            )

        # Per variant (fullsize, thumbs, medium — large=NONE): copy + delete
        assert mock_r2.copy_object.await_count == 3
        assert mock_r2.delete_object.await_count == 3
        # Flip to PRIVATE
        await db_session.refresh(synced_public_image)
        assert synced_public_image.r2_location == R2Location.PRIVATE
        # Purge called with CDN URLs for the 3 variants
        mock_purge.assert_awaited_once()
        urls = mock_purge.await_args.args[0]
        assert all(u.startswith("https://cdn.example.com/") for u in urls)
        assert len(urls) == 3

    async def test_protected_to_public_copies_deletes_no_purge(
        self, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        img = Images(
            user_id=1,
            filename="2026-04-17-9",
            ext="jpg",
            status=ImageStatus.ACTIVE,
            r2_location=R2Location.PRIVATE,
        )
        db_session.add(img)
        await db_session.commit()
        await db_session.refresh(img)

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=True)
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2), patch(
            "app.tasks.r2_jobs.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await sync_image_status_job(
                {},
                image_id=img.image_id,
                old_status=ImageStatus.REVIEW,
                new_status=ImageStatus.ACTIVE,
            )

        # 2 expected variants (fullsize, thumbs) — medium/large=NONE by default
        assert mock_r2.copy_object.await_count == 2
        assert mock_r2.delete_object.await_count == 2
        # No purge — protected → public doesn't need to invalidate the CDN
        mock_purge.assert_not_awaited()
        await db_session.refresh(img)
        assert img.r2_location == R2Location.PUBLIC

    async def test_skips_missing_source_objects(self, synced_public_image, monkeypatch):
        """Missing source objects don't crash — the variant is skipped."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")
        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=False)

        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2), patch(
            "app.tasks.r2_jobs.purge_cache_by_urls", new_callable=AsyncMock
        ):
            await sync_image_status_job(
                {},
                image_id=synced_public_image.image_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.REVIEW,
            )
        mock_r2.copy_object.assert_not_awaited()
        mock_r2.delete_object.assert_not_awaited()

    async def test_no_op_when_r2_disabled(self, synced_public_image, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await sync_image_status_job(
                {},
                image_id=synced_public_image.image_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.REVIEW,
            )
        mock_r2.copy_object.assert_not_awaited()
```

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_r2_status_sync_job.py -v`
Expected: FAIL

**Step 3: Append job to `app/tasks/r2_jobs.py`**

Add imports at the top of the file:

```python
from app.services.cloudflare import purge_cache_by_urls
```

Append:

```python
def _cdn_urls_for(image: Images, variants: list[str]) -> list[str]:
    """Build the public-CDN URLs for the given variants of an image."""
    urls: list[str] = []
    for variant in variants:
        key = _variant_key(image, variant)
        urls.append(f"{settings.R2_PUBLIC_CDN_URL}/{key}")
    return urls


async def sync_image_status_job(
    ctx: dict[str, Any],
    image_id: int,
    old_status: int,
    new_status: int,
) -> dict[str, Any]:
    """Move an image's R2 objects when its status transitions across
    the public/protected boundary.

    - Early-return if r2_location=NONE (finalizer owns first-sync).
    - Early-return if both old and new statuses are on the same side
      of the public/protected boundary.
    - Otherwise: copy each existing variant to destination bucket, verify,
      delete from source. Atomically flip r2_location. If moving
      public → protected, purge the CDN URLs so nobody can fetch the old
      copy from edge cache.
    """
    bind_context(task="r2_status_sync", image_id=image_id)

    if not settings.R2_ENABLED:
        return {"skipped": "disabled"}

    async with get_async_session() as db:
        result = await db.execute(
            select(Images).where(Images.image_id == image_id)
        )
        image = result.scalar_one_or_none()
        if image is None:
            return {"skipped": "image_missing"}
        if image.r2_location == R2Location.NONE:
            logger.debug(
                "r2_status_sync_skipped_not_finalized",
                image_id=image_id,
            )
            return {"skipped": "not_finalized"}

        old_public = old_status in PUBLIC_IMAGE_STATUSES_FOR_R2
        new_public = new_status in PUBLIC_IMAGE_STATUSES_FOR_R2
        if old_public == new_public:
            return {"skipped": "no_bucket_move"}

        if old_public:
            src_bucket = settings.R2_PUBLIC_BUCKET
            dst_bucket = settings.R2_PRIVATE_BUCKET
            dst_location = R2Location.PRIVATE
        else:
            src_bucket = settings.R2_PRIVATE_BUCKET
            dst_bucket = settings.R2_PUBLIC_BUCKET
            dst_location = R2Location.PUBLIC

        variants = _expected_variants(image)
        r2 = get_r2_storage()

        logger.info(
            "r2_status_transition_started",
            image_id=image_id,
            src=src_bucket,
            dst=dst_bucket,
        )

        moved_variants: list[str] = []
        for variant in variants:
            key = _variant_key(image, variant)
            if not await r2.object_exists(bucket=src_bucket, key=key):
                logger.warning(
                    "r2_status_sync_source_missing",
                    image_id=image_id,
                    bucket=src_bucket,
                    key=key,
                )
                continue
            await r2.copy_object(src_bucket=src_bucket, dst_bucket=dst_bucket, key=key)
            if not await r2.object_exists(bucket=dst_bucket, key=key):
                raise RuntimeError(
                    f"Copy succeeded but {dst_bucket}/{key} does not exist"
                )
            await r2.delete_object(bucket=src_bucket, key=key)
            moved_variants.append(variant)

        # Atomic flip
        await db.execute(
            update(Images)
            .where(Images.image_id == image_id)
            .values(r2_location=dst_location)
        )
        await db.commit()

    # Purge CDN when going public → protected. Do this after the DB flip and
    # outside the DB transaction; a purge failure doesn't roll back the move.
    if old_public and moved_variants:
        try:
            await purge_cache_by_urls(_cdn_urls_for(image, moved_variants))
        except Exception as e:
            logger.error(
                "r2_cdn_purge_failed_post_transition",
                image_id=image_id,
                error=str(e),
            )
            # Don't re-raise — the bucket move already committed.

    logger.info(
        "r2_status_transition_completed",
        image_id=image_id,
        moved_variants=moved_variants,
    )
    return {
        "moved_variants": moved_variants,
        "r2_location": int(dst_location),
    }
```

**Step 4: Run tests**

Run: `uv run pytest tests/unit/test_r2_status_sync_job.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add app/tasks/r2_jobs.py tests/unit/test_r2_status_sync_job.py
git commit -m "feat(r2): add sync_image_status_job for bucket moves"
```

---

### Task 13: `r2_delete_image_job`

**Files:**
- Modify: `app/tasks/r2_jobs.py`
- Test: `tests/unit/test_r2_delete_job.py`

**Step 1: Write failing tests**

Create `tests/unit/test_r2_delete_job.py`:

```python
"""Tests for r2_delete_image_job — full delete from R2 and CDN purge."""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.core.r2_constants import R2Location
from app.tasks.r2_jobs import r2_delete_image_job


@pytest.mark.unit
class TestR2DeleteImageJob:
    async def test_deletes_all_four_variants_from_public_and_purges(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")

        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2), patch(
            "app.tasks.r2_jobs.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await r2_delete_image_job(
                {},
                image_id=42,
                r2_location=int(R2Location.PUBLIC),
                filename="2026-04-17-42",
                ext="jpg",
                variants=["fullsize", "thumbs", "medium", "large"],
            )
        assert mock_r2.delete_object.await_count == 4
        for c in mock_r2.delete_object.await_args_list:
            assert c.kwargs["bucket"] == settings.R2_PUBLIC_BUCKET
        mock_purge.assert_awaited_once()
        assert len(mock_purge.await_args.args[0]) == 4

    async def test_deletes_from_private_no_purge(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2), patch(
            "app.tasks.r2_jobs.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await r2_delete_image_job(
                {},
                image_id=42,
                r2_location=int(R2Location.PRIVATE),
                filename="2026-04-17-42",
                ext="jpg",
                variants=["fullsize", "thumbs"],
            )
        assert mock_r2.delete_object.await_count == 2
        for c in mock_r2.delete_object.await_args_list:
            assert c.kwargs["bucket"] == settings.R2_PRIVATE_BUCKET
        mock_purge.assert_not_awaited()

    async def test_no_op_when_location_none(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_delete_image_job(
                {},
                image_id=42,
                r2_location=int(R2Location.NONE),
                filename="2026-04-17-42",
                ext="jpg",
                variants=["fullsize", "thumbs"],
            )
        mock_r2.delete_object.assert_not_awaited()

    async def test_no_op_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_delete_image_job(
                {},
                image_id=42,
                r2_location=int(R2Location.PUBLIC),
                filename="2026-04-17-42",
                ext="jpg",
                variants=["fullsize", "thumbs"],
            )
        mock_r2.delete_object.assert_not_awaited()
```

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_r2_delete_job.py -v`
Expected: FAIL

**Step 3: Append job**

In `app/tasks/r2_jobs.py`, append:

```python
async def r2_delete_image_job(
    ctx: dict[str, Any],
    image_id: int,
    r2_location: int,
    filename: str,
    ext: str,
    variants: list[str],
) -> dict[str, Any]:
    """Delete an image's R2 objects after hard-deletion of the DB row.

    Arguments are denormalised (filename, ext, variants list) because the DB
    row is already gone by the time this runs. `r2_location` tells us which
    bucket the canonical copy lived in; NONE means nothing to do.
    """
    bind_context(task="r2_delete_image", image_id=image_id)

    if not settings.R2_ENABLED:
        return {"skipped": "disabled"}
    if r2_location == R2Location.NONE:
        return {"skipped": "never_in_r2"}

    bucket = (
        settings.R2_PUBLIC_BUCKET
        if r2_location == R2Location.PUBLIC
        else settings.R2_PRIVATE_BUCKET
    )

    r2 = get_r2_storage()
    keys: list[str] = []
    for variant in variants:
        variant_ext = "webp" if variant == "thumbs" else ext
        keys.append(f"{variant}/{filename}.{variant_ext}")

    for key in keys:
        await r2.delete_object(bucket=bucket, key=key)
        logger.info("r2_object_deleted", image_id=image_id, bucket=bucket, key=key)

    # Purge CDN only for public-bucket deletes (private bucket was never cached).
    if r2_location == R2Location.PUBLIC:
        urls = [f"{settings.R2_PUBLIC_CDN_URL}/{k}" for k in keys]
        try:
            await purge_cache_by_urls(urls)
        except Exception as e:
            logger.error(
                "r2_cdn_purge_failed_post_delete",
                image_id=image_id,
                error=str(e),
            )

    return {"deleted_keys": keys}
```

**Step 4: Run tests**

Run: `uv run pytest tests/unit/test_r2_delete_job.py -v`
Expected: all PASS

**Step 5: Register all three jobs in the worker**

In `app/tasks/worker.py`, add import (near existing image_jobs import):

```python
from app.tasks.r2_jobs import (
    r2_delete_image_job,
    r2_finalize_upload_job,
    sync_image_status_job,
)
```

Add to `WorkerSettings.functions` (list currently starting at line 117):

```python
        func(r2_finalize_upload_job, max_tries=5),
        func(sync_image_status_job, max_tries=settings.ARQ_MAX_TRIES),
        func(r2_delete_image_job, max_tries=settings.ARQ_MAX_TRIES),
```

**Step 6: Run full unit suite**

Run: `uv run pytest -m unit -x --tb=short`
Expected: all pass.

**Step 7: Commit**

```bash
git add app/tasks/r2_jobs.py app/tasks/worker.py tests/unit/test_r2_delete_job.py
git commit -m "feat(r2): add r2_delete_image_job and register all R2 jobs"
```

---

## Chunk 5: Route Integration

Wire the three jobs into the upload, status-change, and delete routes. Each integration must be a no-op when `R2_ENABLED=false` (no side-effects in dev).

---

### Task 14: Enqueue `r2_finalize_upload_job` After Upload

**Files:**
- Modify: `app/api/v1/images.py` (upload handler)
- Test: `tests/api/v1/test_image_upload_r2.py`

**Step 1: Locate upload handler**

Run: `uv run grep -n "create_thumbnail_job\|enqueue_job" app/api/v1/images.py | head -20`
Expected: shows the block where thumbnail/variant jobs are enqueued.

**Step 2: Write failing tests**

Create `tests/api/v1/test_image_upload_r2.py`:

```python
"""Tests for R2 finalize job enqueued after upload."""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings


@pytest.mark.api
class TestUploadEnqueuesR2Finalize:
    async def test_enqueues_finalize_when_r2_enabled(
        self, client, auth_headers, sample_image_bytes, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        with patch(
            "app.api.v1.images.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            response = await client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", sample_image_bytes, "image/jpeg")},
                headers=auth_headers,
            )
        assert response.status_code in (200, 201)
        finalize_calls = [
            c for c in mock_enqueue.await_args_list
            if c.args[0] == "r2_finalize_upload_job"
        ]
        assert len(finalize_calls) == 1
        # Deferred long enough for variant jobs to complete first
        assert finalize_calls[0].kwargs.get("_defer_by", 0) >= 60

    async def test_no_finalize_when_r2_disabled(
        self, client, auth_headers, sample_image_bytes, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        with patch(
            "app.api.v1.images.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            await client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", sample_image_bytes, "image/jpeg")},
                headers=auth_headers,
            )
        finalize_calls = [
            c for c in mock_enqueue.await_args_list
            if c.args[0] == "r2_finalize_upload_job"
        ]
        assert finalize_calls == []
```

(Reuse existing project fixtures for `client`, `auth_headers`, `sample_image_bytes`. If they don't exist with those names, adapt to the ones in `tests/conftest.py`.)

**Step 3: Run tests**

Run: `uv run pytest tests/api/v1/test_image_upload_r2.py -v`
Expected: FAIL (finalize not enqueued)

**Step 4: Add enqueue call to upload handler**

In `app/api/v1/images.py`, find the block where `create_thumbnail_job` / variant jobs are enqueued (near the end of the upload route). Directly after those enqueues, add:

```python
    # Enqueue R2 first-sync. Deferred so variant jobs (thumbnail, medium, large)
    # have time to finish; the finalizer retries if their files aren't on disk
    # yet. No-op when R2_ENABLED=false (the job returns early).
    if settings.R2_ENABLED:
        await enqueue_job(
            "r2_finalize_upload_job",
            image_id=new_image.image_id,
            _defer_by=90,
        )
```

If `enqueue_job` isn't already imported in `images.py`, add:

```python
from app.tasks.queue import enqueue_job
```

**Step 5: Run tests**

Run: `uv run pytest tests/api/v1/test_image_upload_r2.py -v`
Expected: PASS

**Step 6: Run broader suite**

Run: `uv run pytest tests/api/v1/ -x --tb=short`
Expected: pass.

**Step 7: Commit**

```bash
git add app/api/v1/images.py tests/api/v1/test_image_upload_r2.py
git commit -m "feat(r2): enqueue finalize job after successful upload"
```

---

### Task 15: Enqueue `sync_image_status_job` on Status Change

**Files:**
- Modify: `app/api/v1/images.py` (PATCH/status-change handler around line 943-946)
- Test: `tests/api/v1/test_image_status_r2.py`

**Step 1: Write failing tests**

Create `tests/api/v1/test_image_status_r2.py`:

```python
"""Tests for sync_image_status_job enqueued on status change."""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import ImageStatus, settings


@pytest.mark.api
class TestStatusChangeEnqueuesSync:
    async def test_enqueues_sync_when_status_changes_and_r2_enabled(
        self, client, admin_headers, test_image_active, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        with patch(
            "app.api.v1.images.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            response = await client.patch(
                f"/api/v1/images/{test_image_active.image_id}",
                json={"status": ImageStatus.REVIEW},
                headers=admin_headers,
            )
        assert response.status_code == 200
        sync_calls = [
            c for c in mock_enqueue.await_args_list
            if c.args[0] == "sync_image_status_job"
        ]
        assert len(sync_calls) == 1
        assert sync_calls[0].kwargs["image_id"] == test_image_active.image_id
        assert sync_calls[0].kwargs["old_status"] == ImageStatus.ACTIVE
        assert sync_calls[0].kwargs["new_status"] == ImageStatus.REVIEW

    async def test_no_enqueue_when_status_unchanged(
        self, client, admin_headers, test_image_active, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        with patch(
            "app.api.v1.images.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            await client.patch(
                f"/api/v1/images/{test_image_active.image_id}",
                json={"caption": "x"},
                headers=admin_headers,
            )
        sync_calls = [
            c for c in mock_enqueue.await_args_list
            if c.args[0] == "sync_image_status_job"
        ]
        assert sync_calls == []

    async def test_no_enqueue_when_r2_disabled(
        self, client, admin_headers, test_image_active, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        with patch(
            "app.api.v1.images.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            await client.patch(
                f"/api/v1/images/{test_image_active.image_id}",
                json={"status": ImageStatus.REVIEW},
                headers=admin_headers,
            )
        sync_calls = [
            c for c in mock_enqueue.await_args_list
            if c.args[0] == "sync_image_status_job"
        ]
        assert sync_calls == []
```

**Step 2: Run tests**

Run: `uv run pytest tests/api/v1/test_image_status_r2.py -v`
Expected: FAIL

**Step 3: Add enqueue in the status-change block**

In `app/api/v1/images.py`, find the status-change block starting around line 943 (`previous_status = image.status`). After the existing `db.add(history)` / `await db.commit()` block that persists the change, add:

```python
    # Enqueue R2 bucket-move if the status actually changed. The job itself
    # early-returns when r2_location=NONE or old/new statuses are on the same
    # side of the public/protected boundary, so this is safe to always call.
    if new_status is not None and new_status != previous_status and settings.R2_ENABLED:
        await enqueue_job(
            "sync_image_status_job",
            image_id=image_id,
            old_status=previous_status,
            new_status=new_status,
        )
```

Make sure `enqueue_job` import is present (Task 14 may already have added it).

**Step 4: Run tests**

Run: `uv run pytest tests/api/v1/test_image_status_r2.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/v1/images.py tests/api/v1/test_image_status_r2.py
git commit -m "feat(r2): enqueue sync_image_status_job on status change"
```

---

### Task 16: Enqueue `r2_delete_image_job` on Hard Delete

**Files:**
- Modify: `app/api/v1/images.py` (delete handler, starting line 731)
- Test: `tests/api/v1/test_image_delete_r2.py`

**Step 1: Write failing tests**

Create `tests/api/v1/test_image_delete_r2.py`:

```python
"""Tests for r2_delete_image_job enqueued on hard delete."""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.core.r2_constants import R2Location


@pytest.mark.api
class TestDeleteEnqueuesR2:
    async def test_enqueues_delete_with_prior_location(
        self, client, admin_headers, test_image_public_r2, monkeypatch
    ):
        """Image had r2_location=PUBLIC; delete job receives that value."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)

        with patch(
            "app.api.v1.images.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            response = await client.delete(
                f"/api/v1/images/{test_image_public_r2.image_id}?reason=test",
                headers=admin_headers,
            )
        assert response.status_code == 204
        delete_calls = [
            c for c in mock_enqueue.await_args_list
            if c.args[0] == "r2_delete_image_job"
        ]
        assert len(delete_calls) == 1
        assert delete_calls[0].kwargs["r2_location"] == int(R2Location.PUBLIC)
        assert delete_calls[0].kwargs["filename"] == test_image_public_r2.filename
        assert "fullsize" in delete_calls[0].kwargs["variants"]
        assert "thumbs" in delete_calls[0].kwargs["variants"]

    async def test_no_enqueue_when_r2_disabled(
        self, client, admin_headers, test_image_public_r2, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        with patch(
            "app.api.v1.images.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            await client.delete(
                f"/api/v1/images/{test_image_public_r2.image_id}?reason=test",
                headers=admin_headers,
            )
        delete_calls = [
            c for c in mock_enqueue.await_args_list
            if c.args[0] == "r2_delete_image_job"
        ]
        assert delete_calls == []
```

**Step 2: Run tests**

Run: `uv run pytest tests/api/v1/test_image_delete_r2.py -v`
Expected: FAIL

**Step 3: Update delete handler**

In `app/api/v1/images.py` delete handler (starting line 731), after `image = result.scalar_one_or_none()` has confirmed the image, capture metadata for the R2 job before the DB delete. After the existing local-file deletion loop and `logger.info("image_deleted", ...)`, add:

```python
    # Enqueue R2 cleanup. We capture r2_location/filename/ext/variants before
    # the DB row was deleted (see above). No-op when R2_ENABLED=false.
    if settings.R2_ENABLED:
        await enqueue_job(
            "r2_delete_image_job",
            image_id=image_id,
            r2_location=prior_r2_location,
            filename=prior_filename,
            ext=prior_ext,
            variants=prior_variants,
        )
```

Add the capture earlier — right after `image = result.scalar_one_or_none()` confirms existence, and before any delete happens:

```python
    prior_r2_location = image.r2_location
    prior_filename = image.filename
    prior_ext = image.ext
    prior_variants = ["fullsize", "thumbs"]
    if image.medium == VariantStatus.READY:
        prior_variants.append("medium")
    if image.large == VariantStatus.READY:
        prior_variants.append("large")
```

Add the import for `VariantStatus` at the top of `images.py` if not already present:

```python
from app.models.image import Images, VariantStatus
```

**Step 4: Run tests**

Run: `uv run pytest tests/api/v1/test_image_delete_r2.py -v`
Expected: PASS

**Step 5: Run the full API suite**

Run: `uv run pytest tests/api/v1/ -x --tb=short`
Expected: pass.

**Step 6: Commit**

```bash
git add app/api/v1/images.py tests/api/v1/test_image_delete_r2.py
git commit -m "feat(r2): enqueue r2_delete_image_job on hard delete"
```

---

## Chunk 6: Operational Tooling (`scripts/r2_sync.py`)

A CLI with subcommands shared by the initial cutover and ongoing ops. Each subcommand is its own task; they all share a common module for bucket-picking logic and arg parsing.

---

### Task 17: CLI Skeleton with `R2_ENABLED` and `R2_ALLOW_BULK_BACKFILL` Guards

**Files:**
- Create: `scripts/r2_sync.py`
- Test: `tests/unit/test_r2_sync_cli_guards.py`

**Step 1: Write failing tests**

Create `tests/unit/test_r2_sync_cli_guards.py`:

```python
"""Tests for r2_sync.py CLI guards (R2_ENABLED, R2_ALLOW_BULK_BACKFILL)."""

import pytest

from app.config import settings
from scripts.r2_sync import (
    BulkBackfillDisallowedError,
    R2DisabledError,
    require_bulk_backfill,
    require_r2_enabled,
)


@pytest.mark.unit
class TestRequireR2Enabled:
    def test_passes_when_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        require_r2_enabled()

    def test_raises_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        with pytest.raises(R2DisabledError):
            require_r2_enabled()


@pytest.mark.unit
class TestRequireBulkBackfill:
    def test_passes_when_allowed(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", True)
        require_bulk_backfill()

    def test_raises_when_disallowed(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", False)
        with pytest.raises(BulkBackfillDisallowedError):
            require_bulk_backfill()
```

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_r2_sync_cli_guards.py -v`
Expected: FAIL (module not found)

**Step 3: Implement skeleton**

Create `scripts/r2_sync.py`:

```python
"""R2 operational tooling.

Subcommands:
    split-existing       — one-time move protected images from public bucket to private
    backfill-locations   — one-shot flip r2_location for existing rows (gated)
    reconcile            — heal: upload missing R2 objects from local FS (gated)
    image                — inspect/re-sync a single image
    verify               — audit R2 vs DB state (read-only)
    purge-cache          — manually purge CDN for one image
    health               — report unsynced counts and storage usage (read-only)

Guarded by R2_ENABLED=true (all commands). backfill-locations and reconcile
additionally require R2_ALLOW_BULK_BACKFILL=true to prevent staging from
mass-uploading prod-imported images to its small staging bucket.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class R2SyncError(Exception):
    """Base for r2_sync CLI errors."""


class R2DisabledError(R2SyncError):
    """Raised when R2_ENABLED=false."""


class BulkBackfillDisallowedError(R2SyncError):
    """Raised when R2_ALLOW_BULK_BACKFILL=false."""


def require_r2_enabled() -> None:
    if not settings.R2_ENABLED:
        raise R2DisabledError(
            "R2_ENABLED=false. Enable R2 in config before running r2_sync commands."
        )


def require_bulk_backfill() -> None:
    """Require both R2 enabled AND bulk backfill explicitly allowed."""
    require_r2_enabled()
    if not settings.R2_ALLOW_BULK_BACKFILL:
        raise BulkBackfillDisallowedError(
            "R2_ALLOW_BULK_BACKFILL=false. This command walks the DB for "
            "unsynced rows and uploads local files to R2; on staging this "
            "would mass-upload the prod dataset. Set the flag true only in "
            "prod's steady-state config."
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="R2 operational tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    # Subcommands — each task below fills in its handler.
    # --dry-run is only on split-existing for now; other bulk commands could
    # add one later but the underlying function must actually honor it.
    se = sub.add_parser("split-existing")
    se.add_argument("--dry-run", action="store_true")
    sub.add_parser("backfill-locations")
    rec = sub.add_parser("reconcile")
    rec.add_argument("--stale-after", type=int, default=600)
    img = sub.add_parser("image")
    img.add_argument("image_id", type=int)
    ver = sub.add_parser("verify")
    ver.add_argument("--sample", type=int, default=None)
    pc = sub.add_parser("purge-cache")
    pc.add_argument("image_id", type=int)
    h = sub.add_parser("health")
    h.add_argument("--json", action="store_true")

    return parser


async def _dispatch(args: argparse.Namespace) -> int:
    require_r2_enabled()
    raise NotImplementedError(
        f"Subcommand '{args.command}' is not yet implemented in this chunk."
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except R2SyncError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

**Step 4: Run tests**

Run: `uv run pytest tests/unit/test_r2_sync_cli_guards.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/r2_sync.py tests/unit/test_r2_sync_cli_guards.py
git commit -m "feat(r2): scripts/r2_sync.py skeleton with guards"
```

---

### Task 18: `split-existing` Subcommand

**Files:**
- Modify: `scripts/r2_sync.py`
- Test: `tests/integration/test_r2_sync_split.py`

**Step 1: Write failing integration test**

Create `tests/integration/test_r2_sync_split.py`:

```python
"""Integration test: split-existing moves protected images to private bucket."""

import aioboto3
import pytest
from moto import mock_aws

from app.config import ImageStatus, settings
from app.core.r2_client import reset_r2_storage
from app.models.image import Images
from scripts.r2_sync import split_existing


@pytest.fixture
def moto_session():
    """Yield an aioboto3 session inside a moto mock_aws context.

    moto.mock_aws() patches botocore (which aioboto3 uses) at a layer below
    the Session class, so every aioboto3 Session created while this context
    is active — including the one get_r2_storage() builds internally — routes
    to moto's shared in-memory S3 backend.
    """
    with mock_aws():
        yield aioboto3.Session(
            aws_access_key_id="t",
            aws_secret_access_key="t",
            region_name="us-east-1",
        )


@pytest.mark.integration
class TestSplitExisting:
    async def test_moves_protected_images_only(self, moto_session, db_session, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "t")
        monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "t")
        monkeypatch.setattr(settings, "R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
        monkeypatch.setattr(settings, "R2_PUBLIC_BUCKET", "public")
        monkeypatch.setattr(settings, "R2_PRIVATE_BUCKET", "private")
        # Reset singleton so get_r2_storage() picks up the new settings.
        reset_r2_storage()

        async with moto_session.client("s3") as s3:
            await s3.create_bucket(Bucket="public")
            await s3.create_bucket(Bucket="private")
            # Seed objects — one public image, one protected image, one repost.
            for img in [
                ("2026-04-17-1", ImageStatus.ACTIVE),     # stays public
                ("2026-04-17-2", ImageStatus.REVIEW),     # moves
                ("2026-04-17-3", ImageStatus.INAPPROPRIATE),  # moves
                ("2026-04-17-4", ImageStatus.REPOST),     # stays public
            ]:
                for variant, ext in [("fullsize", "jpg"), ("thumbs", "webp")]:
                    await s3.put_object(
                        Bucket="public", Key=f"{variant}/{img[0]}.{ext}", Body=b"x"
                    )

        # Corresponding DB rows
        for i, (fn, st) in enumerate(
            [
                ("2026-04-17-1", ImageStatus.ACTIVE),
                ("2026-04-17-2", ImageStatus.REVIEW),
                ("2026-04-17-3", ImageStatus.INAPPROPRIATE),
                ("2026-04-17-4", ImageStatus.REPOST),
            ],
            start=1,
        ):
            db_session.add(Images(image_id=i, user_id=1, filename=fn, ext="jpg", status=st))
        await db_session.commit()

        # Run split — moto intercepts all S3 calls made by get_r2_storage()
        # while the moto_session fixture's mock_aws() context is still open.
        await split_existing(dry_run=False)

        async with moto_session.client("s3") as s3:
            # Public bucket still has actives + repost
            assert "Contents" in await s3.list_objects_v2(Bucket="public")
            public_keys = {
                o["Key"] for o in (await s3.list_objects_v2(Bucket="public"))["Contents"]
            }
            assert "fullsize/2026-04-17-1.jpg" in public_keys
            assert "fullsize/2026-04-17-4.jpg" in public_keys
            assert "fullsize/2026-04-17-2.jpg" not in public_keys
            # Private has the moved items
            private_keys = {
                o["Key"] for o in (await s3.list_objects_v2(Bucket="private"))["Contents"]
            }
            assert "fullsize/2026-04-17-2.jpg" in private_keys
            assert "fullsize/2026-04-17-3.jpg" in private_keys
```

**Step 2: Run test**

Run: `uv run pytest tests/integration/test_r2_sync_split.py -v`
Expected: FAIL (function not exported)

**Step 3: Implement subcommand**

In `scripts/r2_sync.py`, replace the `_dispatch` stub and add the implementation. Add imports near the top:

```python
from sqlalchemy import select

from app.core.database import get_async_session
from app.core.r2_client import get_r2_storage
from app.core.r2_constants import (
    PUBLIC_IMAGE_STATUSES_FOR_R2,
    R2_VARIANTS,
    R2Location,
)
from app.models.image import Images, VariantStatus
from app.services.cloudflare import purge_cache_by_urls
```

Replace the `_dispatch` function with:

```python
async def _dispatch(args: argparse.Namespace) -> int:
    require_r2_enabled()

    if args.command == "split-existing":
        await split_existing(dry_run=args.dry_run)
        return 0
    raise NotImplementedError(
        f"Subcommand '{args.command}' is not yet implemented."
    )
```

Add the `split_existing` function:

```python
async def split_existing(*, dry_run: bool) -> None:
    """Move protected-status images' R2 objects from public → private bucket.

    Assumes existing R2 state is "everything in R2_PUBLIC_BUCKET" (the starting
    point for the production cutover). Idempotent — objects already moved are
    skipped via object_exists checks.
    """
    r2 = get_r2_storage()

    async with get_async_session() as db:
        result = await db.execute(
            select(Images).where(Images.status.notin_(PUBLIC_IMAGE_STATUSES_FOR_R2))
        )
        rows = list(result.scalars())

    logger.info("split_existing_started", count=len(rows), dry_run=dry_run)

    moved = 0
    for image in rows:
        variants = ["fullsize", "thumbs"]
        if image.medium == VariantStatus.READY:
            variants.append("medium")
        if image.large == VariantStatus.READY:
            variants.append("large")
        for variant in variants:
            ext = "webp" if variant == "thumbs" else image.ext
            key = f"{variant}/{image.filename}.{ext}"
            if not await r2.object_exists(bucket=settings.R2_PUBLIC_BUCKET, key=key):
                continue
            if dry_run:
                print(f"DRY_RUN move {settings.R2_PUBLIC_BUCKET}/{key} → {settings.R2_PRIVATE_BUCKET}/{key}")
                moved += 1
                continue
            await r2.copy_object(
                src_bucket=settings.R2_PUBLIC_BUCKET,
                dst_bucket=settings.R2_PRIVATE_BUCKET,
                key=key,
            )
            await r2.delete_object(bucket=settings.R2_PUBLIC_BUCKET, key=key)
            moved += 1

    logger.info("split_existing_completed", moved=moved, dry_run=dry_run)
    print(f"{'[dry-run] ' if dry_run else ''}moved {moved} objects")
```

**Step 4: Run test**

Run: `uv run pytest tests/integration/test_r2_sync_split.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/r2_sync.py tests/integration/test_r2_sync_split.py
git commit -m "feat(r2): add r2_sync.py split-existing subcommand"
```

---

### Task 19: `backfill-locations` Subcommand

**Files:**
- Modify: `scripts/r2_sync.py`
- Test: `tests/integration/test_r2_sync_backfill.py`

**Step 1: Write failing test**

Create `tests/integration/test_r2_sync_backfill.py`:

```python
"""Integration test: backfill-locations fills r2_location based on current status."""

import pytest

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from app.models.image import Images
from scripts.r2_sync import backfill_locations


@pytest.mark.integration
class TestBackfillLocations:
    async def test_respects_r2_allow_bulk_backfill_flag(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", False)
        from scripts.r2_sync import BulkBackfillDisallowedError

        with pytest.raises(BulkBackfillDisallowedError):
            await backfill_locations()

    async def test_flips_public_and_private(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", True)

        db_session.add(Images(user_id=1, filename="a", ext="jpg", status=ImageStatus.ACTIVE))
        db_session.add(Images(user_id=1, filename="b", ext="jpg", status=ImageStatus.REVIEW))
        db_session.add(Images(user_id=1, filename="c", ext="jpg", status=ImageStatus.REPOST))
        await db_session.commit()

        await backfill_locations(batch_size=2)

        from sqlalchemy import select
        result = await db_session.execute(select(Images))
        images = {img.filename: img for img in result.scalars()}
        assert images["a"].r2_location == R2Location.PUBLIC
        assert images["b"].r2_location == R2Location.PRIVATE
        assert images["c"].r2_location == R2Location.PUBLIC
```

**Step 2: Run test**

Run: `uv run pytest tests/integration/test_r2_sync_backfill.py -v`
Expected: FAIL

**Step 3: Implement**

Append to `scripts/r2_sync.py`:

```python
async def backfill_locations(*, batch_size: int = 1000) -> None:
    """Flip r2_location for rows still at NONE based on current status.

    Query: WHERE r2_location = 0 (NONE). Chunked with a fresh read per batch
    so rows updated by the finalizer mid-run (r2_location != 0) are skipped.
    """
    require_bulk_backfill()

    from sqlalchemy import update

    total_flipped = 0
    while True:
        async with get_async_session() as db:
            result = await db.execute(
                select(Images)
                .where(Images.r2_location == R2Location.NONE)
                .limit(batch_size)
            )
            rows = list(result.scalars())
            if not rows:
                break

            public_ids = [
                img.image_id for img in rows
                if img.status in PUBLIC_IMAGE_STATUSES_FOR_R2
            ]
            private_ids = [
                img.image_id for img in rows
                if img.status not in PUBLIC_IMAGE_STATUSES_FOR_R2
            ]

            if public_ids:
                await db.execute(
                    update(Images)
                    .where(Images.image_id.in_(public_ids))
                    .where(Images.r2_location == R2Location.NONE)
                    .values(r2_location=R2Location.PUBLIC)
                )
            if private_ids:
                await db.execute(
                    update(Images)
                    .where(Images.image_id.in_(private_ids))
                    .where(Images.r2_location == R2Location.NONE)
                    .values(r2_location=R2Location.PRIVATE)
                )

            await db.commit()
            total_flipped += len(public_ids) + len(private_ids)
            logger.info(
                "backfill_batch",
                batch_size=len(rows),
                total_flipped=total_flipped,
            )

    print(f"backfilled {total_flipped} rows")
```

Add dispatch:

```python
    if args.command == "backfill-locations":
        await backfill_locations()
        return 0
```

**Step 4: Run test**

Run: `uv run pytest tests/integration/test_r2_sync_backfill.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/r2_sync.py tests/integration/test_r2_sync_backfill.py
git commit -m "feat(r2): add r2_sync.py backfill-locations subcommand"
```

---

### Task 20: `reconcile`, `image`, `verify`, `purge-cache`, `health` Subcommands

These are a related cluster. Each is a thin wrapper around existing building blocks.

**Files:**
- Modify: `scripts/r2_sync.py`
- Test: `tests/unit/test_r2_sync_remaining.py`

**Step 1: Write tests**

Create `tests/unit/test_r2_sync_remaining.py`:

```python
"""Tests for the remaining r2_sync.py subcommands."""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from scripts.r2_sync import (
    BulkBackfillDisallowedError,
    health,
    purge_cache_command,
    reconcile,
    resync_image,
    verify,
)


@pytest.mark.unit
class TestReconcileGuard:
    async def test_requires_bulk_backfill_flag(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", False)
        with pytest.raises(BulkBackfillDisallowedError):
            await reconcile(stale_after=60)


@pytest.mark.unit
class TestHealth:
    async def test_reports_unsynced_count_and_oldest_age(self, db_session, monkeypatch, tmp_path):
        from app.models.image import Images
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        # tmp_path is isolated per-test — avoids du-ing all of /tmp (slow/flaky)
        # and avoids permission issues on shared CI runners.
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        db_session.add(Images(user_id=1, filename="a", ext="jpg", status=ImageStatus.ACTIVE, r2_location=R2Location.NONE))
        db_session.add(Images(user_id=1, filename="b", ext="jpg", status=ImageStatus.ACTIVE, r2_location=R2Location.PUBLIC))
        await db_session.commit()

        result = await health(output_json=True)
        assert result["unsynced_count"] == 1
        assert result["local_storage_path"] == str(tmp_path)


@pytest.mark.unit
class TestPurgeCacheCommand:
    async def test_calls_cloudflare_with_all_variant_urls(self, db_session, monkeypatch):
        from app.models.image import Images, VariantStatus

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")
        db_session.add(
            Images(
                image_id=42,
                user_id=1,
                filename="2026-04-17-42",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                medium=VariantStatus.READY,
                large=VariantStatus.READY,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        with patch(
            "scripts.r2_sync.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await purge_cache_command(image_id=42)
        mock_purge.assert_awaited_once()
        urls = mock_purge.await_args.args[0]
        assert len(urls) == 4


@pytest.mark.unit
class TestResyncImage:
    async def test_prints_state_for_known_image(self, db_session, monkeypatch, capsys):
        from app.models.image import Images
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_BUCKET", "public")
        db_session.add(
            Images(image_id=99, user_id=1, filename="a", ext="jpg",
                   status=ImageStatus.ACTIVE, r2_location=R2Location.PUBLIC)
        )
        await db_session.commit()

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=True)
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            await resync_image(99)

        out = capsys.readouterr().out
        assert "image 99" in out
        assert "fullsize" in out and "thumbs" in out

    async def test_prints_not_found_for_missing_image(self, db_session, monkeypatch, capsys):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        await resync_image(99999999)
        assert "not found" in capsys.readouterr().out


@pytest.mark.unit
class TestVerify:
    """verify must implement the spec's full discrepancy rules."""

    async def test_none_with_no_object_is_clean(self, db_session, monkeypatch):
        """NONE + no object is a legitimate state (spec §Operational tooling)."""
        from app.models.image import Images
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        db_session.add(
            Images(user_id=1, filename="x", ext="jpg", status=ImageStatus.ACTIVE, r2_location=R2Location.NONE)
        )
        await db_session.commit()

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=False)
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            report = await verify(sample=None)
        assert report["discrepancies"] == []

    async def test_none_with_unexpected_object_reports_unexpected(self, db_session, monkeypatch):
        """NONE row + object present in either bucket → leaked upload, must report."""
        from app.models.image import Images
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        db_session.add(
            Images(image_id=10, user_id=1, filename="orphan", ext="jpg",
                   status=ImageStatus.ACTIVE, r2_location=R2Location.NONE)
        )
        await db_session.commit()

        mock_r2 = AsyncMock()
        # Object exists in public bucket despite DB saying NONE.
        mock_r2.object_exists = AsyncMock(
            side_effect=lambda bucket, key: bucket == settings.R2_PUBLIC_BUCKET
        )
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            report = await verify(sample=None)
        kinds = {d["kind"] for d in report["discrepancies"]}
        assert "unexpected" in kinds

    async def test_cross_bucket_orphan_reports_wrong_bucket(self, db_session, monkeypatch):
        """PUBLIC row with copy also in private bucket → incomplete move, must report."""
        from app.models.image import Images
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        db_session.add(
            Images(image_id=11, user_id=1, filename="moved", ext="jpg",
                   status=ImageStatus.ACTIVE, r2_location=R2Location.PUBLIC)
        )
        await db_session.commit()

        mock_r2 = AsyncMock()
        # Exists in BOTH buckets — the expected check passes, but a cross-bucket
        # copy is still an error (probably a failed delete mid-move).
        mock_r2.object_exists = AsyncMock(return_value=True)
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            report = await verify(sample=None)
        kinds = {d["kind"] for d in report["discrepancies"]}
        assert "wrong_bucket" in kinds

    async def test_missing_from_expected_bucket_reports_missing(self, db_session, monkeypatch):
        """PUBLIC row with object missing from public bucket → report missing."""
        from app.models.image import Images
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        db_session.add(
            Images(image_id=12, user_id=1, filename="gone", ext="jpg",
                   status=ImageStatus.ACTIVE, r2_location=R2Location.PUBLIC)
        )
        await db_session.commit()

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=False)
        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            report = await verify(sample=None)
        kinds = {d["kind"] for d in report["discrepancies"]}
        assert "missing" in kinds
```

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_r2_sync_remaining.py -v`
Expected: FAIL

**Step 3: Implement subcommands**

Append to `scripts/r2_sync.py`:

```python
async def reconcile(*, stale_after: int) -> None:
    """Heal: upload missing R2 objects for unsynced rows older than `stale_after`.

    Idempotent: uploaded variants are re-detected via object_exists on the next
    pass, so a partial run that uploads some variants before failing on a
    missing local file will simply skip those keys next time. Orphaned partial
    uploads (if the row is later deleted before reconcile finishes) are the
    responsibility of a future orphan-gc command — out of scope here.
    """
    require_bulk_backfill()

    from datetime import UTC, datetime, timedelta
    from pathlib import Path as FilePath
    from sqlalchemy import update

    # DB columns are naive UTC; strip tz for the subtraction.
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=stale_after)
    r2 = get_r2_storage()

    async with get_async_session() as db:
        result = await db.execute(
            select(Images)
            .where(Images.r2_location == R2Location.NONE)
            .where(Images.date_added < cutoff)
        )
        rows = list(result.scalars())

    healed = 0
    for image in rows:
        variants = ["fullsize", "thumbs"]
        if image.medium == VariantStatus.READY:
            variants.append("medium")
        if image.large == VariantStatus.READY:
            variants.append("large")
        bucket = (
            settings.R2_PUBLIC_BUCKET
            if image.status in PUBLIC_IMAGE_STATUSES_FOR_R2
            else settings.R2_PRIVATE_BUCKET
        )
        all_uploaded = True
        for variant in variants:
            ext = "webp" if variant == "thumbs" else image.ext
            key = f"{variant}/{image.filename}.{ext}"
            local = FilePath(settings.STORAGE_PATH) / variant / f"{image.filename}.{ext}"
            if not local.exists():
                logger.warning("reconcile_local_missing", image_id=image.image_id, variant=variant)
                all_uploaded = False
                break
            if not await r2.object_exists(bucket=bucket, key=key):
                await r2.upload_file(bucket=bucket, key=key, path=local)

        if all_uploaded:
            new_location = (
                R2Location.PUBLIC
                if image.status in PUBLIC_IMAGE_STATUSES_FOR_R2
                else R2Location.PRIVATE
            )
            async with get_async_session() as db:
                await db.execute(
                    update(Images)
                    .where(Images.image_id == image.image_id)
                    .where(Images.r2_location == R2Location.NONE)
                    .values(r2_location=new_location)
                )
                await db.commit()
            healed += 1

    print(f"reconciled {healed}/{len(rows)} rows")


async def resync_image(image_id: int) -> None:
    """Debug tool: print current R2 state for one image (read-only).

    Intentionally does NOT re-upload — use `reconcile` for healing. This is a
    diagnostic for operators asking "what does R2 think about image N?".
    """
    r2 = get_r2_storage()
    async with get_async_session() as db:
        result = await db.execute(select(Images).where(Images.image_id == image_id))
        image = result.scalar_one_or_none()
    if image is None:
        print(f"image {image_id} not found")
        return

    print(f"image {image_id} filename={image.filename} status={image.status} "
          f"r2_location={image.r2_location}")
    bucket = (
        settings.R2_PUBLIC_BUCKET
        if image.status in PUBLIC_IMAGE_STATUSES_FOR_R2
        else settings.R2_PRIVATE_BUCKET
    )
    for variant in R2_VARIANTS:
        ext = "webp" if variant == "thumbs" else image.ext
        key = f"{variant}/{image.filename}.{ext}"
        exists = await r2.object_exists(bucket=bucket, key=key)
        print(f"  {variant}: {bucket}/{key} exists={exists}")


async def verify(*, sample: int | None) -> dict[str, Any]:
    """Audit: report DB/R2 discrepancies per spec §Operational tooling.

    Reports:
      - PUBLIC rows whose object is missing from the public bucket (`missing`)
      - PRIVATE rows whose object is missing from the private bucket (`missing`)
      - NONE rows whose object unexpectedly exists in either bucket (`unexpected`)
      - Cross-bucket placement: PUBLIC row found in private bucket / vice versa (`wrong_bucket`)

    `r2_location=NONE` with no R2 object is a legitimate state (pending
    finalizer, mode disabled, or staging-imported prod data) and is NOT reported.
    """
    r2 = get_r2_storage()
    discrepancies: list[dict[str, Any]] = []

    async with get_async_session() as db:
        stmt = select(Images)
        if sample:
            stmt = stmt.order_by(Images.image_id.desc()).limit(sample)
        result = await db.execute(stmt)
        rows = list(result.scalars())

    def _variants_for(image: Images) -> list[str]:
        variants = ["fullsize", "thumbs"]
        if image.medium == VariantStatus.READY:
            variants.append("medium")
        if image.large == VariantStatus.READY:
            variants.append("large")
        return variants

    for image in rows:
        variants = _variants_for(image)
        for variant in variants:
            ext = "webp" if variant == "thumbs" else image.ext
            key = f"{variant}/{image.filename}.{ext}"
            in_public = await r2.object_exists(
                bucket=settings.R2_PUBLIC_BUCKET, key=key
            )
            in_private = await r2.object_exists(
                bucket=settings.R2_PRIVATE_BUCKET, key=key
            )
            if image.r2_location == R2Location.NONE:
                # NONE + no object is clean. Either bucket having the object
                # is a leaked/orphaned upload and must be reported.
                if in_public or in_private:
                    discrepancies.append({
                        "kind": "unexpected",
                        "image_id": image.image_id,
                        "key": key,
                        "found_in_public": in_public,
                        "found_in_private": in_private,
                    })
                continue
            expected_bucket = (
                settings.R2_PUBLIC_BUCKET
                if image.r2_location == R2Location.PUBLIC
                else settings.R2_PRIVATE_BUCKET
            )
            found_expected = (
                in_public if image.r2_location == R2Location.PUBLIC else in_private
            )
            found_other = (
                in_private if image.r2_location == R2Location.PUBLIC else in_public
            )
            if not found_expected:
                discrepancies.append({
                    "kind": "missing",
                    "image_id": image.image_id,
                    "bucket": expected_bucket,
                    "key": key,
                })
            if found_other:
                # Cross-bucket orphan: either split-existing left a copy behind
                # or a status-change move didn't complete its delete.
                discrepancies.append({
                    "kind": "wrong_bucket",
                    "image_id": image.image_id,
                    "key": key,
                    "r2_location": int(image.r2_location),
                    "found_in_public": in_public,
                    "found_in_private": in_private,
                })

    report = {"checked": len(rows), "discrepancies": discrepancies}
    print(f"checked {report['checked']} rows, {len(discrepancies)} discrepancies")
    for d in discrepancies[:20]:
        print(f"  {d['kind']}: {d.get('bucket', '')}{d['key']} (image_id={d['image_id']})")
    return report


async def purge_cache_command(*, image_id: int) -> None:
    """Manually invoke Cloudflare purge for one image's CDN URLs."""
    async with get_async_session() as db:
        result = await db.execute(select(Images).where(Images.image_id == image_id))
        image = result.scalar_one_or_none()
    if image is None:
        print(f"image {image_id} not found")
        return
    variants = ["fullsize", "thumbs"]
    if image.medium == VariantStatus.READY:
        variants.append("medium")
    if image.large == VariantStatus.READY:
        variants.append("large")
    urls = []
    for variant in variants:
        ext = "webp" if variant == "thumbs" else image.ext
        urls.append(f"{settings.R2_PUBLIC_CDN_URL}/{variant}/{image.filename}.{ext}")
    await purge_cache_by_urls(urls)
    print(f"purged {len(urls)} URLs for image {image_id}")


async def health(*, output_json: bool = False) -> dict[str, Any]:
    """Read-only health report for monitoring wiring."""
    import asyncio as _asyncio
    import subprocess
    from datetime import UTC, datetime
    from sqlalchemy import func

    async with get_async_session() as db:
        count_result = await db.execute(
            select(func.count()).select_from(Images).where(Images.r2_location == R2Location.NONE)
        )
        unsynced_count = count_result.scalar_one()

        oldest_result = await db.execute(
            select(func.min(Images.date_added)).where(Images.r2_location == R2Location.NONE)
        )
        oldest = oldest_result.scalar_one_or_none()
        # DB columns are naive UTC; strip tz from "now" for a clean subtraction.
        oldest_age = (
            int((datetime.now(UTC).replace(tzinfo=None) - oldest).total_seconds())
            if oldest
            else 0
        )

    try:
        # subprocess is sync — offload so we don't block the event loop.
        du_output = await _asyncio.to_thread(
            subprocess.check_output, ["du", "-sb", settings.STORAGE_PATH], text=True
        )
        local_bytes = int(du_output.split()[0])
    except Exception:
        local_bytes = -1

    report = {
        "unsynced_count": unsynced_count,
        "oldest_unsynced_age_seconds": oldest_age,
        "local_storage_used_bytes": local_bytes,
        "local_storage_path": settings.STORAGE_PATH,
    }
    if output_json:
        import json
        print(json.dumps(report))
    else:
        for k, v in report.items():
            print(f"{k}: {v}")
    return report
```

Update `_dispatch`:

```python
async def _dispatch(args: argparse.Namespace) -> int:
    require_r2_enabled()

    if args.command == "split-existing":
        await split_existing(dry_run=args.dry_run)
    elif args.command == "backfill-locations":
        await backfill_locations()
    elif args.command == "reconcile":
        await reconcile(stale_after=args.stale_after)
    elif args.command == "image":
        await resync_image(args.image_id)
    elif args.command == "verify":
        # Parser default is None; argparse keeps None when --sample is omitted.
        await verify(sample=args.sample)
    elif args.command == "purge-cache":
        await purge_cache_command(image_id=args.image_id)
    elif args.command == "health":
        await health(output_json=args.json)
    else:
        raise ValueError(f"unknown command: {args.command}")
    return 0
```

**Step 4: Run tests**

Run: `uv run pytest tests/unit/test_r2_sync_remaining.py -v`
Expected: all PASS

**Step 5: Run full test suite**

Run: `uv run pytest -x --tb=short`
Expected: pass.

**Step 6: Commit**

```bash
git add scripts/r2_sync.py tests/unit/test_r2_sync_remaining.py
git commit -m "feat(r2): add reconcile/image/verify/purge-cache/health subcommands"
```

---

## Chunk 7: Cleanup and Operator Docs

Remove legacy `STORAGE_TYPE` / `S3_*` placeholders, document the nginx config, write the operator runbook.

---

### Task 21: Remove Legacy `STORAGE_TYPE` and `S3_*` Settings

**Files:**
- Modify: `app/config.py`

**Step 1: Confirm they're unused**

Run: `uv run grep -rn "STORAGE_TYPE\|S3_BUCKET\|S3_ACCESS_KEY\|S3_SECRET_KEY\|S3_ENDPOINT\|S3_REGION" app/ tests/ scripts/ | grep -v "app/config.py"`
Expected: no matches (other than `app/config.py` itself).

**Step 2: Remove from `app/config.py`**

In `app/config.py`, delete the block:

```python
    # File Storage
    STORAGE_TYPE: str = Field(default="local", pattern="^(local|s3)$")
    STORAGE_PATH: str = "/shuushuu/images"

    # S3 Configuration (if using S3)
    S3_BUCKET: str | None = None
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_ENDPOINT: str | None = None
    S3_REGION: str = "us-east-1"
```

Keep `STORAGE_PATH` — it's still the local filesystem root:

```python
    # Local filesystem root for image storage (used as fallback when R2 is
    # disabled, or as the source for R2 uploads during phase 1).
    STORAGE_PATH: str = "/shuushuu/images"
```

**Step 3: Run full test suite**

Run: `uv run pytest -x --tb=short`
Expected: pass. `.env` files in the wild that still set `STORAGE_TYPE` / `S3_*` are silently ignored by `extra="ignore"` on `SettingsConfigDict`.

**Step 4: Commit**

```bash
git add app/config.py
git commit -m "chore(r2): remove unused STORAGE_TYPE and S3_* config"
```

---

### Task 22: Document nginx, Env Vars, and Operator Runbook

**Files:**
- Modify: `docs/image-serving-nginx.md`
- Create: `docs/r2-operations.md`

**Step 1: Update nginx doc**

In `docs/image-serving-nginx.md`, add a section at the end:

```markdown
## R2 serving (R2_ENABLED=true)

When `R2_ENABLED=true`, the FastAPI `/images/*` endpoint returns 302 redirects
with `Cache-Control: no-store` instead of `X-Accel-Redirect`:

- Public images (`r2_location=PUBLIC`) 302 to the public CDN domain. Browsers
  follow the redirect; nginx is not in the path for the actual image bytes.
- Protected images (`r2_location=PRIVATE`) 302 to a short-lived presigned URL
  against the private R2 bucket. Each request issues a fresh presigned URL.
- Unfinalized or disabled-mode images (`r2_location=NONE`) still return
  `X-Accel-Redirect` headers pointing at `/internal/*` — same behaviour as
  before the R2 work.

No nginx configuration change is required. The existing `proxy_cache off`
directive on `/images/*`, `/thumbs/*`, `/medium/*`, `/large/*` blocks must be
preserved — nginx must not cache the 302 responses. The `/internal/*` location
blocks remain for the fallback path.
```

**Step 2: Create the operator runbook**

Create `docs/r2-operations.md`:

```markdown
# R2 Operations Runbook

See `docs/plans/2026-04-16-r2-image-serving-design.md` for the full design.
This file is the short reference for operators.

## Environment variables

| Name | Prod | Staging | Dev |
|------|------|---------|-----|
| `R2_ENABLED` | `true` | `true` | `false` |
| `R2_ALLOW_BULK_BACKFILL` | `true` | `false` | — |
| `R2_ACCESS_KEY_ID` | per-env | per-env | — |
| `R2_SECRET_ACCESS_KEY` | per-env | per-env | — |
| `R2_ENDPOINT` | R2 endpoint URL | same | — |
| `R2_PUBLIC_BUCKET` | `shuushuu-images` | `shuushuu-images-staging` | — |
| `R2_PRIVATE_BUCKET` | `shuushuu-images-private` | `shuushuu-images-staging-private` | — |
| `R2_PUBLIC_CDN_URL` | `https://cdn.e-shuushuu.net` | `https://cdn-staging.e-shuushuu.net` | — |
| `R2_PRESIGN_TTL_SECONDS` | `900` | `900` | — |
| `CLOUDFLARE_API_TOKEN` | per-env | per-env | — |
| `CLOUDFLARE_ZONE_ID` | per-env (prod zone) | per-env (staging zone) | — |

Staging's separate `CLOUDFLARE_ZONE_ID` ensures purges issued from staging
never affect prod. Staging's `R2_ALLOW_BULK_BACKFILL=false` ensures a
`reconcile` or `backfill-locations` (including inherited nightly crons)
cannot mass-upload prod-imported images into the small staging bucket.

## One-time cutover

1. Create `shuushuu-images-private` bucket.
2. Attach custom CDN domain to `shuushuu-images`.
3. Create a Cloudflare API token with zone-level cache purge permission.
4. Set all R2/Cloudflare env vars (flag still off).
5. Dry-run: `R2_ENABLED=true uv run python scripts/r2_sync.py split-existing --dry-run`
6. Run for real: `R2_ENABLED=true uv run python scripts/r2_sync.py split-existing`
7. Verify: `R2_ENABLED=true uv run python scripts/r2_sync.py verify --sample 1000`
8. Flip bulk-backfill on: set `R2_ALLOW_BULK_BACKFILL=true`.
9. Backfill: `uv run python scripts/r2_sync.py backfill-locations`
10. Flip `R2_ENABLED=true` in the app config, restart app + ARQ workers.
11. Monitor logs for `r2_*_failed` events for a week.

`R2_ALLOW_BULK_BACKFILL` stays `true` in prod permanently so the nightly
`reconcile` cron can heal stuck rows. Staging's `false` setting neutralises
the same cron on that environment.

## Common commands

```bash
# Inspect one image
R2_ENABLED=true uv run python scripts/r2_sync.py image 12345

# Audit recent rows
R2_ENABLED=true uv run python scripts/r2_sync.py verify --sample 1000

# Manual CDN purge
R2_ENABLED=true uv run python scripts/r2_sync.py purge-cache 12345

# Health (wire to monitoring)
R2_ENABLED=true uv run python scripts/r2_sync.py health --json
```

## Rollback

Set `R2_ENABLED=false`, restart app + workers. URLs fall back to `/images/*`
paths served from the local filesystem. R2 objects remain (harmless). DB
`r2_location` values stay (not consulted while the flag is off).

## Alerting thresholds

From `r2_sync.py health --json`:

- WARNING if `unsynced_count > 0` and `oldest_unsynced_age_seconds > 3600`
- CRITICAL if `unsynced_count > 100` or `oldest_unsynced_age_seconds > 21600`
- CRITICAL if `local_storage_used_bytes` exceeds your per-deployment ceiling

These thresholds are NOT useful on staging (which has millions of
`r2_location=NONE` rows by construction — prod DB copy, small R2 bucket).
Scope alerting to prod only, or add a staging-specific counting mode.

## Invariant to preserve in phase 2

When phase 2 removes local filesystem as source-of-truth, the
`local_cleanup_job` MUST refuse to delete any local file unless
`r2_location in {PUBLIC, PRIVATE}`. Do not add a fallback branch that deletes
based on age alone — a broken R2 integration then becomes a data-loss bug.
```

**Step 3: Commit**

```bash
git add docs/image-serving-nginx.md docs/r2-operations.md
git commit -m "docs(r2): operator runbook and nginx-serving notes"
```

---

## Final Verification

Run the entire test suite, mypy, and ruff once more before opening the PR:

```bash
uv run pytest -x --tb=short
uv run mypy app/ scripts/
uv run ruff check app/ scripts/ tests/
uv run ruff format --check app/ scripts/ tests/
```

All must pass. Then push the branch and open a PR against `main`.

The `R2_ENABLED=false` default means this work can merge without touching prod
behaviour. Production cutover follows the runbook in `docs/r2-operations.md`.
