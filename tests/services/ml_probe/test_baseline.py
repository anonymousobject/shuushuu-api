"""Mapping-path reconstruction from the raw store (app/services/ml_probe/baseline.py)."""

import numpy as np
import pytest

from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions
from app.models.tag_mapping import TagMappings
from app.services.ml_probe.baseline import BaselineScores, build_baseline_scores
from app.services.ml_probe.labels import load_theme_tag_graph
from tests.services.ml_probe.helpers import graph_fixture, make_image, make_user

MODEL = "probe-test-model"


async def _raw_fixture(db_session, child, alias):
    model = MlModels(name=MODEL)
    db_session.add(model)
    externals = {name: MlExternalTags(name=name, category=0) for name in ("e1", "e2", "e3")}
    db_session.add_all(externals.values())
    await db_session.flush()
    db_session.add_all(
        [
            TagMappings(external_tag="e1", internal_tag_id=child.tag_id, confidence=1.0),
            TagMappings(external_tag="e2", internal_tag_id=alias.tag_id, confidence=0.5),
            TagMappings(external_tag="e4", internal_tag_id=None, confidence=1.0),  # ignored
        ]
    )
    await db_session.flush()
    return model, externals


async def _raw(db_session, model, external, image, confidence):
    db_session.add(
        MlRawPredictions(
            image_id=image.image_id,
            model_id=model.id,
            external_tag_id=external.id,
            confidence=confidence,
        )
    )
    await db_session.flush()


async def test_build_baseline_scores_maps_and_aggregates(db_session):
    parent, child, alias, _ = await graph_fixture(db_session)
    model, ext = await _raw_fixture(db_session, child, alias)
    user = await make_user(db_session, "baseline")
    img1 = await make_image(db_session, user, 1)
    img3 = await make_image(db_session, user, 3)
    await _raw(db_session, model, ext["e1"], img1, 0.8)
    await _raw(db_session, model, ext["e2"], img1, 0.9)  # 0.9 * 0.5 = 0.45 < 0.8, loses the max
    await _raw(db_session, model, ext["e3"], img1, 0.99)  # unmapped
    await _raw(db_session, model, ext["e1"], img3, 0.7)  # not requested

    graph = await load_theme_tag_graph(db_session)
    baseline = await build_baseline_scores(
        db_session, graph, model_name=MODEL, image_ids=np.array([img1.image_id])
    )

    col_parent, col_child = graph.column_of[parent.tag_id], graph.column_of[child.tag_id]
    assert baseline.image_ids.tolist() == [img1.image_id]
    assert baseline.tag_ids.tolist() == graph.tag_ids.tolist()
    assert baseline.model == MODEL
    assert baseline.scores.shape == (1, len(graph.tag_ids))
    assert baseline.scores.dtype == np.float32
    assert baseline.scores[0, col_child] == pytest.approx(0.8)
    assert baseline.scores[0, col_parent] == 0.0, "not closed upward here; compare() does that"
    assert baseline.has_raw.tolist() == [True]


async def test_baseline_alias_target_lands_on_canonical_column(db_session):
    parent, child, alias, _ = await graph_fixture(db_session)
    model, ext = await _raw_fixture(db_session, child, alias)
    user = await make_user(db_session, "alias")
    img = await make_image(db_session, user, 1)
    await _raw(db_session, model, ext["e2"], img, 0.9)  # maps to the alias of child, conf 0.5

    graph = await load_theme_tag_graph(db_session)
    baseline = await build_baseline_scores(
        db_session, graph, model_name=MODEL, image_ids=np.array([img.image_id])
    )

    assert baseline.scores[0, graph.column_of[child.tag_id]] == pytest.approx(0.45)


async def test_baseline_has_raw_false_for_unprocessed_image(db_session):
    _, child, alias, _ = await graph_fixture(db_session)
    model, ext = await _raw_fixture(db_session, child, alias)
    user = await make_user(db_session, "noraw")
    with_rows = await make_image(db_session, user, 1)
    without_rows = await make_image(db_session, user, 2)
    await _raw(db_session, model, ext["e1"], with_rows, 0.8)

    graph = await load_theme_tag_graph(db_session)
    baseline = await build_baseline_scores(
        db_session,
        graph,
        model_name=MODEL,
        image_ids=np.array([without_rows.image_id, with_rows.image_id]),
    )

    assert baseline.image_ids.tolist() == sorted([with_rows.image_id, without_rows.image_id])
    row_without = baseline.image_ids.tolist().index(without_rows.image_id)
    assert not baseline.has_raw[row_without]
    assert baseline.scores[row_without].max() == 0.0


async def test_build_baseline_scores_unknown_model_raises(db_session):
    await graph_fixture(db_session)
    graph = await load_theme_tag_graph(db_session)

    with pytest.raises(ValueError, match="no-such-model"):
        await build_baseline_scores(
            db_session, graph, model_name="no-such-model", image_ids=np.array([1])
        )


@pytest.mark.unit
def test_baseline_scores_save_load_round_trip(tmp_path):
    baseline = BaselineScores(
        image_ids=np.array([3, 7], dtype=np.int64),
        scores=np.array([[0.5, 0.0], [0.0, 0.25]], dtype=np.float32),
        has_raw=np.array([True, False]),
        tag_ids=np.array([100, 101], dtype=np.int64),
        model="m",
    )
    baseline.save(tmp_path / "b.npz")
    loaded = BaselineScores.load(tmp_path / "b.npz")
    assert loaded.image_ids.tolist() == [3, 7]
    assert loaded.tag_ids.tolist() == [100, 101]
    assert loaded.model == "m"
    assert np.allclose(loaded.scores, baseline.scores)
    assert loaded.has_raw.tolist() == [True, False]


@pytest.mark.unit
def test_baseline_save_adds_npz_suffix(tmp_path):
    baseline = BaselineScores(
        image_ids=np.array([3], dtype=np.int64),
        scores=np.zeros((1, 1), dtype=np.float32),
        has_raw=np.array([True]),
        tag_ids=np.array([100], dtype=np.int64),
        model="m",
    )
    baseline.save(tmp_path / "b")
    assert (tmp_path / "b.npz").exists()
    assert BaselineScores.load(tmp_path / "b").model == "m"
