"""Unit tests for MLTagSuggestionService.

These tests exercise real service logic (path resolution, category filtering,
output shaping) against a protocol-compatible fake at the inference boundary.
They do NOT test MockModel behaviour — MockModel was removed from the service.
"""

from pathlib import Path
from typing import Any

import pytest

from app.services.ml_service import MLTagSuggestionService


class FakeTaggingModel:
    """Protocol-compatible fake that records call arguments and returns canned predictions."""

    def __init__(self, predictions: list[dict[str, Any]] | None = None) -> None:
        self.last_include_categories: set[int] | None = None
        self.last_min_confidence: float | None = None
        self.cleanup_called: bool = False
        self._predictions = predictions or [
            {"tag": "smile", "confidence": 0.91, "category": 0},
            {"tag": "blue_hair", "confidence": 0.80, "category": 0},
        ]

    async def predict(
        self,
        image_path: str,
        min_confidence: float = 0.35,
        include_categories: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        self.last_include_categories = include_categories
        self.last_min_confidence = min_confidence
        return self._predictions

    async def cleanup(self) -> None:
        self.cleanup_called = True


@pytest.mark.unit
class TestMLTagSuggestionServiceLoadModels:
    """Tests for load_models() fail-fast behaviour."""

    async def test_load_models_raises_file_not_found_for_wd_tagger(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing WD-Tagger files → FileNotFoundError naming the expected path."""
        monkeypatch.setattr("app.services.ml_service.settings.ML_MODELS_PATH", str(tmp_path))
        monkeypatch.setattr(
            "app.services.ml_service.settings.ML_MODEL_NAME", "wd-swinv2-tagger-v3"
        )

        service = MLTagSuggestionService()
        with pytest.raises(FileNotFoundError, match=str(tmp_path)):
            await service.load_models()

    async def test_load_models_raises_file_not_found_for_animetimm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing animetimm model files → FileNotFoundError naming the expected path."""
        monkeypatch.setattr("app.services.ml_service.settings.ML_MODELS_PATH", str(tmp_path))
        monkeypatch.setattr(
            "app.services.ml_service.settings.ML_MODEL_NAME",
            "swinv2_base_window8_256.dbv4-full",
        )

        service = MLTagSuggestionService()
        with pytest.raises(FileNotFoundError, match=str(tmp_path)):
            await service.load_models()

    async def test_load_models_raises_value_error_for_unknown_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unrecognized ML_MODEL_NAME → ValueError (no mock fallback)."""
        monkeypatch.setattr(
            "app.services.ml_service.settings.ML_MODEL_NAME", "totally_unknown_model_xyz"
        )

        service = MLTagSuggestionService()
        with pytest.raises(ValueError, match="totally_unknown_model_xyz"):
            await service.load_models()

    async def test_caformer_name_routes_to_animetimm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """caformer* model names route to the animetimm loader, not ValueError."""
        monkeypatch.setattr("app.services.ml_service.settings.ML_MODELS_PATH", str(tmp_path))
        monkeypatch.setattr(
            "app.services.ml_service.settings.ML_MODEL_NAME", "caformer_b36.dbv4-full"
        )

        service = MLTagSuggestionService()
        with pytest.raises(FileNotFoundError, match=str(tmp_path)):
            await service.load_models()


@pytest.mark.unit
class TestMLTagSuggestionServiceGenerateSuggestions:
    """Tests for generate_suggestions() output shaping and category filtering."""

    async def test_generate_suggestions_without_model_raises_runtime_error(self) -> None:
        """Calling generate_suggestions before any model is set raises RuntimeError."""
        service = MLTagSuggestionService()
        with pytest.raises(RuntimeError, match="load_models"):
            await service.generate_suggestions("/some/image.jpg")

    async def test_generate_suggestions_passes_only_general_category(self) -> None:
        """v1 themes-only: predict is called with include_categories={GENERAL_CATEGORY}."""
        from app.services.onnx_model import GENERAL_CATEGORY

        service = MLTagSuggestionService()
        fake = FakeTaggingModel()
        service.model = fake  # type: ignore[assignment]
        service._model_name = "wd-swinv2-tagger-v3"

        await service.generate_suggestions("/fake/image.jpg")

        assert fake.last_include_categories == {GENERAL_CATEGORY}

    async def test_generate_suggestions_output_keys(self) -> None:
        """Output dicts must contain external_tag, confidence, model_version — no model_source."""
        service = MLTagSuggestionService()
        fake = FakeTaggingModel()
        service.model = fake  # type: ignore[assignment]
        service._model_name = "wd-swinv2-tagger-v3"

        results = await service.generate_suggestions("/fake/image.jpg")

        assert len(results) > 0
        for suggestion in results:
            assert "external_tag" in suggestion
            assert "confidence" in suggestion
            assert "model_version" in suggestion
            assert "model_source" not in suggestion

    async def test_generate_suggestions_model_version_matches_model_name(self) -> None:
        """model_version in output must equal the loaded model name."""
        service = MLTagSuggestionService()
        fake = FakeTaggingModel()
        service.model = fake  # type: ignore[assignment]
        service._model_name = "wd-swinv2-tagger-v3"

        results = await service.generate_suggestions("/fake/image.jpg")

        for suggestion in results:
            assert suggestion["model_version"] == "wd-swinv2-tagger-v3"

    async def test_generate_suggestions_animetimm_passes_animetimm_general_category(self) -> None:
        """For an AnimetimmModel-typed injection, passes ANIMETIMM_GENERAL only."""
        from app.services.animetimm_model import GENERAL_CATEGORY as ANIMETIMM_GENERAL
        from app.services.animetimm_model import AnimetimmModel

        service = MLTagSuggestionService()

        # Create an unloaded AnimetimmModel instance (no files needed for this test)
        animetimm_fake = AnimetimmModel.__new__(AnimetimmModel)
        called_with: list[set[int] | None] = []

        async def fake_predict(
            image_path: str,
            min_confidence: float = 0.35,
            include_categories: set[int] | None = None,
        ) -> list[dict[str, Any]]:
            called_with.append(include_categories)
            return [{"tag": "landscape", "confidence": 0.85, "category": 0}]

        animetimm_fake.predict = fake_predict  # type: ignore[method-assign]
        service.model = animetimm_fake
        service._model_name = "swinv2_base_window8_256.dbv4-full"

        await service.generate_suggestions("/fake/image.jpg")

        assert called_with == [{ANIMETIMM_GENERAL}]


@pytest.mark.unit
class TestMLTagSuggestionServiceGenerateRawPredictions:
    """Tests for generate_raw_predictions() multi-category raw output."""

    async def test_generate_raw_predictions_shape(self) -> None:
        """Raw predictions rename tag→external_tag, keep category, stamp model_version, and
        forward include_categories + min_confidence to the model."""
        from app.services.animetimm_model import CHARACTER_CATEGORY, GENERAL_CATEGORY

        svc = MLTagSuggestionService()
        svc._model_name = "caformer_b36.dbv4-full"
        fake = FakeTaggingModel(
            [
                {"tag": "long_hair", "confidence": 0.9, "category": 0},
                {"tag": "hatsune_miku", "confidence": 0.8, "category": 4},
            ]
        )
        svc.model = fake  # type: ignore[assignment]

        out = await svc.generate_raw_predictions(
            "x.jpg",
            include_categories={GENERAL_CATEGORY, CHARACTER_CATEGORY},
            min_confidence=0.35,
        )
        assert out == [
            {
                "external_tag": "long_hair",
                "confidence": 0.9,
                "category": 0,
                "model_version": "caformer_b36.dbv4-full",
            },
            {
                "external_tag": "hatsune_miku",
                "confidence": 0.8,
                "category": 4,
                "model_version": "caformer_b36.dbv4-full",
            },
        ]
        # the model received exactly what the caller passed (no defaulting/munging)
        assert fake.last_include_categories == {GENERAL_CATEGORY, CHARACTER_CATEGORY}
        assert fake.last_min_confidence == 0.35

    async def test_generate_raw_predictions_without_model_raises_runtime_error(self) -> None:
        """Calling generate_raw_predictions before any model is set raises RuntimeError."""
        from app.services.animetimm_model import CHARACTER_CATEGORY, GENERAL_CATEGORY

        svc = MLTagSuggestionService()
        with pytest.raises(RuntimeError, match="load_models"):
            await svc.generate_raw_predictions(
                "/some/image.jpg",
                include_categories={GENERAL_CATEGORY, CHARACTER_CATEGORY},
                min_confidence=0.35,
            )


@pytest.mark.unit
class TestMLTagSuggestionServiceCleanup:
    """Tests for cleanup() resource teardown."""

    async def test_cleanup_calls_model_cleanup_and_clears_reference(self) -> None:
        """After cleanup(), the fake's cleanup was awaited and service.model is None."""
        service = MLTagSuggestionService()
        fake = FakeTaggingModel()
        service.model = fake  # type: ignore[assignment]

        await service.cleanup()

        assert fake.cleanup_called is True
        assert service.model is None

    async def test_cleanup_on_unloaded_service_is_a_noop(self) -> None:
        """cleanup() on a never-loaded service (model is None) does not raise."""
        service = MLTagSuggestionService()
        assert service.model is None

        await service.cleanup()  # must not raise

        assert service.model is None
