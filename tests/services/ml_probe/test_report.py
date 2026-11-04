"""Probe-vs-mapping comparison under label masks (app/services/ml_probe/report.py)."""

import csv

import numpy as np
import pytest

from app.services.ml_probe.labels import APPROVED, LINKED, NONE, REJECTED
from app.services.ml_probe.metrics import TagMetrics
from app.services.ml_probe.report import (
    FIXED_BASELINE_THRESHOLD,
    MIN_SUPPORT,
    WIN_GAP,
    Comparison,
    View,
    compare,
    newest_rows,
    render_markdown,
    reviewed_view,
    select_view_thresholds,
    write_csv,
)

T, F = True, False


def _compare(*, probe, baseline, truth, has_raw=None, mask=None, parent_index=None, thr=0.5):
    probe = np.asarray(probe, dtype=np.float32)
    baseline = np.asarray(baseline, dtype=np.float32)
    truth = np.asarray(truth, dtype=bool)
    n, k = truth.shape
    return compare(
        probe_scores=probe,
        baseline_scores=baseline,
        truth=truth,
        has_raw=np.ones(n, dtype=bool) if has_raw is None else np.asarray(has_raw, dtype=bool),
        view=View(name="headline/all", rows=np.arange(n), mask=mask),
        probe_thresholds=np.full(k, thr, dtype=np.float32),
        baseline_best_thresholds=np.full(k, thr, dtype=np.float32),
        parent_index=np.full(k, -1) if parent_index is None else np.asarray(parent_index),
        tag_ids=np.arange(100, 100 + k),
        titles=[f"t{i}" for i in range(k)],
    )


@pytest.mark.unit
def test_constants():
    assert (MIN_SUPPORT, WIN_GAP, FIXED_BASELINE_THRESHOLD) == (20, 0.05, 0.35)


@pytest.mark.unit
def test_compare_drops_rows_without_raw():
    # image 1 has no raw rows and would be a false positive for both systems
    result = _compare(
        probe=[[0.9], [0.9]], baseline=[[0.9], [0.9]], truth=[[T], [F]], has_raw=[T, F]
    )

    assert result.per_tag["probe"].fp.tolist() == [0]
    assert result.per_tag["baseline_best"].fp.tolist() == [0]
    assert result.per_tag["baseline_fixed"].fp.tolist() == [0]
    assert result.support.tolist() == [1]
    assert result.summary()["n_images"] == 1


@pytest.mark.unit
def test_compare_applies_mask_to_both_systems():
    mask = np.array([[F], [T]])  # pair (image 1, tag 0) is unknown
    result = _compare(probe=[[0.9], [0.9]], baseline=[[0.9], [0.9]], truth=[[T], [F]], mask=mask)

    assert result.per_tag["probe"].fp.tolist() == [0]
    assert result.per_tag["baseline_fixed"].fp.tolist() == [0]


@pytest.mark.unit
def test_compare_closes_scores_before_thresholding():
    # tag 1 is a child of tag 0; the child is confident, the parent is not
    result = _compare(
        probe=[[0.1, 0.9]], baseline=[[0.1, 0.9]], truth=[[T, T]], parent_index=[-1, 0]
    )

    assert result.per_tag["probe"].tp.tolist() == [1, 1]
    assert result.per_tag["baseline_fixed"].tp.tolist() == [1, 1]
    assert result.per_tag["baseline_best"].tp.tolist() == [1, 1]


@pytest.mark.unit
def test_compare_uses_fixed_and_best_baseline_thresholds():
    # baseline score 0.4: above the fixed 0.35, below the per-tag best of 0.5
    result = _compare(probe=[[0.4]], baseline=[[0.4]], truth=[[T]], thr=0.5)

    assert result.per_tag["baseline_fixed"].tp.tolist() == [1]
    assert result.per_tag["baseline_best"].tp.tolist() == [0]
    assert result.per_tag["probe"].tp.tolist() == [0]
    assert result.thresholds["baseline_fixed"].tolist() == pytest.approx([0.35])


def _metrics(f1s, support=100):
    # f1 = 2tp / (2tp + fp + fn) with fp == fn == support - tp  ->  f1 = tp / support
    tp = np.array([round(f * support) for f in f1s], dtype=np.int64)
    rest = support - tp
    return TagMetrics(tp=tp, fp=rest, fn=rest)


