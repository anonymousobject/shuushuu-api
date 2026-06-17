"""
Tests for the ML raw store service.

Exercises ``populate_external_tags(db, csv_path)`` against the real test
database. Idempotency is verified by running the function twice on the same
CSV and asserting the second call inserts nothing.
"""

import pytest
from sqlalchemy import select

from app.models.ml_raw_prediction import MlExternalTags
from app.services.ml_raw_store import populate_external_tags


async def test_populate_external_tags_idempotent(db_session, tmp_path):
    csv = tmp_path / "selected_tags.csv"
    csv.write_text("tag_id,name,category\n1,long_hair,0\n2,hatsune_miku,4\n")
    n1 = await populate_external_tags(db_session, csv)
    n2 = await populate_external_tags(db_session, csv)  # second run: no new rows
    rows = (await db_session.execute(select(MlExternalTags))).scalars().all()
    assert {(r.name, r.category) for r in rows} == {("long_hair", 0), ("hatsune_miku", 4)}
    assert n1 == 2 and n2 == 0
