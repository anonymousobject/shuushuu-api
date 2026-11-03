"""Confusion metrics, per-tag thresholds and rank AUC (app/services/ml_probe/metrics.py)."""

import math

import numpy as np
import pytest

from app.services.ml_probe.metrics import (
    DEFAULT_GRID,
    best_thresholds,
    confusion,
    macro_f1,
    micro_f1,
    roc_auc,
)

T, F = True, False


@pytest.mark.unit
def test_confusion_counts_and_mask():
    pred = np.array([[T, F], [T, T], [F, T]])
    truth = np.array([[T, F], [F, T], [F, F]])
    mask = np.array([[F, F], [T, F], [F, F]])  # exclude (image 1, tag 0): a false positive

    m = confusion(pred, truth, mask)

    assert m.tp.tolist() == [1, 1]
    assert m.fp.tolist() == [0, 1]
    assert m.fn.tolist() == [0, 0]
    assert m.precision.tolist() == pytest.approx([1.0, 0.5])
    assert m.recall.tolist() == pytest.approx([1.0, 1.0])
    assert m.f1.tolist() == pytest.approx([1.0, 2 / 3])
    assert m.support().tolist() == [1, 1]


@pytest.mark.unit
def test_confusion_without_mask_counts_everything():
    pred = np.array([[T, F], [T, T], [F, T]])
    truth = np.array([[T, F], [F, T], [F, F]])

    m = confusion(pred, truth)

    assert m.fp.tolist() == [1, 1]


@pytest.mark.unit
def test_confusion_zero_predictions_precision_is_zero():
    pred = np.zeros((3, 2), dtype=bool)
    truth = np.array([[T, F], [T, F], [F, F]])

    m = confusion(pred, truth)

    assert m.precision.tolist() == [0.0, 0.0]
    assert m.recall.tolist() == [0.0, 0.0]
    assert m.f1.tolist() == [0.0, 0.0]
    assert not np.isnan(m.f1).any()


@pytest.mark.unit
def test_best_thresholds_picks_max_f1_lowest_on_tie():
    scores = np.array([[0.2], [0.6], [0.9]], dtype=np.float32)
    truth = np.array([[F], [T], [T]])

    thresholds = best_thresholds(scores, truth)

    # any threshold in (0.2, 0.6] scores F1 = 1; the lowest grid point there is 0.21
    assert thresholds.tolist() == pytest.approx([0.21])
    assert thresholds.dtype == np.float32


@pytest.mark.unit
def test_best_thresholds_respects_mask():
    scores = np.array([[0.2], [0.6], [0.9]], dtype=np.float32)
    truth = np.array([[F], [F], [T]])
    mask = np.array([[F], [T], [F]])  # the 0.6 negative is unknown, so 0.21 is still perfect

    assert best_thresholds(scores, truth, mask).tolist() == pytest.approx([0.21])
    assert best_thresholds(scores, truth).tolist() == pytest.approx([0.61])


@pytest.mark.unit
def test_best_thresholds_tag_without_positives():
    scores = np.array([[0.2, 0.9], [0.6, 0.1]], dtype=np.float32)
    truth = np.array([[T, F], [T, F]])

    thresholds = best_thresholds(scores, truth)

    assert thresholds[1] == pytest.approx(0.5)
    assert thresholds[0] == pytest.approx(0.05)


@pytest.mark.unit
def test_default_grid():
    assert DEFAULT_GRID[0] == pytest.approx(0.05)
    assert DEFAULT_GRID[-1] == pytest.approx(0.95)
    assert len(DEFAULT_GRID) == 91


@pytest.mark.unit
def test_macro_f1_respects_min_support_and_micro_f1():
    pred = np.array([[T, T], [T, F], [T, F], [F, F]])
    truth = np.array([[T, T], [T, F], [F, F], [T, F]])
    # tag 0: tp 2 fp 1 fn 1 -> f1 2/3, support 3; tag 1: tp 1 -> f1 1, support 1

    m = confusion(pred, truth)

    assert macro_f1(m, min_support=1) == pytest.approx((2 / 3 + 1.0) / 2)
    assert macro_f1(m, min_support=2) == pytest.approx(2 / 3)
    assert math.isnan(macro_f1(m, min_support=4))
    assert micro_f1(m) == pytest.approx(2 * 3 / (2 * 3 + 1 + 1))


@pytest.mark.unit
def test_roc_auc_perfect_reversed_ties_and_single_class():
    truth = np.array([F, F, T, T])
    assert roc_auc(np.array([0.1, 0.2, 0.8, 0.9]), truth) == pytest.approx(1.0)
    assert roc_auc(np.array([0.9, 0.8, 0.2, 0.1]), truth) == pytest.approx(0.0)
    assert roc_auc(np.array([0.5, 0.5, 0.5, 0.5]), truth) == pytest.approx(0.5)
    assert roc_auc(np.array([0.1, 0.9, 0.5, 0.9]), truth) == pytest.approx(0.625)
    assert math.isnan(roc_auc(np.array([0.1, 0.2]), np.array([T, T])))
