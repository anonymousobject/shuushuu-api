#!/usr/bin/env python3
"""Theme-tag linear probe on frozen animetimm embeddings.

Four subcommands, the first two needing the database and the last two only
files (design: docs/plans/2026-Q4/2026-11-03-theme-probe-design.md):

    labels    tag_links + ml_tag_suggestions  -> labels.npz   (provenance matrix)
    baseline  ml_raw_predictions + mappings   -> baseline.npz (deployed path, val+test folds)
    train     embeddings + labels.npz         -> head.npz     (logistic head, val thresholds)
    eval      embeddings + labels + baseline + head -> per-tag CSVs and summary.md

Usage:
    uv run python scripts/ml_probe.py labels --out labels.npz
    uv run python scripts/ml_probe.py baseline --labels labels.npz \\
        --model swinv2_base_window8_256.dbv4-full --out baseline.npz
    uv run python scripts/ml_probe.py train --embeddings DIR --labels labels.npz --out head.npz
    uv run python scripts/ml_probe.py eval --embeddings DIR --labels labels.npz \\
        --baseline baseline.npz --head head.npz --out-dir DIR

Run from the host with DATABASE_URL pointed at localhost (the .env value names
the compose hostname); see the implementation plan's Global Constraints.
"""

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.config import ImageStatus
from app.core.database import get_async_session
from app.services.ml_probe.baseline import BaselineScores, build_baseline_scores
from app.services.ml_probe.head import (
    LinearHead,
    TrainConfig,
    standardization_stats,
    train_linear_head,
)
from app.services.ml_probe.labels import (
    APPROVED,
    LINKED,
    PENDING,
    REJECTED,
    LabelSet,
    build_label_set,
    close_upward,
    load_theme_tag_graph,
)
from app.services.ml_probe.metrics import best_thresholds
from app.services.ml_probe.report import (
    View,
    compare,
    newest_rows,
    render_markdown,
    reviewed_view,
    select_view_thresholds,
    write_csv,
)
from app.services.ml_probe.split import TEST_FOLD, VAL_FOLD, fold_of
from app.services.ml_probe.store import EmbeddingStore, open_embedding_store

SPLIT_RULE = "image_id % 10; test 0, val 1"
_SCORE_CHUNK = 65_536


# --- DB-backed commands ---------------------------------------------------------


async def cmd_labels(args: argparse.Namespace) -> LabelSet:
    async with get_async_session() as db:
        graph = await load_theme_tag_graph(db)
        labels = await build_label_set(db, graph, status=args.status)
    labels.save(Path(args.out))
    pairs = {
        name: int((labels.provenance == code).sum())
        for name, code in (
            ("linked", LINKED),
            ("approved", APPROVED),
            ("pending", PENDING),
            ("rejected", REJECTED),
        )
    }
    print(f"labels: {len(labels.image_ids)} images x {len(labels.tag_ids)} tags, pairs {pairs}")
    print(f"wrote {args.out}")
    return labels


async def cmd_baseline(args: argparse.Namespace) -> BaselineScores:
    labels = LabelSet.load(Path(args.labels))
    folds = fold_of(labels.image_ids)
    eval_ids = labels.image_ids[(folds == TEST_FOLD) | (folds == VAL_FOLD)]
    async with get_async_session() as db:
        graph = await load_theme_tag_graph(db)
        if graph.tag_ids.tolist() != labels.tag_ids.tolist():
            raise SystemExit("theme tag columns differ between labels.npz and the DB; rerun labels")
        baseline = await build_baseline_scores(db, graph, model_name=args.model, image_ids=eval_ids)
    baseline.save(Path(args.out))
    print(
        f"baseline: {len(baseline.image_ids)} val+test images, "
        f"{int(baseline.has_raw.sum())} with raw rows; wrote {args.out}"
    )
    return baseline


# --- file-only commands ---------------------------------------------------------


@dataclass
class Aligned:
    """Labelled images that are also in the store, with the row of each in both."""

    image_ids: NDArray[np.int64]
    store_rows: NDArray[np.int64]
    label_rows: NDArray[np.int64]


def align(store: EmbeddingStore, labels: LabelSet) -> Aligned:
    rows = store.rows_for(labels.image_ids)
    keep = rows >= 0
    return Aligned(
        image_ids=labels.image_ids[keep],
        store_rows=rows[keep],
        label_rows=np.flatnonzero(keep).astype(np.int64),
    )


def probe_scores(
    head: LinearHead, vectors: np.memmap, rows: NDArray[np.int64]
) -> NDArray[np.float32]:
    """Head scores for store rows, read in chunks, in the order given."""
    parts = [
        head.scores(np.asarray(vectors[rows[start : start + _SCORE_CHUNK]], dtype=np.float32))
        for start in range(0, len(rows), _SCORE_CHUNK)
    ]
    if not parts:
        return np.zeros((0, len(head.tag_ids)), dtype=np.float32)
    return np.concatenate(parts).astype(np.float32)


