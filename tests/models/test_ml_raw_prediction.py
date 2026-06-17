from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions


def test_model_tablenames_and_fields():
    """Pure in-memory model test — no DB. The DB round-trip is in Task 2 (needs the
    migration: the test DB builds its schema from the Alembic chain, not metadata)."""
    assert MlExternalTags.__tablename__ == "ml_external_tags"
    assert MlModels.__tablename__ == "ml_models"
    assert MlRawPredictions.__tablename__ == "ml_raw_predictions"
    row = MlRawPredictions(image_id=1, model_id=2, external_tag_id=3, confidence=0.97)
    assert (row.image_id, row.model_id, row.external_tag_id) == (1, 2, 3)
    assert abs(row.confidence - 0.97) < 1e-6

    ext = MlExternalTags(name="long_hair", category=0)
    assert (ext.name, ext.category) == ("long_hair", 0)
    mdl = MlModels(name="caformer_b36.dbv4-full")
    assert mdl.name == "caformer_b36.dbv4-full"
