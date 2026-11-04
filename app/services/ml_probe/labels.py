"""Theme-tag label matrix with per-pair provenance.

One row per active image, one column per canonical theme tag. Each cell
records where the (image, tag) pair came from, because the review queue has
been writing ``tag_links`` since June 2026 and the evaluation has to be able
to mask that feedback loop out (design §2b):

- ``LINKED``   a tag link exists and no approved suggestion is behind it
- ``APPROVED`` a tag link exists and an approved ML suggestion created it
- ``PENDING``  an unreviewed suggestion, no link
- ``REJECTED`` a rejected suggestion, no link
- ``NONE``     nothing

Positives are the links (``LINKED`` or ``APPROVED``) closed upward through
the tag hierarchy: a child tag implies its ancestors, which is how the site's
own suggestion pipeline treats parents.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagType
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.services.ml_probe.artifacts import npz_path
from app.services.ml_probe.store import locate_sorted

NONE = 0
LINKED = 1
APPROVED = 2
PENDING = 3
REJECTED = 4


@dataclass
class ThemeTagGraph:
    """Canonical theme tags, their parents, and a map from every theme tag id to a column."""

    tag_ids: NDArray[np.int64]
    titles: list[str]
    parent_index: NDArray[np.int64]
    column_of: dict[int, int]


def close_upward(matrix: NDArray[Any], parent_index: NDArray[np.int64]) -> NDArray[Any]:
    """Propagate each column into its parent column until nothing changes.

    Boolean matrices use OR, numeric ones MAX, so a predicted or labelled child
    makes its ancestors predicted or labelled too. Returns a copy.
    """
    closed = np.array(matrix, copy=True)
    combine = np.logical_or if closed.dtype == np.bool_ else np.maximum
    children = [int(c) for c in np.flatnonzero(np.asarray(parent_index) >= 0)]
    changed = True
    while changed:
        changed = False
        for child in children:
            parent = int(parent_index[child])
            merged = combine(closed[:, parent], closed[:, child])
            if not np.array_equal(merged, closed[:, parent]):
                closed[:, parent] = merged
                changed = True
    return closed


@dataclass
class LabelView:
    """Masks for a row/column subset of a LabelSet, all computed from one pass."""

    positives: NDArray[np.bool_]
    approved_mask: NDArray[np.bool_]
    pending_mask: NDArray[np.bool_]
    rejected: NDArray[np.bool_]
    provenance: NDArray[np.uint8]


@dataclass
class LabelSet:
    """Provenance matrix for a set of images over the canonical theme tags."""

    image_ids: NDArray[np.int64]
    tag_ids: NDArray[np.int64]
    titles: list[str]
    parent_index: NDArray[np.int64]
    provenance: NDArray[np.uint8]

    def positives(self) -> NDArray[np.bool_]:
        """Linked pairs (human or approved), closed upward."""
        linked = (self.provenance == LINKED) | (self.provenance == APPROVED)
        closed: NDArray[np.bool_] = close_upward(linked, self.parent_index)
        return closed

    def human_positives(self) -> NDArray[np.bool_]:
        """Positives that survive with every approved-suggestion link removed."""
        closed: NDArray[np.bool_] = close_upward(self.provenance == LINKED, self.parent_index)
        return closed

    def approved_mask(self) -> NDArray[np.bool_]:
        """Positives that exist only because the baseline suggested them (headline mask)."""
        return self.positives() & ~self.human_positives()

    def pending_mask(self) -> NDArray[np.bool_]:
        """Unreviewed suggestions that are not positives: unknown, not negative."""
        pending: NDArray[np.bool_] = self.provenance == PENDING
        return pending & ~self.positives()

    def rejected(self) -> NDArray[np.bool_]:
        """Confirmed negatives: rejected suggestions that are not positives."""
        rejected: NDArray[np.bool_] = self.provenance == REJECTED
        return rejected & ~self.positives()

    def view(self, rows: NDArray[np.int64], columns: NDArray[np.int64]) -> LabelView:
        """Every mask for a row/column subset, from two closures instead of four.

        The CLI slices to the images that are also in the embedding store and to
        the tags the head was trained on; computing the closures once on the full
        matrix and slicing afterwards is what keeps eval's memory in check.
        """
        positives = self.positives()
        human = self.human_positives()
        pending: NDArray[np.bool_] = self.provenance == PENDING
        rejected: NDArray[np.bool_] = self.provenance == REJECTED

        def cut(matrix: NDArray[Any]) -> NDArray[Any]:
            return matrix[rows][:, columns]

        return LabelView(
            positives=cut(positives),
            approved_mask=cut(positives & ~human),
            pending_mask=cut(pending & ~positives),
            rejected=cut(rejected & ~positives),
            provenance=cut(self.provenance),
        )

    def save(self, path: Path) -> None:
        np.savez_compressed(
            npz_path(path),
            image_ids=self.image_ids,
            tag_ids=self.tag_ids,
            titles=np.array(self.titles, dtype=str),
            parent_index=self.parent_index,
            provenance=self.provenance,
        )

    @classmethod
    def load(cls, path: Path) -> LabelSet:
        with np.load(npz_path(path)) as data:
            return cls(
                image_ids=data["image_ids"].astype(np.int64),
                tag_ids=data["tag_ids"].astype(np.int64),
                titles=[str(t) for t in data["titles"]],
                parent_index=data["parent_index"].astype(np.int64),
                provenance=data["provenance"].astype(np.uint8),
            )


_STREAM_CHUNK = 100_000


async def load_theme_tag_graph(db: AsyncSession) -> ThemeTagGraph:
    """Canonical theme tags as columns, with aliases and parents resolved onto them."""
    rows = (
        await db.execute(
            select(Tags.tag_id, Tags.title, Tags.alias_of, Tags.inheritedfrom_id).where(  # type: ignore[call-overload]
                Tags.type == TagType.THEME
            )
        )
    ).all()
    canonical = sorted((r for r in rows if r.alias_of is None), key=lambda r: r.tag_id)
    column_of: dict[int, int] = {r.tag_id: i for i, r in enumerate(canonical)}
    for r in rows:
        if r.alias_of is not None and r.alias_of in column_of:
            column_of[r.tag_id] = column_of[r.alias_of]
    parent_index = np.full(len(canonical), -1, dtype=np.int64)
    for i, r in enumerate(canonical):
        if r.inheritedfrom_id is not None:
            parent_index[i] = column_of.get(r.inheritedfrom_id, -1)
    return ThemeTagGraph(
        tag_ids=np.array([r.tag_id for r in canonical], dtype=np.int64),
        titles=[r.title or "" for r in canonical],
        parent_index=parent_index,
        column_of=column_of,
    )


def _column_lookup(graph: ThemeTagGraph) -> NDArray[np.int64]:
    """Dense tag_id -> column array (-1 where the tag is not a theme tag), for vectorised joins."""
    lookup = np.full(max(graph.column_of, default=0) + 1, -1, dtype=np.int64)
    for tag_id, column in graph.column_of.items():
        lookup[tag_id] = column
    return lookup


def _locate(
    image_ids: NDArray[np.int64],
    lookup: NDArray[np.int64],
    chunk: Sequence[Row[Any]],
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.bool_]]:
    """Row and column index for each (image_id, tag_id) pair in a chunk, plus a validity mask."""
    pair_images = np.fromiter((r[0] for r in chunk), dtype=np.int64, count=len(chunk))
    pair_tags = np.fromiter((r[1] for r in chunk), dtype=np.int64, count=len(chunk))
    rows, known_image = locate_sorted(image_ids, pair_images)
    known_tag = pair_tags < len(lookup)
    cols = np.where(known_tag, lookup[np.clip(pair_tags, 0, len(lookup) - 1)], -1)
    valid = known_image & (cols >= 0)
    return rows[valid], cols[valid], valid


async def build_label_set(
    db: AsyncSession, graph: ThemeTagGraph, *, status: int = ImageStatus.ACTIVE
) -> LabelSet:
    """Provenance matrix for every image with the given status and a filename.

    Pass 1 streams theme-tag links and marks LINKED. Pass 2 streams the ML
    suggestions: an approved one behind an existing link upgrades it to
    APPROVED; a pending or rejected one on an unlinked pair records PENDING or
    REJECTED; anything else (a link with a pending suggestion, an approved
    suggestion whose link was since removed) leaves the cell as it is. Images
    with no positive after closure are dropped.
    """
    image_filter = (Images.status == status, Images.filename.is_not(None))  # type: ignore[union-attr]
    image_rows = (
        await db.execute(
            select(Images.image_id).where(*image_filter).order_by(Images.image_id)  # type: ignore[call-overload]
        )
    ).all()
    image_ids = np.array([r[0] for r in image_rows], dtype=np.int64)
    provenance = np.zeros((len(image_ids), len(graph.tag_ids)), dtype=np.uint8)
    lookup = _column_lookup(graph)

    links = (
        select(TagLinks.image_id, TagLinks.tag_id)  # type: ignore[call-overload]
        .join(Images, Images.image_id == TagLinks.image_id)
        .join(Tags, Tags.tag_id == TagLinks.tag_id)
        .where(Tags.type == TagType.THEME, *image_filter)
    )
    stream = await db.stream(links.execution_options(yield_per=_STREAM_CHUNK))
    async for chunk in stream.partitions(_STREAM_CHUNK):
        rows, cols, _ = _locate(image_ids, lookup, chunk)
        provenance[rows, cols] = LINKED

    suggestions = (
        select(MlTagSuggestions.image_id, MlTagSuggestions.tag_id, MlTagSuggestions.status)  # type: ignore[call-overload]
        .join(Images, Images.image_id == MlTagSuggestions.image_id)
        .join(Tags, Tags.tag_id == MlTagSuggestions.tag_id)
        .where(Tags.type == TagType.THEME, *image_filter)
    )
    stream = await db.stream(suggestions.execution_options(yield_per=_STREAM_CHUNK))
    async for chunk in stream.partitions(_STREAM_CHUNK):
        rows, cols, valid = _locate(image_ids, lookup, chunk)
        statuses = np.array([r[2] for r in chunk], dtype=object)[valid]
        current = provenance[rows, cols]
        upgrade = (statuses == "approved") & (current == LINKED)
        provenance[rows[upgrade], cols[upgrade]] = APPROVED
        pending = (statuses == "pending") & (current == NONE)
        provenance[rows[pending], cols[pending]] = PENDING
        rejected = (statuses == "rejected") & (current == NONE)
        provenance[rows[rejected], cols[rejected]] = REJECTED

    labels = LabelSet(
        image_ids=image_ids,
        tag_ids=graph.tag_ids,
        titles=graph.titles,
        parent_index=graph.parent_index,
        provenance=provenance,
    )
    keep = labels.positives().any(axis=1)
    labels.image_ids = image_ids[keep]
    labels.provenance = provenance[keep]
    return labels
