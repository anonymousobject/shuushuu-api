"""A numpy logistic head over frozen embeddings, and the loop that trains it.

Independent sigmoid per tag on standardised features; binary cross-entropy
with Adam; a small L2 penalty on the weights; early stopping on validation
loss. Minibatches are gathered from any ``[N, D]`` indexable, including a
float16 memmap of the full embedding store, with each batch's row indices
sorted so a memmap read is sequential-ish. Nothing here needs torch.
"""

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.services.ml_probe.artifacts import npz_path

_STD_FLOOR = np.float32(1e-6)
_ADAM_BETA1, _ADAM_BETA2, _ADAM_EPS = 0.9, 0.999, 1e-8


def _sigmoid(z: NDArray[np.float32]) -> NDArray[np.float32]:
    # tanh form: no overflow warnings for large |z|
    out: NDArray[np.float32] = (0.5 * (1.0 + np.tanh(0.5 * z))).astype(np.float32)
    return out


def _bce_from_logits(z: NDArray[np.float32], y: NDArray[np.float32]) -> float:
    """Mean binary cross-entropy, computed from logits so it is stable at any |z|."""
    loss = np.maximum(z, 0) - z * y + np.log1p(np.exp(-np.abs(z)))
    return float(loss.mean())


@dataclass
class TrainConfig:
    lr: float = 1e-3
    batch_size: int = 4096
    l2: float = 1e-4
    max_epochs: int = 20
    patience: int = 2
    seed: int = 0


@dataclass
class LinearHead:
    """Weights plus everything needed to apply them to a raw embedding."""

    weight: NDArray[np.float32]
    bias: NDArray[np.float32]
    mean: NDArray[np.float32]
    std: NDArray[np.float32]
    tag_ids: NDArray[np.int64]
    thresholds: NDArray[np.float32]
    meta: dict[str, Any]

    def scores(self, x: NDArray[np.floating]) -> NDArray[np.float32]:
        """Per-tag probabilities for raw (unstandardised) embeddings ``[n, D]``."""
        standardised = (np.asarray(x, dtype=np.float32) - self.mean) / self.std
        logits: NDArray[np.float32] = (standardised @ self.weight + self.bias).astype(np.float32)
        return _sigmoid(logits)

    def save(self, path: Path) -> None:
        np.savez(
            npz_path(path),
            weight=self.weight,
            bias=self.bias,
            mean=self.mean,
            std=self.std,
            tag_ids=self.tag_ids,
            thresholds=self.thresholds,
            meta=np.array(json.dumps(self.meta)),
        )

    @classmethod
    def load(cls, path: Path) -> LinearHead:
        with np.load(npz_path(path)) as data:
            return cls(
                weight=data["weight"].astype(np.float32),
                bias=data["bias"].astype(np.float32),
                mean=data["mean"].astype(np.float32),
                std=data["std"].astype(np.float32),
                tag_ids=data["tag_ids"].astype(np.int64),
                thresholds=data["thresholds"].astype(np.float32),
                meta=json.loads(str(data["meta"])),
            )


