"""Tests for arq worker configuration, job registration, and lifecycle."""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.tasks.worker import WorkerSettings, shutdown, startup


@pytest.fixture
def _no_meilisearch():
    """Make the meilisearch startup block degrade gracefully without a server.

    Main's worker startup initializes meilisearch first; it is wrapped in a
    try/except that logs a warning and continues when meilisearch is
    unreachable. Patching the client constructor to raise reproduces that
    "unavailable" path deterministically (no network), so these lifecycle
    tests can focus on the ML wiring.
    """
    with patch(
        "meilisearch_python_sdk.AsyncClient",
        side_effect=RuntimeError("meilisearch unavailable (test)"),
    ):
        yield


@pytest.mark.unit
class TestWorkerConfiguration:
    """Job registration in WorkerSettings.functions."""

    def test_generate_ml_tag_suggestions_registered(self):
        function_names = [func.coroutine.__name__ for func in WorkerSettings.functions]
        assert "generate_ml_tag_suggestions" in function_names, (
            "generate_ml_tag_suggestions should be registered in WorkerSettings.functions"
        )

    def test_existing_jobs_still_registered(self):
        function_names = [func.coroutine.__name__ for func in WorkerSettings.functions]
        for name in (
            "create_thumbnail_job",
            "create_variant_job",
            "add_to_iqdb_job",
            "recalculate_rating_job",
        ):
            assert name in function_names

    def test_generate_ml_tag_suggestions_has_retry_config(self):
        func_cfg = next(
            (
                f
                for f in WorkerSettings.functions
                if f.coroutine.__name__ == "generate_ml_tag_suggestions"
            ),
            None,
        )
        assert func_cfg is not None
        assert func_cfg.max_tries == 3


@pytest.mark.unit
class TestWorkerLifecycle:
    """Startup/shutdown ML service wiring, gated by the feature flag."""

    async def test_startup_skips_ml_service_when_flag_off(self, _no_meilisearch, monkeypatch):
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", False)
        ctx: dict = {}

        await startup(ctx)

        assert "ml_service" not in ctx

    async def test_startup_loads_ml_service_when_flag_on(self, _no_meilisearch, monkeypatch):
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
        ctx: dict = {}

        fake_service = AsyncMock()
        fake_service.model_name = "fake-model"
        with patch("app.services.ml_service.MLTagSuggestionService", return_value=fake_service):
            await startup(ctx)

        assert ctx.get("ml_service") is fake_service
        fake_service.load_models.assert_awaited_once()

    async def test_startup_raises_when_flag_on_and_model_files_missing(
        self, _no_meilisearch, monkeypatch, tmp_path
    ):
        """Flag on but model files absent → worker must fail to start."""
        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
        # Point at an empty dir so the real load_models() finds no model files.
        monkeypatch.setattr(settings, "ML_MODELS_PATH", str(tmp_path))
        monkeypatch.setattr(settings, "ML_MODEL_NAME", "wd-swinv2-tagger-v3")

        with pytest.raises(FileNotFoundError):
            await startup({})

    async def test_shutdown_cleans_up_ml_service(self):
        fake_service = AsyncMock()
        ctx = {"ml_service": fake_service}

        await shutdown(ctx)

        fake_service.cleanup.assert_awaited_once()

    async def test_shutdown_without_ml_service_does_not_raise(self):
        await shutdown({})
