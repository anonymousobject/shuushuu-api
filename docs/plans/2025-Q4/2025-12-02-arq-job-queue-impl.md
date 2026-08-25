# ARQ Job Queue Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace FastAPI background tasks with arq-based job queue for reliability, retries, and job dependencies.

**Architecture:** Add arq worker process that handles background jobs (image processing, IQDB indexing, rating calculations) with automatic retries and proper job ordering. Use Redis database 1 for arq (database 0 for caching). Jobs run in separate worker process with same codebase access.

**Tech Stack:** arq 0.26+, Redis 7, FastAPI 0.115+, Python 3.12

---

## Task 1: Add arq Dependency

**Files:**
- Modify: `pyproject.toml:38`

**Step 1: Uncomment arq dependency**

Edit `pyproject.toml` line 38:

```toml
# Before:
    # "arq>=0.26.0",  # Task queue - may be used for background jobs

# After:
    "arq>=0.26.0",  # Task queue for background jobs
```

**Step 2: Install dependency**

Run: `uv sync`
Expected: arq installed successfully

**Step 3: Verify installation**

Run: `uv run python -c "import arq; print(arq.__version__)"`
Expected: Prints version like "0.26.0" or similar

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add arq for job queue"
```

---

## Task 2: Create ARQ Worker Module Structure

**Files:**
- Create: `app/tasks/__init__.py`
- Create: `app/tasks/worker.py`

**Step 1: Create tasks package**

Create `app/tasks/__init__.py`:

```python
"""
Background task queue using arq.

Tasks are enqueued from API endpoints and processed by worker process.
"""
```

**Step 2: Create worker configuration**

Create `app/tasks/worker.py`:

```python
"""
ARQ worker configuration and job definitions.

Run worker with: uv run arq app.tasks.worker.WorkerSettings
"""

from arq import create_pool
from arq.connections import RedisSettings

from app.config import settings


async def startup(ctx: dict) -> None:
    """Worker startup - initialize any shared resources."""
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("arq_worker_starting", redis_url=settings.ARQ_REDIS_URL)


async def shutdown(ctx: dict) -> None:
    """Worker shutdown - cleanup resources."""
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("arq_worker_shutdown")


class WorkerSettings:
    """ARQ worker configuration."""

    # Redis connection from settings
    redis_settings = RedisSettings.from_dsn(settings.ARQ_REDIS_URL)

    # Worker behavior
    max_jobs = 10  # Process up to 10 jobs concurrently
    job_timeout = 300  # 5 minutes max per job
    keep_result = settings.ARQ_KEEP_RESULT  # Keep results for 1 hour

    # Lifecycle hooks
    on_startup = startup
    on_shutdown = shutdown

    # Job functions - will add these in next tasks
    functions = []
```

**Step 3: Verify module imports**

Run: `uv run python -c "from app.tasks.worker import WorkerSettings; print('OK')"`
Expected: Prints "OK"

**Step 4: Commit**

```bash
git add app/tasks/
git commit -m "feat: create arq worker configuration"
```

---

## Task 3: Create Image Processing Jobs

**Files:**
- Create: `app/tasks/image_jobs.py`
- Modify: `app/tasks/worker.py`

**Step 1: Create image processing jobs module**

Create `app/tasks/image_jobs.py`:

```python
"""Image processing background jobs for arq worker."""

from pathlib import Path as FilePath

from arq import Retry

from app.config import settings
from app.core.logging import bind_context, get_logger

logger = get_logger(__name__)


