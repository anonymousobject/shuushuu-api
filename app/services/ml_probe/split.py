"""Deterministic train / validation / test split on image id.

Image ids are chronological and dense, so ``image_id % 10`` is a uniform
random split that anyone can recompute without a stored file. Fold 0 is test,
fold 1 is validation, folds 2–9 train.
"""

import numpy as np
from numpy.typing import NDArray

TEST_FOLD = 0
VAL_FOLD = 1


def fold_of(image_ids: NDArray[np.int64]) -> NDArray[np.uint8]:
    """Fold number per image: ``image_id % 10``."""
    return (np.asarray(image_ids, dtype=np.int64) % 10).astype(np.uint8)
