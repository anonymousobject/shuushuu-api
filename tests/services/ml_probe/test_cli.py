"""End-to-end `train` and `eval` of scripts/ml_probe.py on synthetic files.

No database and no model: a tiny embedding store, a label set and a baseline
file are generated so that the probe is learnable, the store and the labels
hold different image sets, one evaluation image has no raw-store rows, and
the three label views all have something to score.
"""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from app.services.ml_probe.baseline import BaselineScores
from app.services.ml_probe.head import LinearHead
from app.services.ml_probe.labels import APPROVED, LINKED, NONE, PENDING, LabelSet
from app.services.ml_probe.store import IDS_FILE, META_FILE, VECTORS_FILE

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "ml_probe.py"
_spec = importlib.util.spec_from_file_location("ml_probe_cli", _SCRIPT)
assert _spec is not None and _spec.loader is not None
ml_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ml_probe)

DIM, K = 8, 3
LABELED_IDS = np.arange(1, 61, dtype=np.int64)  # 60 images; fold 0: 10..60, fold 1: 1..51
STORE_IDS = np.array([i for i in LABELED_IDS if i != 7] + [999], dtype=np.int64)


def _synthetic(tmp_path):
    rng = np.random.default_rng(0)
    vectors = {int(i): rng.normal(size=DIM).astype(np.float16) for i in STORE_IDS}
    w_true = rng.normal(size=(DIM, K))
    b_true = np.array([0.0, -0.3, 0.2])

    def truth_for(image_id):
        x = vectors.get(int(image_id), rng.normal(size=DIM).astype(np.float16)).astype(np.float32)
        y = (x @ w_true + b_true) > 0
        y[0] |= y[1]  # tag 1 is a child of tag 0: parent positive whenever the child is
        return y

    # store
    store = tmp_path / "emb"
    store.mkdir()
    (store / META_FILE).write_text(
        json.dumps({"model": "test-model", "dim": DIM, "dtype": "float16"})
    )
    np.asarray(STORE_IDS, dtype=np.int64).tofile(store / IDS_FILE)
    np.stack([vectors[int(i)] for i in STORE_IDS]).astype(np.float16).tofile(store / VECTORS_FILE)

    # labels: LINKED where true; two approved pairs and one pending pair in the test fold
    provenance = np.zeros((len(LABELED_IDS), K), dtype=np.uint8)
    truth = np.stack([truth_for(i) for i in LABELED_IDS])
    truth[LABELED_IDS == 30, 2] = False  # make room for a pending pair
    provenance[truth] = LINKED
    for row in np.flatnonzero(np.isin(LABELED_IDS, [10, 20])):
        truth[row, 2] = True
        provenance[row, 2] = APPROVED
    provenance[LABELED_IDS == 30, 2] = PENDING
    labels = LabelSet(
        image_ids=LABELED_IDS,
        tag_ids=np.array([100, 101, 102], dtype=np.int64),
        titles=["parent", "child", "other"],
        parent_index=np.array([-1, 0, -1], dtype=np.int64),
        provenance=provenance,
    )
    labels.save(tmp_path / "labels.npz")

    # baseline for folds 0 and 1: confident where true, weak otherwise; image 40 has no raw rows
    eval_ids = np.array([i for i in LABELED_IDS if i % 10 in (0, 1)], dtype=np.int64)
    scores = np.where(truth[np.isin(LABELED_IDS, eval_ids)], 0.8, 0.1).astype(np.float32)
    has_raw = eval_ids != 40
    scores[~has_raw] = 0.0
    BaselineScores(image_ids=eval_ids, scores=scores, has_raw=has_raw).save(
        tmp_path / "baseline.npz"
    )
    assert provenance[LABELED_IDS == 30, 2] == PENDING and (provenance == APPROVED).sum() == 2
    assert NONE == 0
    return store


@pytest.mark.unit
def test_train_and_eval_align_by_image_id(tmp_path):
    store = _synthetic(tmp_path)
    parser = ml_probe.build_parser()

    head = ml_probe.cmd_train(
        parser.parse_args(
            [
                "train",
                "--embeddings",
                str(store),
                "--labels",
                str(tmp_path / "labels.npz"),
                "--out",
                str(tmp_path / "head.npz"),
                "--min-train-positives",
                "3",
                "--max-epochs",
                "40",
                "--batch-size",
                "16",
                "--lr",
                "0.02",
            ]
        )
    )

    saved = LinearHead.load(tmp_path / "head.npz")
    assert set(saved.tag_ids.tolist()) <= {100, 101, 102}
    assert saved.tag_ids.tolist() == head.tag_ids.tolist()
    assert saved.meta["model"] == "test-model"
    assert saved.meta["split"] == "image_id % 10; test 0, val 1"
    assert saved.meta["min_train_positives"] == 3
    # 60 labelled ids, 12 in folds 0/1, id 7 missing from the store, id 999 unlabelled
    assert saved.meta["n_train"] == 47
    assert saved.meta["n_val"] == 6
    assert not np.allclose(saved.thresholds, 0.5), "thresholds were chosen on validation"

    out_dir = tmp_path / "eval"
    summaries = ml_probe.cmd_eval(
        parser.parse_args(
            [
                "eval",
                "--embeddings",
                str(store),
                "--labels",
                str(tmp_path / "labels.npz"),
                "--baseline",
                str(tmp_path / "baseline.npz"),
                "--head",
                str(tmp_path / "head.npz"),
                "--out-dir",
                str(out_dir),
            ]
        )
    )

    assert set(summaries) >= {
        "headline/all",
        "headline/newest10",
        "sensitivity/all",
        "sensitivity/newest10",
    }
    assert summaries["headline/all"]["n_images"] == 5, (
        "6 test images minus the one without raw rows"
    )
    assert summaries["headline/newest10"]["n_images"] == 1
    for view in ("headline_all", "headline_newest10", "sensitivity_all", "sensitivity_newest10"):
        assert (out_dir / f"per_tag_{view}.csv").exists()
    summary_md = (out_dir / "summary.md").read_text()
    assert "headline/all" in summary_md and "sensitivity/all" in summary_md
    assert "Reviewed pairs" in summary_md
    assert "| all | 2 | 0 |" in summary_md, "two approved pairs, no rejections, in the test fold"


@pytest.mark.unit
def test_parser_has_four_subcommands():
    parser = ml_probe.build_parser()
    for argv in (
        ["labels", "--out", "l.npz"],
        ["baseline", "--labels", "l.npz", "--model", "m", "--out", "b.npz"],
        ["train", "--embeddings", "e", "--labels", "l.npz", "--out", "h.npz"],
        [
            "eval",
            "--embeddings",
            "e",
            "--labels",
            "l.npz",
            "--baseline",
            "b.npz",
            "--head",
            "h.npz",
            "--out-dir",
            "d",
        ],
    ):
        assert parser.parse_args(argv).command == argv[0]