async def create_thumbnail_job(
    ctx: dict,
    image_id: int,
    source_path: str,
    ext: str,
    storage_path: str,
) -> dict[str, bool | str]:
    """
    Create thumbnail for uploaded image.

    Args:
        ctx: ARQ context dict
        image_id: Database image ID
        source_path: Path to original image file
        ext: File extension (jpg, png, etc.)
        storage_path: Base storage directory

    Returns:
        dict with success status and thumbnail_path

    Raises:
        Retry: If thumbnail generation fails (will retry up to max_tries)
    """
    bind_context(task="thumbnail_generation", image_id=image_id)

    try:
        # Import here to avoid loading PIL at module level
        from app.services.image_processing import create_thumbnail

        # Call existing sync function (runs in thread pool)
        create_thumbnail(
            source_path=FilePath(source_path),
            image_id=image_id,
            ext=ext,
            storage_path=storage_path,
        )

        thumb_path = f"{storage_path}/thumbs/{image_id}.{ext}"
        logger.info("thumbnail_job_completed", image_id=image_id, path=thumb_path)

        return {"success": True, "thumbnail_path": thumb_path}

    except Exception as e:
        logger.error(
            "thumbnail_job_failed",
            image_id=image_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        # Retry with exponential backoff (arq default: 0s, 5s, 15s, 30s, etc.)
        raise Retry(defer=ctx["job_try"] * 5) from e


async def create_variant_job(
    ctx: dict,
    image_id: int,
    source_path: str,
    ext: str,
    storage_path: str,
    width: int,
    height: int,
    variant_type: str,
) -> dict[str, bool]:
    """
    Create image variant (medium or large).

    Args:
        ctx: ARQ context dict
        image_id: Database image ID
        source_path: Path to original image
        ext: File extension
        storage_path: Base storage directory
        width: Original image width
        height: Original image height
        variant_type: 'medium' or 'large'

    Returns:
        dict with success status

    Raises:
        Retry: If variant generation fails
    """
    bind_context(task=f"{variant_type}_variant_generation", image_id=image_id)

    try:
        from app.services.image_processing import _create_variant

        result = _create_variant(
            source_path=FilePath(source_path),
            image_id=image_id,
            ext=ext,
            storage_path=storage_path,
            width=width,
            height=height,
            size_threshold=settings.MEDIUM_EDGE if variant_type == "medium" else settings.LARGE_EDGE,
            variant_type=variant_type,
        )

        logger.info(f"{variant_type}_variant_job_completed", image_id=image_id, created=result)

        return {"success": True, "created": result}

    except Exception as e:
        logger.error(
            f"{variant_type}_variant_job_failed",
            image_id=image_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise Retry(defer=ctx["job_try"] * 5) from e


async def add_to_iqdb_job(
    ctx: dict,
    image_id: int,
    thumb_path: str,
) -> dict[str, bool]:
    """
    Add image thumbnail to IQDB index.

    NOTE: This should be enqueued AFTER thumbnail_job completes.
    No more polling/sleep hacks!

    Args:
        ctx: ARQ context dict
        image_id: Database image ID
        thumb_path: Path to thumbnail file

    Returns:
        dict with success status

    Raises:
        Retry: If IQDB is unavailable
    """
    bind_context(task="iqdb_indexing", image_id=image_id)

    try:
        import httpx

        thumb_file = FilePath(thumb_path)

        # Verify thumbnail exists (should always exist since we depend on thumbnail job)
        if not thumb_file.exists():
            logger.error("iqdb_job_thumbnail_missing", image_id=image_id, path=thumb_path)
            return {"success": False, "error": "thumbnail_not_found"}

        iqdb_url = f"http://{settings.IQDB_HOST}:{settings.IQDB_PORT}/images/{image_id}"

        with open(thumb_file, "rb") as f:
            files = {"file": (thumb_file.name, f, "image/jpeg")}

            # Use sync httpx client (worker runs in thread pool)
            with httpx.Client(timeout=10.0) as client:
                response = client.post(iqdb_url, files=files)

        if response.status_code in (200, 201):
            logger.info("iqdb_job_completed", image_id=image_id)
            return {"success": True}
        else:
            logger.warning(
                "iqdb_job_failed_status",
                image_id=image_id,
                status_code=response.status_code,
            )
            # Retry if IQDB returned error
            raise Retry(defer=ctx["job_try"] * 10)

    except httpx.RequestError as e:
        logger.error("iqdb_job_request_failed", image_id=image_id, error=str(e))
        # Retry on network errors
        raise Retry(defer=ctx["job_try"] * 10) from e

    except Exception as e:
        logger.error(
            "iqdb_job_unexpected_error",
            image_id=image_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        return {"success": False, "error": str(e)}
```

**Step 2: Register jobs in worker**

Edit `app/tasks/worker.py`, update the `functions` list:

```python
# At top, add imports
from arq.worker import func

from app.tasks.image_jobs import (
    add_to_iqdb_job,
    create_thumbnail_job,
    create_variant_job,
)

# In WorkerSettings class, replace functions list:
    functions = [
        func(create_thumbnail_job, max_tries=3),
        func(create_variant_job, max_tries=3),
        func(add_to_iqdb_job, max_tries=3),
    ]
```

**Step 3: Verify imports**

Run: `uv run python -c "from app.tasks.image_jobs import create_thumbnail_job; print('OK')"`
Expected: Prints "OK"

**Step 4: Commit**

```bash
git add app/tasks/
git commit -m "feat: add image processing arq jobs"
```

---

## Task 4: Create Rating Calculation Job

**Files:**
- Create: `app/tasks/rating_jobs.py`
- Modify: `app/tasks/worker.py`

**Step 1: Create rating jobs module**

Create `app/tasks/rating_jobs.py`:

```python
"""Rating calculation background jobs for arq worker."""

from arq import Retry

from app.core.database import get_async_session
from app.core.logging import bind_context, get_logger

logger = get_logger(__name__)


async def recalculate_rating_job(
    ctx: dict,
    image_id: int,
) -> dict[str, bool]:
    """
    Recalculate Bayesian rating for an image.

    Args:
        ctx: ARQ context dict
        image_id: Image ID to recalculate

    Returns:
        dict with success status

    Raises:
        Retry: If database operation fails
    """
    bind_context(task="rating_recalculation", image_id=image_id)

    try:
        from app.services.rating import recalculate_image_ratings

        async with get_async_session() as db:
            await recalculate_image_ratings(db, image_id)
            await db.commit()

        logger.info("rating_recalculation_completed", image_id=image_id)
        return {"success": True}

    except Exception as e:
        logger.error(
            "rating_recalculation_failed",
            image_id=image_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        # Retry with backoff
        raise Retry(defer=ctx["job_try"] * 5) from e
```

**Step 2: Register job in worker**

Edit `app/tasks/worker.py`:

```python
# Add import
from app.tasks.rating_jobs import recalculate_rating_job

# Add to functions list in WorkerSettings:
    functions = [
        func(create_thumbnail_job, max_tries=3),
        func(create_variant_job, max_tries=3),
        func(add_to_iqdb_job, max_tries=3),
        func(recalculate_rating_job, max_tries=3),
    ]
```

**Step 3: Verify imports**

Run: `uv run python -c "from app.tasks.rating_jobs import recalculate_rating_job; print('OK')"`
Expected: Prints "OK"

**Step 4: Commit**

```bash
git add app/tasks/
git commit -m "feat: add rating calculation arq job"
```

---

## Task 5: Create Queue Client Helper

**Files:**
- Create: `app/tasks/queue.py`

**Step 1: Create queue client module**

Create `app/tasks/queue.py`:

```python
"""
Queue client for enqueuing arq jobs from API endpoints.

Provides a simple interface for adding jobs to the arq queue.
"""

from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Global pool instance (created on first use)
_pool: ArqRedis | None = None


async def get_queue() -> ArqRedis:
    """
    Get or create arq Redis connection pool.

    Returns:
        ArqRedis pool instance
    """
    global _pool
    if _pool is None:
        redis_settings = RedisSettings.from_dsn(settings.ARQ_REDIS_URL)
        _pool = await create_pool(redis_settings)
        logger.info("arq_pool_created", redis_url=settings.ARQ_REDIS_URL)
    return _pool


async def enqueue_job(
    function_name: str,
    *args: Any,
    _job_id: str | None = None,
    _defer_by: float | None = None,
    **kwargs: Any,
) -> str | None:
    """
    Enqueue a job to arq worker.

    Args:
        function_name: Name of registered arq function
        *args: Positional arguments for the function
        _job_id: Optional custom job ID
        _defer_by: Optional delay in seconds before job runs
        **kwargs: Keyword arguments for the function

    Returns:
        Job ID if enqueued successfully, None otherwise

    Example:
        await enqueue_job("create_thumbnail", image_id=123, source_path="/path/to/image.jpg")
    """
    try:
        pool = await get_queue()
        job = await pool.enqueue_job(
            function_name,
            *args,
            _job_id=_job_id,
            _defer_by=_defer_by,
            **kwargs,
        )

        if job:
            logger.debug("job_enqueued", function=function_name, job_id=job.job_id, kwargs=kwargs)
            return job.job_id
        else:
            logger.warning("job_enqueue_failed", function=function_name, kwargs=kwargs)
            return None

    except Exception as e:
        logger.error(
            "job_enqueue_error",
            function=function_name,
            error=str(e),
            error_type=type(e).__name__,
        )
        return None


async def close_queue() -> None:
    """Close arq Redis connection pool (call on shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("arq_pool_closed")
```

**Step 2: Verify imports**

Run: `uv run python -c "from app.tasks.queue import enqueue_job; print('OK')"`
Expected: Prints "OK"

**Step 3: Commit**

```bash
git add app/tasks/queue.py
git commit -m "feat: add arq queue client helper"
```

---

## Task 6: Update Main App Lifecycle

**Files:**
- Modify: `app/main.py`

**Step 1: Add queue shutdown to app lifespan**

Find the lifespan context manager in `app/main.py` and add queue cleanup:

```python
# Add import at top
from app.tasks.queue import close_queue

# In the lifespan function, add shutdown:
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown tasks."""
    # Startup
    logger.info("application_starting", version=settings.VERSION)

    yield

    # Shutdown
    logger.info("application_shutting_down")
    await close_queue()  # Close arq pool
```

**Step 2: Verify app still starts**

Run: `uv run python -c "from app.main import app; print('OK')"`
Expected: Prints "OK"

**Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat: add arq pool cleanup on shutdown"
```

---

## Task 7: Migrate Image Upload Endpoint

**Files:**
- Modify: `app/api/v1/images.py:752-791`

**Step 1: Replace BackgroundTasks with arq jobs**

Find the upload_image endpoint and replace background task scheduling:

```python
# Remove BackgroundTasks from function signature (line ~591):
# Before:
async def upload_image(
    request: Request,
    background_tasks: BackgroundTasks,  # REMOVE THIS
    current_user: Annotated[Users, Depends(get_current_user)],
    ...

# After:
async def upload_image(
    request: Request,
    current_user: Annotated[Users, Depends(get_current_user)],
    ...
```

Add import at top of file:

```python
from app.tasks.queue import enqueue_job
```

Replace the background task section (lines ~751-791):

```python
        # Before: (lines 751-791)
        # Schedule thumbnail generation in background
        # background_tasks.add_task(...)
        # etc.

        # After:
        # Schedule thumbnail generation (blocking until complete)
        await enqueue_job(
            "create_thumbnail",
            image_id=image_id,
            source_path=str(file_path),
            ext=ext,
            storage_path=settings.STORAGE_PATH,
        )
        logger.debug("thumbnail_job_enqueued", image_id=image_id)

        # Schedule medium variant generation if needed
        if has_medium:
            await enqueue_job(
                "create_variant",
                image_id=image_id,
                source_path=str(file_path),
                ext=ext,
                storage_path=settings.STORAGE_PATH,
                width=width,
                height=height,
                variant_type="medium",
            )

        # Schedule large variant generation if needed
        if has_large:
            await enqueue_job(
                "create_variant",
                image_id=image_id,
                source_path=str(file_path),
                ext=ext,
                storage_path=settings.STORAGE_PATH,
                width=width,
                height=height,
                variant_type="large",
            )

        # Add to IQDB index AFTER thumbnail is created
        # Use defer to ensure thumbnail completes first (simple approach)
        thumb_path = FilePath(settings.STORAGE_PATH) / "thumbs" / f"{date_prefix}-{image_id}.{ext}"
        await enqueue_job(
            "add_to_iqdb",
            image_id=image_id,
            thumb_path=str(thumb_path),
            _defer_by=5.0,  # Wait 5 seconds for thumbnail to complete
        )
```

**Step 2: Remove BackgroundTasks import**

Remove or comment out the BackgroundTasks import:

```python
# Remove from imports:
# from fastapi import (
#     BackgroundTasks,  # REMOVE THIS
# )
```

**Step 3: Verify endpoint still imports**

Run: `uv run python -c "from app.api.v1.images import router; print('OK')"`
Expected: Prints "OK"

**Step 4: Commit**

```bash
git add app/api/v1/images.py
git commit -m "feat: migrate image upload to arq jobs"
```

---

## Task 8: Migrate Rating Calculation

**Files:**
- Modify: `app/services/rating.py:90-123`

**Step 1: Replace asyncio.create_task with arq job**

Replace the `schedule_rating_recalculation` function:

```python
# Before (lines 90-123):
def schedule_rating_recalculation(image_id: int) -> None:
    """Schedule a background task..."""
    from app.core.database import get_async_session

    async def _background_task() -> None:
        # ...

    asyncio.create_task(_background_task())

# After:
async def schedule_rating_recalculation(image_id: int) -> None:
    """
    Schedule a background job to recalculate image ratings using arq.

    This enqueues the job to the arq worker for async processing with retries.

    Args:
        image_id: ID of the image to recalculate ratings for
    """
    from app.tasks.queue import enqueue_job

    await enqueue_job("recalculate_rating", image_id=image_id)
```

**Step 2: Find all callers and make them async**

Search for calls to `schedule_rating_recalculation`:

Run: `uv run grep -rn "schedule_rating_recalculation" app/`

For each caller, ensure they await it:

```python
# Before:
schedule_rating_recalculation(image_id)

# After:
await schedule_rating_recalculation(image_id)
```

**Step 3: Remove unused imports**

Remove `asyncio` import if no longer needed:

```python
# Remove:
# import asyncio
```

**Step 4: Verify imports**

Run: `uv run python -c "from app.services.rating import schedule_rating_recalculation; print('OK')"`
Expected: Prints "OK"

**Step 5: Commit**

```bash
git add app/services/rating.py
git commit -m "feat: migrate rating calculation to arq jobs"
```

---

## Task 9: Remove IQDB Polling Hack

**Files:**
- Modify: `app/services/iqdb.py:96-108`

**Step 1: Remove polling loop from add_to_iqdb**

Since arq handles job ordering, remove the polling hack:

```python
# Before (lines 96-108):
    try:
        # Wait for thumbnail to be created (it's also a background task)
        # Check if file exists with short retry loop
        import time

        max_retries = 20
        for _ in range(max_retries):
            if thumb_path.exists():
                break
            time.sleep(0.5)  # Wait 500ms between checks
        else:
            # Thumbnail not ready after 10 seconds, skip IQDB insertion
            return

        iqdb_url = f"http://{settings.IQDB_HOST}:{settings.IQDB_PORT}/images/{image_id}"
        # ...

# After:
    try:
        # Thumbnail should exist (job ordering handled by arq defer)
        if not thumb_path.exists():
            # Log warning but don't crash
            logger.warning("iqdb_thumbnail_missing", image_id=image_id, path=str(thumb_path))
            return

        iqdb_url = f"http://{settings.IQDB_HOST}:{settings.IQDB_PORT}/images/{image_id}"
        # ... rest of function unchanged
```

Add logger import if not present:

```python
from app.core.logging import get_logger

logger = get_logger(__name__)
```

**Step 2: Verify imports**

Run: `uv run python -c "from app.services.iqdb import add_to_iqdb; print('OK')"`
Expected: Prints "OK"

**Step 3: Commit**

```bash
git add app/services/iqdb.py
git commit -m "refactor: remove iqdb polling hack (handled by arq)"
```

---

## Task 10: Update Docker Compose

**Files:**
- Modify: `docker-compose.yml:117-147`

**Step 1: Uncomment arq worker service**

Edit `docker-compose.yml` lines 117-147, uncomment the arq-worker service:

```yaml
  # Remove the comment markers from arq-worker service:
  arq-worker:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: shuushuu-arq-worker
    command: uv run arq app.tasks.worker.WorkerSettings
    environment:
      - ENVIRONMENT=${ENVIRONMENT:-development}
      - DATABASE_URL=mysql+aiomysql://${MARIADB_USER:-shuushuu}:${MARIADB_PASSWORD:-shuushuu_password}@mariadb:3306/${MARIADB_DATABASE:-shuushuu}?charset=utf8mb4
      - REDIS_URL=${REDIS_URL:-redis://redis:6379/0}
      - ARQ_REDIS_URL=${ARQ_REDIS_URL:-redis://redis:6379/1}
      - SECRET_KEY=${SECRET_KEY}
      - IQDB_HOST=iqdb
      - IQDB_PORT=${IQDB_PORT:-5588}
      - STORAGE_PATH=${STORAGE_PATH:-/shuushuu/images}
      # Prevent Python from writing pyc files
      - PYTHONDONTWRITEBYTECODE=1
    volumes:
      - .:/app
      - ${STORAGE_PATH:-/shuushuu/images}:${STORAGE_PATH:-/shuushuu/images}
      # Anonymous volumes to isolate container artifacts
      - /app/.venv
      - /app/.pytest_cache
      - /app/.ruff_cache
    depends_on:
      redis:
        condition: service_healthy
      mariadb:
        condition: service_healthy
    develop:
      watch:
        - action: rebuild
          path: ./Dockerfile
        - action: rebuild
          path: ./pyproject.toml
        - action: rebuild
          path: ./uv.lock
        - action: sync
          path: ./app
          target: /app/app
    restart: unless-stopped
```

**Step 2: Update .env with ARQ settings**

Add to `.env` file (or verify they exist):

```bash
ARQ_REDIS_URL=redis://redis:6379/1
ARQ_MAX_TRIES=3
ARQ_KEEP_RESULT=3600
```

Also update .env.example similarly.

**Step 3: Test docker-compose config**

Run: `docker-compose config`
Expected: No errors, valid YAML output

**Step 4: Commit**

```bash
git add docker-compose.yml .env
git commit -m "feat: enable arq worker in docker-compose"
```

---

## Task 11: Add Development Worker Run Script

**Files:**
- Create: `scripts/run-worker.sh`

**Step 1: Create worker run script**

Create `scripts/run-worker.sh`:

```bash
#!/usr/bin/env bash
#
# Run arq worker for local development
#

set -e

echo "Starting arq worker..."
echo "Redis: ${ARQ_REDIS_URL:-redis://localhost:6379/1}"
echo ""

uv run arq app.tasks.worker.WorkerSettings --verbose
```

**Step 2: Make executable**

Run: `chmod +x scripts/run-worker.sh`

**Step 3: Test script exists**

Run: `ls -la scripts/run-worker.sh`
Expected: Shows executable permissions

**Step 4: Commit**

```bash
git add scripts/run-worker.sh
git commit -m "feat: add worker run script for development"
```

---

## Task 12: Write Worker Tests

**Files:**
- Create: `tests/unit/test_arq_jobs.py`

**Step 1: Write test for thumbnail job**

Create `tests/unit/test_arq_jobs.py`:

```python
"""Tests for arq background jobs."""

import pytest
from pathlib import Path as FilePath
from unittest.mock import AsyncMock, Mock, patch

from app.tasks.image_jobs import create_thumbnail_job


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_thumbnail_job_success():
    """Test successful thumbnail creation job."""
    # Arrange
    ctx = {"job_try": 1}
    image_id = 123
    source_path = "/test/image.jpg"
    ext = "jpg"
    storage_path = "/test/storage"

    # Mock the image processing function
    with patch("app.tasks.image_jobs.create_thumbnail") as mock_create:
        # Act
        result = await create_thumbnail_job(ctx, image_id, source_path, ext, storage_path)

        # Assert
        assert result["success"] is True
        assert "thumbnail_path" in result
        mock_create.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_thumbnail_job_retry_on_failure():
    """Test thumbnail job retries on failure."""
    from arq import Retry

    # Arrange
    ctx = {"job_try": 1}

    # Mock to raise exception
    with patch("app.tasks.image_jobs.create_thumbnail") as mock_create:
        mock_create.side_effect = Exception("Image processing failed")

        # Act & Assert
        with pytest.raises(Retry):
            await create_thumbnail_job(ctx, 123, "/test/image.jpg", "jpg", "/test/storage")
```

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_arq_jobs.py -v`
Expected: 2 tests pass

**Step 3: Commit**

```bash
git add tests/unit/test_arq_jobs.py
git commit -m "test: add arq job tests"
```

---

## Task 13: Integration Testing

**Files:**
- None (testing only)

**Step 1: Start services with docker-compose**

Run: `docker-compose up -d`
Expected: All services start including arq-worker

**Step 2: Verify worker is running**

Run: `docker-compose logs arq-worker --tail=20`
Expected: See "arq_worker_starting" log message

**Step 3: Check Redis connection**

Run: `docker-compose exec redis redis-cli -n 1 PING`
Expected: Prints "PONG"

**Step 4: Test image upload (triggers jobs)**

Run: `curl -X POST http://localhost:8000/api/v1/images/upload -F "file=@test-image.jpg" -H "Authorization: Bearer YOUR_TOKEN"`
Expected: Upload succeeds (200 response)

**Step 5: Check worker logs for job processing**

Run: `docker-compose logs arq-worker --tail=50 --follow`
Expected: See jobs being processed:
- "thumbnail_generation_started"
- "thumbnail_job_completed"
- "iqdb_indexing"
- etc.

**Step 6: Verify jobs in Redis**

Run: `docker-compose exec redis redis-cli -n 1 KEYS "arq:*"`
Expected: See arq job keys

---

## Task 14: Documentation

**Files:**
- Create: `docs/arq-background-jobs.md`

**Step 1: Create documentation**

Create `docs/arq-background-jobs.md`:

```markdown
# Background Jobs with ARQ

This application uses [arq](https://arq-docs.helpmanual.io/) for reliable background job processing.

## Architecture

- **FastAPI app**: Enqueues jobs via `app.tasks.queue.enqueue_job()`
- **ARQ worker**: Processes jobs in separate process/container
- **Redis**: Job queue storage (database 1, caching uses database 0)

## Running the Worker

### Development (local)

```bash
./scripts/run-worker.sh
```

### Docker Compose

Worker starts automatically with `docker-compose up`.

### Production

```bash
uv run arq app.tasks.worker.WorkerSettings
```

## Available Jobs

### Image Processing

- `create_thumbnail`: Generate thumbnail (300x300)
- `create_variant`: Generate medium/large variants
- `add_to_iqdb`: Index image in IQDB

### Ratings

- `recalculate_rating`: Update Bayesian rating

## Job Configuration

- **Max tries**: 3 attempts per job
- **Timeout**: 5 minutes per job
- **Retry backoff**: Exponential (5s, 10s, 15s...)
- **Concurrency**: 10 jobs at once

## Monitoring

### View worker logs

```bash
docker-compose logs arq-worker --follow
```

### Check Redis queue

```bash
docker-compose exec redis redis-cli -n 1
> KEYS arq:*
> HGETALL arq:job:JOBID
```

### Redis Commander UI

Open http://localhost:8081 to view queues in browser.

## Adding New Jobs

1. Create job function in `app/tasks/*_jobs.py`
2. Register in `app/tasks/worker.py` functions list
3. Enqueue from API: `await enqueue_job("job_name", arg1=val1)`

Example:

```python
# Define job in app/tasks/my_jobs.py
async def my_new_job(ctx: dict, param: str) -> dict:
    logger.info("job_running", param=param)
    return {"success": True}

# Register in worker.py - add to functions list
from arq.worker import func
from app.tasks.my_jobs import my_new_job

functions = [
    # ... existing jobs ...
    func(my_new_job, max_tries=3),  # Job name inferred as "my_new_job"
]

# Enqueue from API (use function name without "_job" suffix if desired)
await enqueue_job("my_new_job", param="value")
```

## Troubleshooting

**Worker not starting?**
- Check Redis is running: `docker-compose ps redis`
- Check ARQ_REDIS_URL in .env
- Check worker logs: `docker-compose logs arq-worker`

**Jobs not processing?**
- Verify job is enqueued: Redis Commander or `redis-cli`
- Check worker is running: `docker-compose ps arq-worker`
- Check for errors in worker logs

**Job keeps retrying?**
- Check worker logs for error details
- Verify job dependencies (files exist, services available)
- Check max_tries configuration
```

**Step 2: Commit**

```bash
git add docs/arq-background-jobs.md
git commit -m "docs: add arq background jobs documentation"
```

---

## Task 15: Update Main README

**Files:**
- Modify: `README.md`

**Step 1: Add ARQ section to README**

Add to README.md under "Development" section:

```markdown
### Background Jobs

The application uses ARQ for background job processing (image processing, IQDB indexing, rating calculations).

The worker starts automatically with `docker-compose up`. For local development without Docker:

```bash
./scripts/run-worker.sh
```

See [docs/arq-background-jobs.md](docs/arq-background-jobs.md) for details.
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add arq worker to README"
```

---

## Task 16: Final Verification

**Files:**
- None (testing only)

**Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass

**Step 2: Check type checking**

Run: `uv run mypy app/`
Expected: No errors

**Step 3: Check linting**

Run: `uv run ruff check app/`
Expected: No errors

**Step 4: Verify services all start**

Run: `docker-compose down && docker-compose up -d`
Expected: All services healthy

**Step 5: Final integration test**

Upload test image and verify:
1. Upload succeeds
2. Thumbnail appears in `/shuushuu/images/thumbs/`
3. Worker logs show job processing
4. No errors in logs

---

## Optional: Add Scheduled Jobs

**Files:**
- Create: `app/tasks/scheduled_jobs.py`
- Modify: `app/tasks/worker.py`

**Step 1: Create scheduled jobs module**

Create `app/tasks/scheduled_jobs.py`:

```python
"""Scheduled jobs for review system and maintenance."""

from arq.cron import cron

from app.core.database import get_async_session
from app.core.logging import bind_context, get_logger

logger = get_logger(__name__)


async def check_review_deadlines_job(ctx: dict) -> dict[str, int]:
    """
    Scheduled job to check and process expired reviews.

    Runs every hour.

    Returns:
        dict with processing stats
    """
    bind_context(task="check_review_deadlines")

    try:
        from app.services.review_jobs import check_review_deadlines

        async with get_async_session() as db:
            results = await check_review_deadlines(db)
            await db.commit()

        logger.info("review_deadlines_checked", **results)
        return results

    except Exception as e:
        logger.error("review_deadlines_check_failed", error=str(e), error_type=type(e).__name__)
        return {"errors": 1, "error_details": [str(e)]}


async def prune_admin_actions_job(ctx: dict) -> dict[str, int]:
    """
    Scheduled job to prune old audit logs.

    Runs daily at 2 AM.

    Returns:
        dict with deleted count
    """
    bind_context(task="prune_admin_actions")

    try:
        from app.services.review_jobs import prune_admin_actions

        async with get_async_session() as db:
            deleted = await prune_admin_actions(db, retention_years=2)

        logger.info("admin_actions_pruned", deleted=deleted)
        return {"deleted": deleted}

    except Exception as e:
        logger.error("admin_actions_prune_failed", error=str(e), error_type=type(e).__name__)
        return {"deleted": 0, "error": str(e)}
```

**Step 2: Register cron jobs in worker**

Edit `app/tasks/worker.py`:

```python
# Add imports
from arq.cron import cron
from app.tasks.scheduled_jobs import check_review_deadlines_job, prune_admin_actions_job

# In WorkerSettings class, add cron_jobs:
    cron_jobs: list[cron] = [
        cron(check_review_deadlines_job, hour={0, 6, 12, 18}),  # Every 6 hours
        cron(prune_admin_actions_job, hour=2, minute=0),  # Daily at 2 AM
    ]
```

**Step 3: Verify imports**

Run: `uv run python -c "from app.tasks.scheduled_jobs import check_review_deadlines_job; print('OK')"`
Expected: Prints "OK"

**Step 4: Commit**

```bash
git add app/tasks/
git commit -m "feat: add scheduled jobs for reviews and maintenance"
```

---

## Summary

**What was accomplished:**

✅ Added arq dependency
✅ Created worker with job definitions
✅ Migrated all background tasks to arq jobs
✅ Removed polling hacks (proper job ordering)
✅ Added retry logic with exponential backoff
✅ Configured Docker Compose for worker
✅ Added tests and documentation
✅ Optional: Scheduled jobs for review system

**Benefits gained:**

✅ Jobs survive failures with automatic retries
✅ Job dependencies handled properly (no more sleep/polling)
✅ Full visibility into job status via logs and Redis
✅ Can scale workers independently from API
✅ Same Redis used for both caching and job queue

**What changed:**

- Image upload: `BackgroundTasks` → `enqueue_job()`
- Rating calc: `asyncio.create_task()` → `enqueue_job()`
- IQDB: Removed 10-second polling loop
- Added: Worker process in Docker Compose
- Added: Comprehensive logging throughout

**How to use:**

```python
# Enqueue job from API endpoint
await enqueue_job("create_thumbnail", image_id=123, source_path="/path/to/image.jpg")

# Job runs in worker with retries, proper logging, and error handling
```
