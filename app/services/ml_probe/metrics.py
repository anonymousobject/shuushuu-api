"""Per-tag confusion metrics, best-F1 thresholds and a rank-based ROC AUC.

Everything takes an optional boolean ``mask`` of (image, tag) pairs to exclude
from scoring: the design's label masks (approved-suggestion pairs, pending
pairs) are applied here, identically for both systems. Ratios with a zero
denominator are 0, never NaN, so a tag that is never predicted or never
present reports cleanly.
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

DEFAULT_GRID: NDArray[np.float32] = np.round(np.arange(0.05, 0.96, 0.01), 2).astype(np.float32)
_NO_POSITIVES_THRESHOLD = np.float32(0.5)


def _ratio(numerator: NDArray[np.int64], denominator: NDArray[np.int64]) -> NDArray[np.float64]:
    out = np.zeros(numerator.shape, dtype=np.float64)
    np.divide(numerator, denominator, out=out, where=denominator > 0)
    return out


@dataclass
class TagMetrics:
    """True positives, false positives and false negatives per tag."""

    tp: NDArray[np.int64]
    fp: NDArray[np.int64]
    fn: NDArray[np.int64]

    @property
    def precision(self) -> NDArray[np.float64]:
        return _ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> NDArray[np.float64]:
        return _ratio(self.tp, self.tp + self.fn)

    @property
    def f1(self) -> NDArray[np.float64]:
        return _ratio(2 * self.tp, 2 * self.tp + self.fp + self.fn)

    def support(self) -> NDArray[np.int64]:
        """Scored positives per tag."""
        return self.tp + self.fn


def confusion(
    pred: NDArray[np.bool_],
    truth: NDArray[np.bool_],
    mask: NDArray[np.bool_] | None = None,
) -> TagMetrics:
    """Column-wise confusion counts; pairs where ``mask`` is True are not scored."""
    pred = np.asarray(pred, dtype=np.bool_)
    truth = np.asarray(truth, dtype=np.bool_)
    keep = (
        np.ones(pred.shape, dtype=np.bool_) if mask is None else ~np.asarray(mask, dtype=np.bool_)
    )
    return TagMetrics(
        tp=(pred & truth & keep).sum(axis=0).astype(np.int64),
        fp=(pred & ~truth & keep).sum(axis=0).astype(np.int64),
        fn=(~pred & truth & keep).sum(axis=0).astype(np.int64),
    )


def best_thresholds(
    scores: NDArray[np.float32],
    truth: NDArray[np.bool_],
    mask: NDArray[np.bool_] | None = None,
    grid: NDArray[np.float32] = DEFAULT_GRID,
) -> NDArray[np.float32]:
    """Per tag, the grid threshold with the best F1; the lowest on ties.

    A tag with no scored positives gets 0.5, since every threshold is equally
    meaningless for it. A tag with positives that no threshold ever catches
    gets the highest grid value, so it predicts as little as possible.
    """
    truth = np.asarray(truth, dtype=np.bool_)
    n_tags = scores.shape[1]
    best_f1 = np.full(n_tags, -1.0)
    best = np.full(n_tags, _NO_POSITIVES_THRESHOLD, dtype=np.float32)
    for threshold in np.sort(grid):
        f1 = confusion(scores >= threshold, truth, mask).f1
        better = f1 > best_f1
        best_f1[better] = f1[better]
        best[better] = threshold
    positives = confusion(np.zeros_like(truth), truth, mask).support()
    # Never right at any threshold: predict as little as possible rather than
    # flooding false positives from the lowest grid point.
    best[best_f1 <= 0] = np.float32(np.max(grid))
    best[positives == 0] = _NO_POSITIVES_THRESHOLD
    return best


def macro_f1(metrics: TagMetrics, *, min_support: int) -> float:
    """Mean F1 over tags with at least ``min_support`` scored positives; NaN if none."""
    selected = metrics.support() >= min_support
    if not selected.any():
        return float("nan")
    return float(metrics.f1[selected].mean())


def micro_f1(metrics: TagMetrics) -> float:
    """F1 of the pooled counts across all tags."""
    tp, fp, fn = int(metrics.tp.sum()), int(metrics.fp.sum()), int(metrics.fn.sum())
    denominator = 2 * tp + fp + fn
    return 2 * tp / denominator if denominator else 0.0


def roc_auc(scores: NDArray[np.floating], truth: NDArray[np.bool_]) -> float:
    """Mann–Whitney AUC with average ranks for ties; NaN when a class is empty."""
    scores = np.asarray(scores, dtype=np.float64)
    truth = np.asarray(truth, dtype=np.bool_)
    n_pos = int(truth.sum())
    n_neg = int(truth.size - n_pos)
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    _, inverse, counts = np.unique(scores, return_inverse=True, return_counts=True)
    starts = np.cumsum(counts) - counts + 1
    mean_ranks = starts + (counts - 1) / 2
    ranks = mean_ranks[inverse]
    return float((ranks[truth].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))
