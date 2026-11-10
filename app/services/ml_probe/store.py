"""Reader for the append-only embedding store written by scripts/ml_gpu_infer.py.

Layout of a store directory:

- ``embeddings.f16``  raw little-endian float16, row-major ``[N, dim]``
- ``embeddings.ids``  raw int64 ``[N]``, the image id of each row, in write order
- ``embeddings.meta.json``  ``model``, ``dim``, ``dtype`` and run metadata

The writer appends a vector and then its id, flushing after each, so a crash
can only leave a trailing partial record. Both the writer and this reader
therefore trust the shorter of the two files; see ``consistent_row_count``.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

EMBEDDING_DIM = 1024
VECTORS_FILE = "embeddings.f16"
IDS_FILE = "embeddings.ids"
META_FILE = "embeddings.meta.json"

_VECTOR_ITEMSIZE = np.dtype(np.float16).itemsize
_ID_ITEMSIZE = np.dtype(np.int64).itemsize


@dataclass
class EmbeddingStore:
    """A read-only view of one store directory."""

    ids: NDArray[np.int64]
    vectors: np.memmap
    meta: dict[str, Any]

    def rows_for(self, image_ids: NDArray[np.int64]) -> NDArray[np.int64]:
        """Row index per requested id, -1 where the id is not in the store."""
        order = np.argsort(self.ids, kind="stable")
        position, found = locate_sorted(self.ids[order], np.asarray(image_ids, dtype=np.int64))
        rows = np.full(len(position), -1, dtype=np.int64)
        rows[found] = order[position[found]]
        return rows


def locate_sorted(
    sorted_ids: NDArray[np.int64], query: NDArray[np.int64]
) -> tuple[NDArray[np.int64], NDArray[np.bool_]]:
    """Position of each query id in ``sorted_ids`` plus a found mask.

    Positions are clipped into range so callers can index with them directly
    after masking; an empty ``sorted_ids`` finds nothing. The one lookup every
    id join in this package uses.
    """
    sorted_ids = np.asarray(sorted_ids, dtype=np.int64)
    query = np.asarray(query, dtype=np.int64)
    if len(sorted_ids) == 0:
        return np.zeros(len(query), dtype=np.int64), np.zeros(len(query), dtype=np.bool_)
    position = np.clip(np.searchsorted(sorted_ids, query), 0, len(sorted_ids) - 1)
    found: NDArray[np.bool_] = sorted_ids[position] == query
    return position.astype(np.int64), found


def consistent_row_count(directory: Path, dim: int) -> int:
    """Rows present in both files: min(ids, full vectors); 0 when either file is absent."""
    vectors_path = directory / VECTORS_FILE
    ids_path = directory / IDS_FILE
    if not vectors_path.exists() or not ids_path.exists():
        return 0
    vector_rows = vectors_path.stat().st_size // (dim * _VECTOR_ITEMSIZE)
    id_rows = ids_path.stat().st_size // _ID_ITEMSIZE
    return min(vector_rows, id_rows)


def open_embedding_store(directory: Path) -> EmbeddingStore:
    """Memmap the consistent prefix of a store. Raises FileNotFoundError without meta."""
    meta_path = directory / META_FILE
    if not meta_path.exists():
        raise FileNotFoundError(f"embedding store meta not found: {meta_path}")
    meta: dict[str, Any] = json.loads(meta_path.read_text())
    dim = int(meta["dim"])
    rows = consistent_row_count(directory, dim)
    ids = np.fromfile(directory / IDS_FILE, dtype=np.int64, count=rows)
    vectors = np.memmap(directory / VECTORS_FILE, dtype=np.float16, mode="r", shape=(rows, dim))
    return EmbeddingStore(ids=ids, vectors=vectors, meta=meta)
