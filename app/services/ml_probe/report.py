"""Probe versus mapping path, tag by tag, under one label mask at a time.

A ``View`` is one slice of the evaluation images plus one per-pair mask
(design §2b: headline masks approved-suggestion pairs, sensitivity masks
pending ones too). ``compare`` scores three systems on it — the probe at its
per-tag thresholds, the baseline at the deployed fixed threshold, and the
baseline at per-tag best thresholds — after closing both score matrices
upward so hierarchy is not a confound. ``reviewed_view`` is the third angle:
only pairs a human has ruled on, pooled, with precision, recall and AUC.
"""

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.services.ml_probe.labels import APPROVED, REJECTED, close_upward
from app.services.ml_probe.metrics import TagMetrics, best_thresholds, confusion, micro_f1, roc_auc

MIN_SUPPORT = 20
WIN_GAP = 0.05
FIXED_BASELINE_THRESHOLD = 0.35
SYSTEMS = ("probe", "baseline_fixed", "baseline_best")
_F1_FLOOR = 0.5


@dataclass
class View:
    """Which evaluation rows to score and which (row, tag) pairs to leave out."""

    name: str
    rows: NDArray[np.int64]
    mask: NDArray[np.bool_] | None


@dataclass
class Comparison:
    tag_ids: NDArray[np.int64]
    titles: list[str]
    per_tag: dict[str, TagMetrics]
    thresholds: dict[str, NDArray[np.float32]]
    support: NDArray[np.int64]
    n_images: int = 0
    scored_tags: NDArray[np.bool_] = field(init=False)

    def __post_init__(self) -> None:
        self.scored_tags = self.support >= MIN_SUPPORT

    def winners(self) -> NDArray[np.str_]:
        """Per tag: "probe", "baseline" or "tie" against the best-threshold baseline."""
        gap = self.per_tag["probe"].f1 - self.per_tag["baseline_best"].f1
        verdict = np.where(gap >= WIN_GAP, "probe", np.where(gap <= -WIN_GAP, "baseline", "tie"))
        verdict = verdict.astype("<U8")
        verdict[~self.scored_tags] = "n/a"
        return verdict

    def summary(self) -> dict[str, float]:
        winners = self.winners()
        out: dict[str, float] = {
            "n_images": self.n_images,
            "n_tags_scored": int(self.scored_tags.sum()),
            "tags_won_probe": int((winners == "probe").sum()),
            "tags_won_baseline": int((winners == "baseline").sum()),
            "tags_tie": int((winners == "tie").sum()),
        }
        for system in SYSTEMS:
            metrics = self.per_tag[system]
            scored_f1 = metrics.f1[self.scored_tags]
            out[f"{system}_macro_f1"] = float(scored_f1.mean()) if scored_f1.size else math.nan
            out[f"{system}_micro_f1"] = micro_f1(metrics)
            out[f"{system}_tags_f1_ge_0.5"] = int((scored_f1 >= _F1_FLOOR).sum())
        return out


@dataclass
class ViewThresholds:
    """Per-tag operating points for one view: chosen on validation under that view's mask."""

    probe: NDArray[np.float32]
    baseline: NDArray[np.float32]


def select_view_thresholds(
    *,
    probe_scores: NDArray[np.float32],
    baseline_scores: NDArray[np.float32],
    truth: NDArray[np.bool_],
    masks: dict[str, NDArray[np.bool_]],
) -> dict[str, ViewThresholds]:
    """Best-F1 thresholds for both systems, once per mask, on the same validation rows.

    Design §2b: threshold selection respects the same mask as the scoring it
    feeds. Pending pairs are the baseline's own predictions, so a threshold
    tuned with them counted as false positives is too high once they are
    masked out; each view therefore gets its own pair.
    """
    return {
        name: ViewThresholds(
            probe=best_thresholds(probe_scores, truth, mask),
            baseline=best_thresholds(baseline_scores, truth, mask),
        )
        for name, mask in masks.items()
    }


def compare(
    *,
    probe_scores: NDArray[np.float32],
    baseline_scores: NDArray[np.float32],
    truth: NDArray[np.bool_],
    has_raw: NDArray[np.bool_],
    view: View,
    probe_thresholds: NDArray[np.float32],
    baseline_best_thresholds: NDArray[np.float32],
    parent_index: NDArray[np.int64],
    tag_ids: NDArray[np.int64],
    titles: list[str],
) -> Comparison:
    """Score the three systems on ``view``; rows without raw-store data are dropped."""
    keep = has_raw[view.rows]
    rows = view.rows[keep]
    mask = None if view.mask is None else view.mask[keep]
    probe = close_upward(probe_scores[rows], parent_index)
    baseline = close_upward(baseline_scores[rows], parent_index)
    y = truth[rows]
    n_tags = probe.shape[1]
    thresholds = {
        "probe": np.asarray(probe_thresholds, dtype=np.float32),
        "baseline_fixed": np.full(n_tags, FIXED_BASELINE_THRESHOLD, dtype=np.float32),
        "baseline_best": np.asarray(baseline_best_thresholds, dtype=np.float32),
    }
    per_tag = {
        "probe": confusion(probe >= thresholds["probe"], y, mask),
        "baseline_fixed": confusion(baseline >= thresholds["baseline_fixed"], y, mask),
        "baseline_best": confusion(baseline >= thresholds["baseline_best"], y, mask),
    }
    return Comparison(
        tag_ids=np.asarray(tag_ids, dtype=np.int64),
        titles=list(titles),
        per_tag=per_tag,
        thresholds=thresholds,
        support=per_tag["probe"].support(),
        n_images=int(len(rows)),
    )