@pytest.mark.unit
def test_winners_use_gap_and_support():
    comparison = Comparison(
        tag_ids=np.arange(4),
        titles=["a", "b", "c", "d"],
        per_tag={
            "probe": _metrics([0.9, 0.5, 0.52, 0.9]),
            "baseline_fixed": _metrics([0.5, 0.9, 0.5, 0.1]),
            "baseline_best": _metrics([0.5, 0.9, 0.5, 0.1]),
        },
        thresholds={},
        support=np.array([100, 100, 100, MIN_SUPPORT - 1]),
    )

    assert comparison.winners().tolist() == ["probe", "baseline", "tie", "n/a"]
    summary = comparison.summary()
    assert summary["tags_won_probe"] == 1
    assert summary["tags_won_baseline"] == 1
    assert summary["tags_tie"] == 1
    assert summary["probe_macro_f1"] == pytest.approx((0.9 + 0.5 + 0.52) / 3)
    assert summary["baseline_best_macro_f1"] == pytest.approx((0.5 + 0.9 + 0.5) / 3)
    assert summary["probe_tags_f1_ge_0.5"] == 3
    assert summary["baseline_best_tags_f1_ge_0.5"] == 3
    assert summary["n_tags_scored"] == 3
    assert "probe_micro_f1" in summary and "baseline_fixed_micro_f1" in summary


@pytest.mark.unit
def test_reviewed_view_counts_precision_recall_and_auc():
    provenance = np.array(
        [[APPROVED, REJECTED], [APPROVED, APPROVED], [LINKED, NONE]], dtype=np.uint8
    )
    probe = np.array([[0.9, 0.2], [0.8, 0.7], [0.99, 0.99]], dtype=np.float32)
    baseline = np.array([[0.4, 0.9], [0.6, 0.6], [0.0, 0.0]], dtype=np.float32)

    result = reviewed_view(
        probe_scores=probe,
        baseline_scores=baseline,
        provenance=provenance,
        rows=np.array([0, 1, 2]),
        probe_thresholds=np.array([0.5, 0.5], dtype=np.float32),
        baseline_thresholds=np.array([0.5, 0.5], dtype=np.float32),
    )

    assert result["n_approved"] == 3 and result["n_rejected"] == 1
    assert result["probe_precision"] == pytest.approx(1.0)
    assert result["probe_recall"] == pytest.approx(1.0)
    assert result["probe_auc"] == pytest.approx(1.0)
    assert result["baseline_precision"] == pytest.approx(2 / 3)
    assert result["baseline_recall"] == pytest.approx(2 / 3)
    assert result["baseline_auc"] == pytest.approx(0.0)


@pytest.mark.unit
def test_newest_rows_takes_top_decile_by_id():
    ids = np.array([5, 1, 9, 3, 7, 2, 8, 4, 6, 10])

    assert newest_rows(ids).tolist() == [9]
    assert newest_rows(ids, fraction=0.2).tolist() == [2, 9]
    assert newest_rows(np.array([42]), fraction=0.1).tolist() == [0], "at least one row"


@pytest.mark.unit
def test_write_csv_and_render_markdown_have_expected_columns(tmp_path):
    result = _compare(probe=[[0.9, 0.1]], baseline=[[0.9, 0.9]], truth=[[T, F]])
    path = tmp_path / "per_tag.csv"

    write_csv(path, result)
    with open(path) as f:
        rows = list(csv.DictReader(f))

    assert [r["title"] for r in rows] == ["t0", "t1"]
    assert set(rows[0]) >= {
        "tag_id",
        "title",
        "support",
        "winner",
        "probe_threshold",
        "probe_precision",
        "probe_recall",
        "probe_f1",
        "baseline_fixed_f1",
        "baseline_best_threshold",
        "baseline_best_f1",
    }
    assert rows[0]["probe_f1"] == "1.0000"
    assert rows[1]["baseline_fixed_precision"] == "0.0000"

    markdown = render_markdown(
        {"headline/all": result.summary(), "sensitivity/all": result.summary()},
        {"all": {"n_approved": 3, "n_rejected": 1, "probe_auc": 1.0, "baseline_auc": 0.5}},
    )
    assert "headline/all" in markdown and "sensitivity/all" in markdown
    assert "reviewed" in markdown.lower()
    assert "probe_macro_f1" in markdown or "macro F1" in markdown


@pytest.mark.unit
def test_select_view_thresholds_uses_each_views_own_mask():
    # one tag; validation rows: a true negative at 0.2, a pending pair at 0.6, a positive at 0.9
    probe = np.array([[0.2], [0.6], [0.9]], dtype=np.float32)
    baseline = np.array([[0.2], [0.6], [0.9]], dtype=np.float32)
    truth = np.array([[F], [F], [T]])
    masks = {
        "headline": np.array([[F], [F], [F]]),
        "sensitivity": np.array([[F], [T], [F]]),  # the pending pair is unknown here
    }

    thresholds = select_view_thresholds(
        probe_scores=probe, baseline_scores=baseline, truth=truth, masks=masks
    )

    assert set(thresholds) == {"headline", "sensitivity"}
    # headline must exclude the 0.6 false positive; sensitivity may include it
    assert thresholds["headline"].probe.tolist() == pytest.approx([0.61])
    assert thresholds["headline"].baseline.tolist() == pytest.approx([0.61])
    assert thresholds["sensitivity"].probe.tolist() == pytest.approx([0.21])
    assert thresholds["sensitivity"].baseline.tolist() == pytest.approx([0.21])
