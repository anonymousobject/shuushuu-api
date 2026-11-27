"""The image-id split: deterministic, recomputable, no stored file."""

import numpy as np
import pytest

from app.services.ml_probe.split import TEST_FOLD, VAL_FOLD, fold_of


@pytest.mark.unit
def test_fold_of_is_image_id_mod_ten():
    assert fold_of(np.array([10, 11, 29, 1000001])).tolist() == [0, 1, 9, 1]
    assert fold_of(np.array([10])).dtype == np.uint8


@pytest.mark.unit
def test_fold_constants():
    assert TEST_FOLD == 0
    assert VAL_FOLD == 1
