"""Tests for the ML bulk-backfill helpers.

Pure helpers (path/shard/JSONL) are tested directly. The DB helpers
(fetch_manifest_rows, ingest_results) run against the real test database;
ingest_results reuses store_predictions, whose mapping/resolution boundary is
patched here exactly as in test_ml_suggestion_pipeline.py.
"""

from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.image import Images, ImageStatus
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.user import Users
from app.services.ml_backfill import (
    check_shard_output,
    fetch_manifest_rows,
    ingest_results,
    load_image_ids,
    select_shard,
    variant_relpath,
    write_results,
)

PIPELINE = "app.services.ml_suggestion_pipeline"


# --- pure helpers ---------------------------------------------------------


def test_variant_relpath_thumbs_uses_webp():
    assert variant_relpath("thumbs", "2024-01-01-1", "jpg") == "thumbs/2024-01-01-1.webp"


def test_variant_relpath_other_variants_keep_ext():
    assert variant_relpath("fullsize", "abc", "png") == "fullsize/abc.png"
    assert variant_relpath("medium", "abc", "jpeg") == "medium/abc.jpeg"


def test_select_shard_partitions_records_disjointly():
    records = list(range(10))
    s0 = select_shard(records, shards=3, index=0)
    s1 = select_shard(records, shards=3, index=1)
    s2 = select_shard(records, shards=3, index=2)
    assert s0 == [0, 3, 6, 9]
    assert s1 == [1, 4, 7]
    assert s2 == [2, 5, 8]
    # Disjoint and complete.
    assert sorted(s0 + s1 + s2) == records


def test_select_shard_single_shard_returns_all():
    records = [1, 2, 3]
    assert select_shard(records, shards=1, index=0) == records


def test_select_shard_validates_arguments():
    with pytest.raises(ValueError):
        select_shard([1], shards=0, index=0)
    with pytest.raises(ValueError):
        select_shard([1], shards=2, index=2)


def test_jsonl_roundtrip_and_resume_ids(tmp_path):
    out = tmp_path / "results.jsonl"
    rows = [
        {"image_id": 1, "predictions": [{"external_tag": "a", "confidence": 0.9, "model_version": "v3"}]},
        {"image_id": 2, "predictions": []},
    ]
    write_results(out, rows)
    write_results(out, [{"image_id": 3, "predictions": []}])  # appends
    assert load_image_ids(out) == {1, 2, 3}


def test_load_image_ids_missing_file_is_empty(tmp_path):
    assert load_image_ids(tmp_path / "nope.jsonl") == set()


def test_iter_results_tolerates_malformed_lines(tmp_path):
    """A truncated/corrupt line (e.g. from a hard kill mid-write) is skipped, not fatal."""
    out = tmp_path / "results.jsonl"
    out.write_text(
        '{"image_id": 1, "predictions": []}\n'
        '{"image_id": 2, "predictions": [  <- truncated\n'  # garbage / partial line
        '{"image_id": 3, "predictions": []}\n'
    )
    # Resume must recover the intact records and ignore the broken one.
    assert load_image_ids(out) == {1, 3}


def test_check_shard_output_creates_then_accepts_matching(tmp_path):
    out = tmp_path / "r0.jsonl"
    check_shard_output(out, shards=2, shard_index=0)  # first run writes the binding
    # Resuming the same shard into the same file is fine.
    check_shard_output(out, shards=2, shard_index=0)


def test_check_shard_output_rejects_mismatch(tmp_path):
    out = tmp_path / "r0.jsonl"
    check_shard_output(out, shards=2, shard_index=0)
    with pytest.raises(ValueError):
        check_shard_output(out, shards=2, shard_index=1)  # wrong shard index
    with pytest.raises(ValueError):
        check_shard_output(out, shards=4, shard_index=0)  # wrong shard count


# --- DB helpers -----------------------------------------------------------


async def _make_user(db_session, suffix: str) -> Users:
    user = Users(
        username=f"backfill_{suffix}",
        email=f"backfill_{suffix}@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_image(
    db_session, user: Users, suffix: str, *, has_theme: bool = False, status: int = ImageStatus.ACTIVE
) -> Images:
    image = Images(
        filename=f"2024-01-01-{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"hash_{suffix}",
        filesize=1024,
        width=800,
        height=600,
        has_theme=has_theme,
        status=status,
    )
    db_session.add(image)
    await db_session.flush()
    return image


async def test_fetch_manifest_missing_theme_and_status(db_session):
    """Default scoping returns active, theme-less images; honours flags."""
    user = await _make_user(db_session, "manifest")
    a = await _make_image(db_session, user, "a", has_theme=False, status=ImageStatus.ACTIVE)
    await _make_image(db_session, user, "b", has_theme=True, status=ImageStatus.ACTIVE)
    await _make_image(db_session, user, "c", has_theme=False, status=ImageStatus.REVIEW)
    await db_session.flush()

    rows = await fetch_manifest_rows(
        db_session, status=ImageStatus.ACTIVE, missing_theme=True, exclude_existing=False
    )
    ids = {r["image_id"] for r in rows}
    assert ids == {a.image_id}  # b has theme, c is not active
    assert rows[0]["filename"] == a.filename
    assert rows[0]["ext"] == a.ext


