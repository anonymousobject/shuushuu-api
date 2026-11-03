"""Label matrix with provenance (app/services/ml_probe/labels.py).

Pure tests cover the closure and the masks on hand-built matrices. The DB
tests build a tiny tag graph and a few images in the test database and run
the real queries.
"""

import numpy as np
import pytest

from app.services.ml_probe.labels import (
    APPROVED,
    LINKED,
    NONE,
    PENDING,
    REJECTED,
    LabelSet,
    close_upward,
)


@pytest.mark.unit
def test_provenance_codes():
    assert (NONE, LINKED, APPROVED, PENDING, REJECTED) == (0, 1, 2, 3, 4)


@pytest.mark.unit
def test_close_upward_bool_two_levels():
    # column 2's parent is 1, column 1's parent is 0; a positive at 2 implies 1 and 0
    parent_index = np.array([-1, 0, 1])
    matrix = np.array([[False, False, True], [False, True, False], [False, False, False]])

    closed = close_upward(matrix, parent_index)

    assert closed.tolist() == [[True, True, True], [True, True, False], [False, False, False]]
    assert matrix[0].tolist() == [False, False, True], "input is not mutated"


@pytest.mark.unit
def test_close_upward_float_takes_max():
    parent_index = np.array([-1, 0])
    scores = np.array([[0.1, 0.9], [0.7, 0.2]], dtype=np.float32)

    closed = close_upward(scores, parent_index)

    assert np.allclose(closed, [[0.9, 0.9], [0.7, 0.2]])
    assert scores[0, 0] == pytest.approx(0.1), "input is not mutated"


@pytest.mark.unit
def test_close_upward_without_parents_is_identity():
    matrix = np.array([[True, False]])
    assert close_upward(matrix, np.array([-1, -1])).tolist() == [[True, False]]


def _label_set(provenance, parent_index):
    provenance = np.asarray(provenance, dtype=np.uint8)
    k = provenance.shape[1]
    return LabelSet(
        image_ids=np.arange(1, provenance.shape[0] + 1, dtype=np.int64),
        tag_ids=np.arange(100, 100 + k, dtype=np.int64),
        titles=[f"t{i}" for i in range(k)],
        parent_index=np.asarray(parent_index, dtype=np.int64),
        provenance=provenance,
    )


@pytest.mark.unit
def test_label_set_masks_with_approved_child_and_pending():
    # tags: A (parent of B), B, C. One image: A untouched, B approved-suggestion link, C pending.
    labels = _label_set([[NONE, APPROVED, PENDING]], parent_index=[-1, 0, -1])

    assert labels.positives().tolist() == [[True, True, False]]
    assert labels.human_positives().tolist() == [[False, False, False]]
    assert labels.approved_mask().tolist() == [[True, True, False]]
    assert labels.pending_mask().tolist() == [[False, False, True]]
    assert labels.rejected().tolist() == [[False, False, False]]


@pytest.mark.unit
def test_label_set_human_link_is_not_masked_and_rejected_is_a_negative():
    # A linked by a human, B rejected suggestion
    labels = _label_set([[LINKED, REJECTED]], parent_index=[-1, -1])

    assert labels.positives().tolist() == [[True, False]]
    assert labels.approved_mask().tolist() == [[False, False]]
    assert labels.rejected().tolist() == [[False, True]]


@pytest.mark.unit
def test_label_set_pending_under_a_linked_child_is_a_positive_not_pending():
    # A is B's parent; B linked by a human; A has a pending suggestion. Closure makes A positive.
    labels = _label_set([[PENDING, LINKED]], parent_index=[-1, 0])

    assert labels.positives().tolist() == [[True, True]]
    assert labels.pending_mask().tolist() == [[False, False]]


@pytest.mark.unit
def test_label_set_save_load_round_trip(tmp_path):
    labels = _label_set([[LINKED, NONE], [NONE, APPROVED]], parent_index=[-1, 0])
    path = tmp_path / "labels.npz"

    labels.save(path)
    loaded = LabelSet.load(path)

    assert loaded.image_ids.tolist() == [1, 2]
    assert loaded.tag_ids.tolist() == [100, 101]
    assert loaded.titles == ["t0", "t1"]
    assert loaded.parent_index.tolist() == [-1, 0]
    assert loaded.provenance.tolist() == labels.provenance.tolist()
    assert loaded.provenance.dtype == np.uint8