def _pooled(pred: NDArray[np.bool_], truth: NDArray[np.bool_]) -> tuple[float, float]:
    tp = int((pred & truth).sum())
    fp = int((pred & ~truth).sum())
    fn = int((~pred & truth).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return precision, recall


def reviewed_view(
    *,
    probe_scores: NDArray[np.float32],
    baseline_scores: NDArray[np.float32],
    provenance: NDArray[np.uint8],
    rows: NDArray[np.int64],
    probe_thresholds: NDArray[np.float32],
    baseline_thresholds: NDArray[np.float32],
) -> dict[str, float]:
    """Pooled precision, recall and AUC over pairs a reviewer approved or rejected."""
    prov = provenance[rows]
    selected = (prov == APPROVED) | (prov == REJECTED)
    truth = prov[selected] == APPROVED
    out: dict[str, float] = {
        "n_approved": int(truth.sum()),
        "n_rejected": int((~truth).sum()),
    }
    for system, scores, thresholds in (
        ("probe", probe_scores, probe_thresholds),
        ("baseline", baseline_scores, baseline_thresholds),
    ):
        sliced = scores[rows]
        pred = (sliced >= thresholds)[selected]
        precision, recall = _pooled(pred, truth)
        out[f"{system}_precision"] = precision
        out[f"{system}_recall"] = recall
        out[f"{system}_auc"] = roc_auc(sliced[selected], truth)
    return out


def newest_rows(image_ids: NDArray[np.int64], fraction: float = 0.1) -> NDArray[np.int64]:
    """Row indices of the newest ``fraction`` of images by id (at least one), ascending."""
    count = max(1, round(len(image_ids) * fraction))
    order = np.argsort(np.asarray(image_ids), kind="stable")
    return np.sort(order[-count:]).astype(np.int64)


_CSV_COLUMNS = [
    "tag_id",
    "title",
    "support",
    "winner",
    "probe_threshold",
    "probe_precision",
    "probe_recall",
    "probe_f1",
    "baseline_fixed_precision",
    "baseline_fixed_recall",
    "baseline_fixed_f1",
    "baseline_best_threshold",
    "baseline_best_precision",
    "baseline_best_recall",
    "baseline_best_f1",
]


def write_csv(path: Path, comparison: Comparison) -> None:
    winners = comparison.winners()
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for i, tag_id in enumerate(comparison.tag_ids):
            row: dict[str, Any] = {
                "tag_id": int(tag_id),
                "title": comparison.titles[i],
                "support": int(comparison.support[i]),
                "winner": str(winners[i]),
                "probe_threshold": f"{comparison.thresholds['probe'][i]:.2f}",
                "baseline_best_threshold": f"{comparison.thresholds['baseline_best'][i]:.2f}",
            }
            for system in SYSTEMS:
                metrics = comparison.per_tag[system]
                row[f"{system}_precision"] = f"{metrics.precision[i]:.4f}"
                row[f"{system}_recall"] = f"{metrics.recall[i]:.4f}"
                row[f"{system}_f1"] = f"{metrics.f1[i]:.4f}"
            writer.writerow(row)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return "nan" if math.isnan(value) else f"{value:.3f}"
    return str(value)


def render_markdown(
    summaries: dict[str, dict[str, float]], reviewed: dict[str, dict[str, float]]
) -> str:
    """One aggregates table (view × system) and one reviewed-pairs table."""
    lines = [
        "# Theme-tag probe vs mapping path",
        "",
        "## Aggregates",
        "",
        "| view | system | macro F1 (support ≥ 20) | micro F1 | tags F1 ≥ 0.5 |"
        " tags won | n images | n tags scored |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for view_name, summary in summaries.items():
        for system in SYSTEMS:
            won = {
                "probe": summary.get("tags_won_probe"),
                "baseline_fixed": None,
                "baseline_best": summary.get("tags_won_baseline"),
            }[system]
            lines.append(
                f"| {view_name} | {system} | {_fmt(summary.get(f'{system}_macro_f1'))} "
                f"| {_fmt(summary.get(f'{system}_micro_f1'))} "
                f"| {_fmt(summary.get(f'{system}_tags_f1_ge_0.5'))} | {_fmt(won)} "
                f"| {_fmt(summary.get('n_images'))} | {_fmt(summary.get('n_tags_scored'))} |"
            )
    lines += [
        "",
        "Ties (F1 gap under 0.05) per view: "
        + ", ".join(f"{name} {_fmt(s.get('tags_tie'))}" for name, s in summaries.items()),
        "",
        "## Reviewed pairs (approved vs rejected suggestions)",
        "",
        "| slice | approved | rejected | probe precision | probe recall | probe AUC |"
        " baseline precision | baseline recall | baseline AUC |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for slice_name, values in reviewed.items():
        cells = [
            values.get(key)
            for key in (
                "n_approved",
                "n_rejected",
                "probe_precision",
                "probe_recall",
                "probe_auc",
                "baseline_precision",
                "baseline_recall",
                "baseline_auc",
            )
        ]
        lines.append(f"| {slice_name} | " + " | ".join(_fmt(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"