async def test_fetch_manifest_excludes_images_with_existing_suggestions(db_session):
    user = await _make_user(db_session, "exclude")
    a = await _make_image(db_session, user, "a")
    b = await _make_image(db_session, user, "b")
    tag = Tags(tag_id=46, title="long hair")
    db_session.add(tag)
    await db_session.flush()
    db_session.add(
        MlTagSuggestions(
            image_id=b.image_id, tag_id=46, confidence=0.9, model_version="v3", status="pending"
        )
    )
    await db_session.flush()

    rows = await fetch_manifest_rows(db_session, missing_theme=False, exclude_existing=True)
    ids = {r["image_id"] for r in rows}
    assert a.image_id in ids
    assert b.image_id not in ids


async def test_fetch_manifest_all_statuses_and_limit(db_session):
    user = await _make_user(db_session, "all")
    await _make_image(db_session, user, "a", status=ImageStatus.ACTIVE)
    await _make_image(db_session, user, "b", status=ImageStatus.REVIEW)
    await db_session.flush()

    rows = await fetch_manifest_rows(db_session, status=None, missing_theme=False)
    assert len({r["image_id"] for r in rows}) >= 2

    limited = await fetch_manifest_rows(db_session, status=None, missing_theme=False, limit=1)
    assert len(limited) == 1


def _fixed_resolver(mapped: list[dict[str, Any]]):
    async def _r(db, suggestions):
        return [dict(m) for m in mapped]

    return _r


async def _passthrough(db, suggestions):
    return suggestions


@pytest.mark.needs_commit
async def test_ingest_results_creates_rows(db_session):
    user = await _make_user(db_session, "ingest")
    img1 = await _make_image(db_session, user, "i1")
    img2 = await _make_image(db_session, user, "i2")
    db_session.add(Tags(tag_id=46, title="long hair"))
    await db_session.commit()

    results = [
        {"image_id": img1.image_id, "predictions": [{"external_tag": "long_hair", "confidence": 0.9, "model_version": "v3"}]},
        {"image_id": img2.image_id, "predictions": [{"external_tag": "long_hair", "confidence": 0.9, "model_version": "v3"}]},
    ]
    mapped = [{"tag_id": 46, "confidence": 0.9, "model_version": "v3"}]

    seen: list[int] = []
    with (
        patch(f"{PIPELINE}.resolve_external_tags", _fixed_resolver(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough),
    ):
        stats = await ingest_results(db_session, results, on_processed=seen.append)

    assert stats.processed == 2
    assert stats.created == 2
    assert stats.errors == []
    assert seen == [img1.image_id, img2.image_id]
    rows = await db_session.execute(select(MlTagSuggestions))
    assert {s.image_id for s in rows.scalars().all()} == {img1.image_id, img2.image_id}


@pytest.mark.needs_commit
async def test_ingest_results_skips_given_ids(db_session):
    user = await _make_user(db_session, "skip")
    img1 = await _make_image(db_session, user, "s1")
    img2 = await _make_image(db_session, user, "s2")
    db_session.add(Tags(tag_id=46, title="long hair"))
    await db_session.commit()

    results = [
        {"image_id": img1.image_id, "predictions": [{"external_tag": "long_hair", "confidence": 0.9, "model_version": "v3"}]},
        {"image_id": img2.image_id, "predictions": [{"external_tag": "long_hair", "confidence": 0.9, "model_version": "v3"}]},
    ]
    mapped = [{"tag_id": 46, "confidence": 0.9, "model_version": "v3"}]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _fixed_resolver(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough),
    ):
        stats = await ingest_results(db_session, results, skip_ids={img1.image_id})

    assert stats.skipped == 1
    assert stats.processed == 1


@pytest.mark.needs_commit
async def test_ingest_results_records_errors_and_continues(db_session):
    """A bad image_id (FK violation) is recorded; remaining images still ingest."""
    user = await _make_user(db_session, "err")
    good = await _make_image(db_session, user, "good")
    db_session.add(Tags(tag_id=46, title="long hair"))
    await db_session.commit()
    good_id = good.image_id  # capture before ingest: the rollback below expires `good`

    results = [
        {"image_id": 99999999, "predictions": [{"external_tag": "long_hair", "confidence": 0.9, "model_version": "v3"}]},
        {"image_id": good_id, "predictions": [{"external_tag": "long_hair", "confidence": 0.9, "model_version": "v3"}]},
    ]
    mapped = [{"tag_id": 46, "confidence": 0.9, "model_version": "v3"}]

    with (
        patch(f"{PIPELINE}.resolve_external_tags", _fixed_resolver(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough),
    ):
        stats = await ingest_results(db_session, results)

    assert stats.processed == 1
    assert stats.created == 1
    assert len(stats.errors) == 1
    rows = await db_session.execute(select(MlTagSuggestions))
    assert {s.image_id for s in rows.scalars().all()} == {good_id}
