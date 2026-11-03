"""The numpy logistic head and its trainer (app/services/ml_probe/head.py)."""

import numpy as np
import pytest

from app.services.ml_probe.head import (
    LinearHead,
    TrainConfig,
    standardization_stats,
    train_linear_head,
)
from app.services.ml_probe.metrics import confusion


def _sigmoid(z):
    return 1 / (1 + np.exp(-z))


def _head(**overrides):
    fields = {
        "weight": np.eye(2, dtype=np.float32),
        "bias": np.array([0.0, 1.0], dtype=np.float32),
        "mean": np.array([3.0, 4.0], dtype=np.float32),
        "std": np.array([1.0, 2.0], dtype=np.float32),
        "tag_ids": np.array([10, 20], dtype=np.int64),
        "thresholds": np.array([0.5, 0.5], dtype=np.float32),
        "meta": {"model": "m"},
    }
    fields.update(overrides)
    return LinearHead(**fields)


@pytest.mark.unit
def test_scores_shape_and_standardization():
    head = _head()
    x = np.array([[3.0, 4.0], [4.0, 8.0]], dtype=np.float32)  # row 0 is the mean

    scores = head.scores(x)

    assert scores.shape == (2, 2)
    assert scores.dtype == np.float32
    assert scores[0].tolist() == pytest.approx([0.5, _sigmoid(1.0)])
    # row 1 standardises to [1, 2]: logits [1, 2 + 1]
    assert scores[1].tolist() == pytest.approx([_sigmoid(1.0), _sigmoid(3.0)], rel=1e-5)


@pytest.mark.unit
def test_scores_accepts_float16_input():
    head = _head()
    x = np.array([[3.0, 4.0]], dtype=np.float16)
    assert head.scores(x)[0].tolist() == pytest.approx([0.5, _sigmoid(1.0)], rel=1e-3)


@pytest.mark.unit
def test_save_load_round_trip(tmp_path):
    head = _head(meta={"model": "m", "split": "image_id % 10", "epochs_run": 3})
    head.save(tmp_path / "head.npz")

    loaded = LinearHead.load(tmp_path / "head.npz")

    assert np.array_equal(loaded.weight, head.weight)
    assert np.array_equal(loaded.bias, head.bias)
    assert np.array_equal(loaded.mean, head.mean)
    assert np.array_equal(loaded.std, head.std)
    assert loaded.tag_ids.tolist() == [10, 20]
    assert loaded.thresholds.tolist() == [0.5, 0.5]
    assert loaded.meta == {"model": "m", "split": "image_id % 10", "epochs_run": 3}


@pytest.mark.unit
def test_standardization_stats_matches_numpy_and_floors_std():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(100, 4)).astype(np.float16)
    x[:, 3] = 2.0  # a constant feature
    rows = np.arange(0, 100, 3)

    mean, std = standardization_stats(x, rows, chunk=16)

    reference = x[rows].astype(np.float32)
    assert mean.dtype == np.float32 and std.dtype == np.float32
    assert np.allclose(mean[:3], reference[:, :3].mean(axis=0), atol=1e-5)
    assert np.allclose(std[:3], reference[:, :3].std(axis=0), atol=1e-5)
    assert mean[3] == pytest.approx(2.0)
    assert std[3] == pytest.approx(1e-6)


@pytest.mark.unit
def test_train_config_defaults():
    config = TrainConfig()
    assert (config.lr, config.batch_size, config.l2) == (1e-3, 4096, 1e-4)
    assert (config.max_epochs, config.patience, config.seed) == (20, 2, 0)


@pytest.mark.unit
def test_train_recovers_planted_separator():
    rng = np.random.default_rng(0)
    d, k, n = 16, 3, 6000
    x = rng.normal(size=(n, d)).astype(np.float16)
    w_true = rng.normal(size=(d, k))
    b_true = rng.normal(size=k)
    y = (x.astype(np.float32) @ w_true + b_true) > 0
    train, val = np.arange(0, 5000), np.arange(5000, 6000)
    mean, std = standardization_stats(x, train)
    logged: list[str] = []

    head = train_linear_head(
        x,
        y,
        train,
        val,
        tag_ids=np.arange(k),
        mean=mean,
        std=std,
        config=TrainConfig(max_epochs=30, batch_size=256, lr=1e-2),
        log=logged.append,
    )

    pred = head.scores(x[val].astype(np.float32)) >= 0.5
    m = confusion(pred, y[val])
    assert m.f1.min() > 0.95
    assert head.weight.shape == (d, k) and head.bias.shape == (k,)
    assert head.tag_ids.tolist() == [0, 1, 2]
    assert head.thresholds.tolist() == [0.5, 0.5, 0.5], "thresholds are chosen later, by the CLI"
    assert 1 <= head.meta["epochs_run"] <= 30
    assert head.meta["best_val_loss"] < 0.2
    assert head.meta["config"]["lr"] == 1e-2
    assert any("epoch" in line for line in logged)


@pytest.mark.unit
def test_train_early_stops_on_flat_validation_loss():
    # Labels independent of features: validation loss cannot improve, so patience ends training.
    rng = np.random.default_rng(3)
    x = rng.normal(size=(2000, 4)).astype(np.float16)
    y = rng.random((2000, 2)) < 0.5
    train, val = np.arange(0, 1500), np.arange(1500, 2000)
    mean, std = standardization_stats(x, train)

    head = train_linear_head(
        x,
        y,
        train,
        val,
        tag_ids=np.array([1, 2]),
        mean=mean,
        std=std,
        config=TrainConfig(max_epochs=50, patience=2, batch_size=128, lr=1e-2),
        log=lambda line: None,
    )

    assert head.meta["epochs_run"] < 50
