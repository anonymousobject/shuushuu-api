"""Tests for the embedding store reader (app/services/ml_probe/store.py).

The store is two raw files plus a JSON sidecar, written by the app-free GPU
runner and read here. These tests hand-write the files so the reader is
exercised independently of the writer; the round trip through the writer
lives next to the runner in tests/integration/test_ml_gpu_infer.py.
"""

import json

import numpy as np
import pytest

from app.services.ml_probe.store import (
    IDS_FILE,
    META_FILE,
    VECTORS_FILE,
    consistent_row_count,
    open_embedding_store,
)


def _write_store(directory, ids, vectors, dim):
    (directory / META_FILE).write_text(json.dumps({"model": "m", "dim": dim, "dtype": "float16"}))
    np.asarray(ids, dtype=np.int64).tofile(directory / IDS_FILE)
    np.asarray(vectors, dtype=np.float16).tofile(directory / VECTORS_FILE)


@pytest.mark.unit
def test_open_store_reads_rows_and_meta(tmp_path):
    _write_store(tmp_path, [5, 9], [[0, 1, 2, 3], [1, 1, 1, 1]], dim=4)

    store = open_embedding_store(tmp_path)

    assert store.ids.tolist() == [5, 9]
    assert store.vectors.shape == (2, 4)
    assert store.vectors.dtype == np.float16
    assert np.allclose(store.vectors[0], [0, 1, 2, 3])
    assert store.meta == {"model": "m", "dim": 4, "dtype": "float16"}
    assert store.rows_for(np.array([9, 5, 7])).tolist() == [1, 0, -1]


@pytest.mark.unit
def test_consistent_row_count_uses_shorter_file(tmp_path):
    _write_store(tmp_path, [1, 2, 3], [[0] * 4, [1] * 4], dim=4)
    with open(tmp_path / VECTORS_FILE, "ab") as f:
        f.write(b"\x00\x01\x02")  # a partial third vector

    assert consistent_row_count(tmp_path, dim=4) == 2


@pytest.mark.unit
def test_consistent_row_count_is_zero_without_files(tmp_path):
    assert consistent_row_count(tmp_path, dim=4) == 0


@pytest.mark.unit
def test_open_store_truncates_view_to_consistent_rows(tmp_path):
    _write_store(tmp_path, [1, 2, 3], [[0] * 4, [1] * 4], dim=4)

    store = open_embedding_store(tmp_path)

    assert store.ids.tolist() == [1, 2]
    assert store.vectors.shape == (2, 4)


@pytest.mark.unit
def test_open_store_without_meta_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_embedding_store(tmp_path)
