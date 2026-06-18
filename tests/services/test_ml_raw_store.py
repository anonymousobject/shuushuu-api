"""
Tests for the ML raw store service.

Exercises ``populate_external_tags(db, csv_path)`` and
``ingest_raw_predictions(db, records)`` against the real test database.
Idempotency is verified by running each function twice and asserting the
second call inserts nothing.
"""

import pytest
from sqlalchemy import select

from app.models.image import Images
from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions
from app.models.user import Users
from app.services.ml_raw_store import ingest_raw_predictions, populate_external_tags


async def test_populate_external_tags_idempotent(db_session, tmp_path):
    csv = tmp_path / "selected_tags.csv"
    csv.write_text("tag_id,name,category\n1,long_hair,0\n2,hatsune_miku,4\n")
    n1 = await populate_external_tags(db_session, csv)
    n2 = await populate_external_tags(db_session, csv)  # second run: no new rows
    rows = (await db_session.execute(select(MlExternalTags))).scalars().all()
    assert {(r.name, r.category) for r in rows} == {("long_hair", 0), ("hatsune_miku", 4)}
    assert n1 == 2 and n2 == 0


async def _make_user(db_session, suffix: str) -> Users:
    user = Users(
        username=f"rawstore_{suffix}",
        email=f"rawstore_{suffix}@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_image(db_session, user: Users, suffix: str) -> Images:
    image = Images(
        filename=f"2024-01-01-{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"rawstore_hash_{suffix}",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.flush()
    return image


async def test_ingest_raw_predictions(db_session, tmp_path):
    # Seed image + external-tag dict.
    csv = tmp_path / "selected_tags.csv"
    csv.write_text("tag_id,name,category\n1,long_hair,0\n2,hatsune_miku,4\n")
    await populate_external_tags(db_session, csv)

    user = await _make_user(db_session, "ingest")
    img = await _make_image(db_session, user, "ingest")
    await db_session.commit()

    records = [
        {
            "image_id": img.image_id,
            "predictions": [
                {
                    "external_tag": "long_hair",
                    "confidence": 0.9,
                    "model_version": "caformer_b36.dbv4-full",
                    "category": 0,
                },
                {
                    "external_tag": "hatsune_miku",
                    "confidence": 0.8,
                    "model_version": "caformer_b36.dbv4-full",
                    "category": 4,
                },
            ],
        }
    ]

    created = await ingest_raw_predictions(db_session, records)
    assert created == 2

    # Idempotent: re-running inserts nothing.
    again = await ingest_raw_predictions(db_session, records)
    assert again == 0

    # Verify rows exist in the DB.
    rows = (await db_session.execute(select(MlRawPredictions))).scalars().all()
    assert len(rows) == 2
    assert {r.confidence for r in rows} == {0.9, 0.8}

    # Unknown external_tag is skipped — must not error.
    unknown_records = [
        {
            "image_id": img.image_id,
            "predictions": [
                {
                    "external_tag": "no_such_tag_xyz",
                    "confidence": 0.7,
                    "model_version": "caformer_b36.dbv4-full",
                    "category": 0,
                },
            ],
        }
    ]
    skipped = await ingest_raw_predictions(db_session, unknown_records)
    assert skipped == 0

    # Verify the model was upserted.
    models = (await db_session.execute(select(MlModels))).scalars().all()
    assert len(models) == 1
    assert models[0].name == "caformer_b36.dbv4-full"


async def test_ingest_coexists_across_models(db_session, tmp_path):
    """The composite PK (image_id, model_id, external_tag_id) means the same
    (image, external_tag) predicted by TWO different models coexists as two
    distinct rows in ml_raw_predictions.

    Specifically:
    - ingest caformer prediction for long_hair → created == 1
    - ingest swinv2 prediction for the same image+long_hair → created == 1 (NOT 0)
    - the store now holds 2 MlRawPredictions rows for that image, same
      external_tag_id but two distinct model_ids, and ml_models has both rows.
    """
    csv = tmp_path / "selected_tags.csv"
    csv.write_text("tag_id,name,category\n1,long_hair,0\n")
    await populate_external_tags(db_session, csv)

    user = await _make_user(db_session, "multimodel")
    img = await _make_image(db_session, user, "multimodel")
    await db_session.commit()

    caformer_records = [
        {
            "image_id": img.image_id,
            "predictions": [
                {
                    "external_tag": "long_hair",
                    "confidence": 0.9,
                    "model_version": "caformer_b36.dbv4-full",
                    "category": 0,
                },
            ],
        }
    ]
    created = await ingest_raw_predictions(db_session, caformer_records)
    assert created == 1

    swinv2_records = [
        {
            "image_id": img.image_id,
            "predictions": [
                {
                    "external_tag": "long_hair",
                    "confidence": 0.85,
                    "model_version": "swinv2_base_window8_256.dbv4-full",
                    "category": 0,
                },
            ],
        }
    ]
    # Different model → different composite-PK row → NOT idempotent-skipped
    created2 = await ingest_raw_predictions(db_session, swinv2_records)
    assert created2 == 1, "same (image, external_tag) under a different model must be a new row"

    raw_rows = (
        await db_session.execute(
            select(MlRawPredictions).where(MlRawPredictions.image_id == img.image_id)
        )
    ).scalars().all()
    assert len(raw_rows) == 2, "one row per (image, model, external_tag)"

    # Same external_tag_id, distinct model_ids
    ext_tag_ids = {r.external_tag_id for r in raw_rows}
    assert len(ext_tag_ids) == 1, "both rows reference the same external_tag_id"
    model_ids = {r.model_id for r in raw_rows}
    assert len(model_ids) == 2, "each row must have a distinct model_id"

    # ml_models must have both model rows
    models = (await db_session.execute(select(MlModels))).scalars().all()
    model_names = {m.name for m in models}
    assert "caformer_b36.dbv4-full" in model_names
    assert "swinv2_base_window8_256.dbv4-full" in model_names
