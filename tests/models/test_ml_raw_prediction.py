import pytest
from sqlalchemy import select

from app.models.image import Images
from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions
from app.models.user import Users


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


@pytest.mark.asyncio
async def test_raw_prediction_db_roundtrip(db_session):
    user = Users(
        username="rawpred",
        email="rawpred@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
    )
    db_session.add(user)
    await db_session.flush()

    img = Images(
        filename="rawpred-img",
        ext="jpg",
        user_id=user.user_id,
        md5_hash="rawpredhash",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(img)
    await db_session.flush()

    model = MlModels(name="caformer_b36.dbv4-full")
    tag = MlExternalTags(name="long_hair", category=0)
    db_session.add_all([model, tag])
    await db_session.flush()

    db_session.add(
        MlRawPredictions(
            image_id=img.image_id,
            model_id=model.id,
            external_tag_id=tag.id,
            confidence=0.97,
        )
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(MlRawPredictions).where(MlRawPredictions.image_id == img.image_id)
        )
    ).scalar_one()
    assert row.model_id == model.id and row.external_tag_id == tag.id
