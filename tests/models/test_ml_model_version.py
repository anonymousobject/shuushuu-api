# tests/models/test_ml_model_version.py

import pytest

from app.models.ml_model_version import MLModelVersion


@pytest.mark.asyncio
async def test_ml_model_version_creation(db_session):
    """Test creating an MLModelVersion instance"""
    model_version = MLModelVersion(
        model_name="custom_theme",
        version="v1",
        file_path="/shuushuu/ml_models/custom_theme/v1/model.onnx",
        is_active=True,
        metrics={"accuracy": 0.85, "precision": 0.82},
    )
    db_session.add(model_version)
    await db_session.commit()
    await db_session.refresh(model_version)

    assert model_version.version_id is not None
    assert model_version.is_active is True
    assert model_version.metrics["accuracy"] == 0.85


@pytest.mark.asyncio
async def test_unique_model_name_version(db_session):
    """Test unique constraint on (model_name, version)"""
    v1 = MLModelVersion(
        model_name="custom_theme",
        version="v1",
        file_path="/path/to/v1",
        is_active=True,
    )
    db_session.add(v1)
    await db_session.commit()

    # Duplicate
    v1_dup = MLModelVersion(
        model_name="custom_theme",
        version="v1",
        file_path="/path/to/v1_duplicate",
        is_active=False,
    )
    db_session.add(v1_dup)

    # Should raise IntegrityError due to UNIQUE constraint
    with pytest.raises(Exception):  # IntegrityError or similar
        await db_session.commit()
