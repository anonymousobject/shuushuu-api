"""Tests for ARQ worker configuration and job registration."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.tasks.worker import WorkerSettings, startup, shutdown


@pytest.mark.unit
class TestWorkerConfiguration:
    """Test worker configuration and job registration."""

    def test_worker_has_generate_tag_suggestions_registered(self):
        """Test that generate_tag_suggestions job is registered in worker functions."""
        # Check that generate_tag_suggestions is in the functions list
        function_names = [func.coroutine.__name__ for func in WorkerSettings.functions]
        assert "generate_tag_suggestions" in function_names, (
            "generate_tag_suggestions job should be registered in WorkerSettings.functions"
        )

    def test_worker_has_all_required_jobs(self):
        """Test that all required jobs are registered."""
        function_names = [func.coroutine.__name__ for func in WorkerSettings.functions]

        # Existing jobs
        assert "create_thumbnail_job" in function_names
        assert "create_variant_job" in function_names
        assert "add_to_iqdb_job" in function_names
        assert "recalculate_rating_job" in function_names

        # New job
        assert "generate_tag_suggestions" in function_names

    def test_generate_tag_suggestions_has_retry_config(self):
        """Test that generate_tag_suggestions has proper retry configuration."""
        # Find the generate_tag_suggestions function config
        tag_suggestion_func = None
        for func in WorkerSettings.functions:
            if func.coroutine.__name__ == "generate_tag_suggestions":
                tag_suggestion_func = func
                break

        assert tag_suggestion_func is not None, "generate_tag_suggestions should be registered"
        # Check that max_tries is set (should be 3 like other jobs)
        assert tag_suggestion_func.max_tries == 3


@pytest.mark.unit
@pytest.mark.asyncio
class TestWorkerLifecycle:
    """Test worker startup and shutdown with ML service."""

    async def test_startup_initializes_ml_service(self):
        """Test that worker startup initializes ML service in context."""
        # Arrange
        ctx = {}

        # Mock the MLTagSuggestionService
        with patch("app.tasks.worker.MLTagSuggestionService") as MockMLService:
            mock_ml_service = AsyncMock()
            MockMLService.return_value = mock_ml_service

            # Act
            await startup(ctx)

            # Assert
            assert "ml_service" in ctx, "ML service should be initialized in worker context"
            assert ctx["ml_service"] == mock_ml_service
            mock_ml_service.load_models.assert_called_once()

    async def test_shutdown_cleans_up_ml_service(self):
        """Test that worker shutdown cleans up ML service."""
        # Arrange
        mock_ml_service = AsyncMock()
        ctx = {"ml_service": mock_ml_service}

        # Act
        await shutdown(ctx)

        # Assert
        mock_ml_service.cleanup.assert_called_once()

    async def test_shutdown_handles_missing_ml_service(self):
        """Test that worker shutdown handles missing ML service gracefully."""
        # Arrange
        ctx = {}  # No ML service

        # Act & Assert - should not raise
        await shutdown(ctx)


@pytest.mark.unit
@pytest.mark.asyncio
class TestJobEnqueuing:
    """Test that tag suggestion job can be enqueued."""

    async def test_can_enqueue_generate_tag_suggestions(self):
        """Test that generate_tag_suggestions job can be enqueued via arq."""
        from app.tasks.queue import enqueue_job

        # Arrange
        image_id = 123

        # Mock the arq pool
        with patch("app.tasks.queue.get_queue") as mock_get_queue:
            mock_pool = AsyncMock()
            mock_job = MagicMock()
            mock_job.job_id = "test-job-id"
            mock_pool.enqueue_job.return_value = mock_job
            mock_get_queue.return_value = mock_pool

            # Act
            job_id = await enqueue_job("generate_tag_suggestions", image_id=image_id)

            # Assert
            assert job_id == "test-job-id"
            mock_pool.enqueue_job.assert_called_once()
            # Check that the function name is correct
            call_args = mock_pool.enqueue_job.call_args
            assert call_args[0][0] == "generate_tag_suggestions"
            assert call_args[1]["image_id"] == image_id