def standardization_stats(
    x: Any, rows: NDArray[np.int64], *, chunk: int = 65_536
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """Per-feature mean and (population) std over ``x[rows]``, read in chunks.

    The std is floored at 1e-6 so a constant feature divides to zero rather
    than exploding.
    """
    rows = np.sort(np.asarray(rows, dtype=np.int64))
    total = np.zeros(x.shape[1], dtype=np.float64)
    total_sq = np.zeros(x.shape[1], dtype=np.float64)
    for start in range(0, len(rows), chunk):
        batch = np.asarray(x[rows[start : start + chunk]], dtype=np.float64)
        total += batch.sum(axis=0)
        total_sq += np.square(batch).sum(axis=0)
    mean = total / len(rows)
    variance = np.maximum(total_sq / len(rows) - np.square(mean), 0.0)
    std = np.maximum(np.sqrt(variance).astype(np.float32), _STD_FLOOR)
    return mean.astype(np.float32), std


def train_linear_head(
    x: Any,
    y: NDArray[np.bool_],
    train_rows: NDArray[np.int64],
    val_rows: NDArray[np.int64],
    *,
    tag_ids: NDArray[np.int64],
    mean: NDArray[np.float32],
    std: NDArray[np.float32],
    config: TrainConfig,
    log: Callable[[str], None] = print,
) -> LinearHead:
    """Train a logistic head; returns the weights from the best validation epoch.

    ``x`` is any ``[N, D]`` indexable (a memmap is fine); ``y`` is ``[N, K]``
    aligned with its rows. Thresholds in the returned head are 0.5
    placeholders; the caller chooses them on the validation fold.
    """
    rng = np.random.default_rng(config.seed)
    train_rows = np.asarray(train_rows, dtype=np.int64)
    val_rows = np.sort(np.asarray(val_rows, dtype=np.int64))
    n_features, n_tags = x.shape[1], y.shape[1]

    weight = np.zeros((n_features, n_tags), dtype=np.float32)
    bias = np.zeros(n_tags, dtype=np.float32)
    moments = [
        np.zeros_like(weight),
        np.zeros_like(weight),
        np.zeros_like(bias),
        np.zeros_like(bias),
    ]
    step = 0

    def standardised(rows: NDArray[np.int64]) -> NDArray[np.float32]:
        out: NDArray[np.float32] = (np.asarray(x[rows], dtype=np.float32) - mean) / std
        return out

    def validation_loss() -> float:
        total, count = 0.0, 0
        for start in range(0, len(val_rows), config.batch_size):
            rows = val_rows[start : start + config.batch_size]
            logits = standardised(rows) @ weight + bias
            total += _bce_from_logits(logits, y[rows].astype(np.float32)) * len(rows)
            count += len(rows)
        return total / count

    best_loss = float("inf")
    best = (weight.copy(), bias.copy())
    epochs_without_improvement = 0
    epochs_run = 0
    for epoch in range(1, config.max_epochs + 1):
        started = time.monotonic()
        permutation = rng.permutation(train_rows)
        train_total = 0.0
        for start in range(0, len(permutation), config.batch_size):
            rows = np.sort(permutation[start : start + config.batch_size])
            features = standardised(rows)
            targets = y[rows].astype(np.float32)
            logits = features @ weight + bias
            train_total += _bce_from_logits(logits, targets) * len(rows)
            gradient = (_sigmoid(logits) - targets) / len(rows)
            grads = [features.T @ gradient + config.l2 * weight, gradient.sum(axis=0)]
            step += 1
            for i, (param, grad) in enumerate(zip((weight, bias), grads, strict=True)):
                m, v = moments[2 * i], moments[2 * i + 1]
                m[...] = _ADAM_BETA1 * m + (1 - _ADAM_BETA1) * grad
                v[...] = _ADAM_BETA2 * v + (1 - _ADAM_BETA2) * np.square(grad)
                m_hat = m / (1 - _ADAM_BETA1**step)
                v_hat = v / (1 - _ADAM_BETA2**step)
                param -= (config.lr * m_hat / (np.sqrt(v_hat) + _ADAM_EPS)).astype(np.float32)
        epochs_run = epoch
        val_loss = validation_loss()
        log(
            f"epoch {epoch}: train_loss={train_total / len(permutation):.4f} "
            f"val_loss={val_loss:.4f} ({time.monotonic() - started:.1f}s)"
        )
        if val_loss < best_loss - 1e-6:
            best_loss = val_loss
            best = (weight.copy(), bias.copy())
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= config.patience:
                break

    return LinearHead(
        weight=best[0],
        bias=best[1],
        mean=np.asarray(mean, dtype=np.float32),
        std=np.asarray(std, dtype=np.float32),
        tag_ids=np.asarray(tag_ids, dtype=np.int64),
        thresholds=np.full(n_tags, 0.5, dtype=np.float32),
        meta={
            "config": asdict(config),
            "epochs_run": epochs_run,
            "best_val_loss": best_loss,
            "n_train": int(len(train_rows)),
            "n_val": int(len(val_rows)),
        },
    )