def subset_parent_index(
    parent_index: NDArray[np.int64], columns: NDArray[np.int64]
) -> NDArray[np.int64]:
    """Parent pointers re-expressed within a column subset (-1 when the parent is outside it)."""
    position = {int(col): i for i, col in enumerate(columns)}
    return np.array([position.get(int(parent_index[col]), -1) for col in columns], dtype=np.int64)


def cmd_train(args: argparse.Namespace) -> LinearHead:
    store = open_embedding_store(Path(args.embeddings))
    labels = LabelSet.load(Path(args.labels))
    aligned = align(store, labels)
    folds = fold_of(aligned.image_ids)
    train_sel = folds >= 2
    val_sel = folds == VAL_FOLD

    positives = labels.positives()[aligned.label_rows]
    tag_sel = positives[train_sel].sum(axis=0) >= args.min_train_positives
    if not tag_sel.any():
        raise SystemExit(f"no tag has {args.min_train_positives} training positives")
    columns = np.flatnonzero(tag_sel).astype(np.int64)
    tag_ids = labels.tag_ids[columns]
    print(
        f"aligned {len(aligned.image_ids)} of {len(labels.image_ids)} labelled images with the "
        f"store ({len(store.ids)} rows); training {len(tag_ids)} of {len(labels.tag_ids)} tags"
    )

    # y indexed by store row, so the trainer can gather x and y with the same indices
    y = np.zeros((len(store.ids), len(tag_ids)), dtype=np.bool_)
    y[aligned.store_rows] = positives[:, columns]
    train_rows = aligned.store_rows[train_sel]
    val_rows = aligned.store_rows[val_sel]
    mean, std = standardization_stats(store.vectors, train_rows)
    config = TrainConfig(
        lr=args.lr,
        batch_size=args.batch_size,
        l2=args.l2,
        max_epochs=args.max_epochs,
        patience=args.patience,
        seed=args.seed,
    )
    head = train_linear_head(
        store.vectors, y, train_rows, val_rows, tag_ids=tag_ids, mean=mean, std=std, config=config
    )

    # thresholds: best F1 per tag on validation, under the headline mask, on closed scores
    parent_index = subset_parent_index(labels.parent_index, columns)
    val_scores = close_upward(probe_scores(head, store.vectors, val_rows), parent_index)
    val_mask = labels.approved_mask()[aligned.label_rows][val_sel][:, columns]
    head.thresholds = best_thresholds(val_scores, y[val_rows], val_mask)
    head.meta.update(
        {
            "model": store.meta.get("model"),
            "split": SPLIT_RULE,
            "min_train_positives": args.min_train_positives,
        }
    )
    head.save(Path(args.out))
    print(
        f"trained {len(tag_ids)} tags in {head.meta['epochs_run']} epochs, "
        f"best val loss {head.meta['best_val_loss']:.4f}; wrote {args.out}"
    )
    return head


def _baseline_rows(
    baseline: BaselineScores, image_ids: NDArray[np.int64], columns: NDArray[np.int64]
) -> tuple[NDArray[np.float32], NDArray[np.bool_]]:
    """Baseline scores (head's tag subset) and has_raw for the given images, zeros where absent."""
    position = np.searchsorted(baseline.image_ids, image_ids)
    clipped = np.clip(position, 0, max(len(baseline.image_ids) - 1, 0))
    found = (len(baseline.image_ids) > 0) & (baseline.image_ids[clipped] == image_ids)
    scores = np.zeros((len(image_ids), len(columns)), dtype=np.float32)
    has_raw = np.zeros(len(image_ids), dtype=np.bool_)
    scores[found] = baseline.scores[clipped[found]][:, columns]
    has_raw[found] = baseline.has_raw[clipped[found]]
    return scores, has_raw


def cmd_eval(args: argparse.Namespace) -> dict[str, dict[str, float]]:
    store = open_embedding_store(Path(args.embeddings))
    labels = LabelSet.load(Path(args.labels))
    baseline = BaselineScores.load(Path(args.baseline))
    head = LinearHead.load(Path(args.head))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    columns = np.searchsorted(labels.tag_ids, head.tag_ids)
    if not np.array_equal(labels.tag_ids[columns], head.tag_ids):
        raise SystemExit("head.npz tags are not all present in labels.npz")
    parent_index = subset_parent_index(labels.parent_index, columns)
    titles = [labels.titles[int(c)] for c in columns]

    aligned = align(store, labels)
    folds = fold_of(aligned.image_ids)
    positives = labels.positives()[aligned.label_rows][:, columns]
    approved = labels.approved_mask()[aligned.label_rows][:, columns]
    pending = labels.pending_mask()[aligned.label_rows][:, columns]
    provenance = labels.provenance[aligned.label_rows][:, columns]

    # Per-view operating points, chosen on the validation images both systems can score,
    # each under its own mask (design §2b), on closed scores as compare() thresholds them.
    val_sel = folds == VAL_FOLD
    val_base, val_has_raw = _baseline_rows(baseline, aligned.image_ids[val_sel], columns)
    val_base = close_upward(val_base, parent_index)[val_has_raw]
    val_probe = close_upward(
        probe_scores(head, store.vectors, aligned.store_rows[val_sel]), parent_index
    )[val_has_raw]
    val_truth = positives[val_sel][val_has_raw]
    val_masks = {
        "headline": approved[val_sel][val_has_raw],
        "sensitivity": (approved[val_sel] | pending[val_sel])[val_has_raw],
    }
    thresholds = select_view_thresholds(
        probe_scores=val_probe, baseline_scores=val_base, truth=val_truth, masks=val_masks
    )

    test_sel = folds == TEST_FOLD
    test_ids = aligned.image_ids[test_sel]
    test_probe = probe_scores(head, store.vectors, aligned.store_rows[test_sel])
    test_base, test_has_raw = _baseline_rows(baseline, test_ids, columns)
    truth = positives[test_sel]
    masks = {
        "headline": approved[test_sel],
        "sensitivity": approved[test_sel] | pending[test_sel],
    }
    slices = {"all": np.arange(len(test_ids), dtype=np.int64), "newest10": newest_rows(test_ids)}
    print(f"evaluating {len(test_ids)} test images ({int(test_has_raw.sum())} with raw rows)")

    summaries: dict[str, dict[str, float]] = {}
    for mask_name, mask in masks.items():
        for slice_name, rows in slices.items():
            view = View(name=f"{mask_name}/{slice_name}", rows=rows, mask=mask[rows])
            comparison = compare(
                probe_scores=test_probe,
                baseline_scores=test_base,
                truth=truth,
                has_raw=test_has_raw,
                view=view,
                probe_thresholds=thresholds[mask_name].probe,
                baseline_best_thresholds=thresholds[mask_name].baseline,
                parent_index=parent_index,
                tag_ids=head.tag_ids,
                titles=titles,
            )
            summaries[view.name] = comparison.summary()
            write_csv(out_dir / f"per_tag_{mask_name}_{slice_name}.csv", comparison)

    probe_closed = close_upward(test_probe, parent_index)
    base_closed = close_upward(test_base, parent_index)
    reviewed = {
        slice_name: reviewed_view(
            probe_scores=probe_closed,
            baseline_scores=base_closed,
            provenance=provenance[test_sel],
            rows=rows[test_has_raw[rows]],
            probe_thresholds=thresholds["headline"].probe,
            baseline_thresholds=thresholds["headline"].baseline,
        )
        for slice_name, rows in slices.items()
    }
    markdown = render_markdown(summaries, reviewed)
    (out_dir / "summary.md").write_text(markdown)
    print(markdown)
    print(f"wrote {out_dir}")
    return summaries


# --- CLI ------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    labels = sub.add_parser("labels", help="build labels.npz from the database")
    labels.add_argument("--out", required=True)
    labels.add_argument("--status", type=int, default=ImageStatus.ACTIVE)

    baseline = sub.add_parser("baseline", help="reconstruct the mapping path for val+test folds")
    baseline.add_argument("--labels", required=True)
    baseline.add_argument("--model", required=True, help="ml_models.name of the raw predictions")
    baseline.add_argument("--out", required=True)

    train = sub.add_parser("train", help="train the logistic head on the embedding store")
    train.add_argument("--embeddings", required=True, help="embedding store directory")
    train.add_argument("--labels", required=True)
    train.add_argument("--out", required=True)
    train.add_argument("--min-train-positives", type=int, default=100)
    defaults = TrainConfig()
    train.add_argument("--lr", type=float, default=defaults.lr)
    train.add_argument("--batch-size", type=int, default=defaults.batch_size)
    train.add_argument("--l2", type=float, default=defaults.l2)
    train.add_argument("--max-epochs", type=int, default=defaults.max_epochs)
    train.add_argument("--patience", type=int, default=defaults.patience)
    train.add_argument("--seed", type=int, default=defaults.seed)

    evaluate = sub.add_parser("eval", help="compare the head with the mapping path")
    evaluate.add_argument("--embeddings", required=True)
    evaluate.add_argument("--labels", required=True)
    evaluate.add_argument("--baseline", required=True)
    evaluate.add_argument("--head", required=True)
    evaluate.add_argument("--out-dir", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "labels":
        asyncio.run(cmd_labels(args))
    elif args.command == "baseline":
        asyncio.run(cmd_baseline(args))
    elif args.command == "train":
        cmd_train(args)
    else:
        cmd_eval(args)


if __name__ == "__main__":
    main()
