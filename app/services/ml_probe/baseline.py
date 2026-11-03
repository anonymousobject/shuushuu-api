"""The mapping path, reconstructed offline from the raw prediction store.

This is the deployed suggestion mechanism scored the same way as the probe:
raw Danbooru-vocabulary predictions for the model, translated through
``tag_mappings`` (confidence times the mapping's confidence), aliases
collapsed onto canonical theme columns, and the maximum kept where several
external tags map to one internal tag. The image-state-dependent steps of the
live pipeline (redundancy filter, already-applied exclusion) are deliberately
left out: they read the image's current tags, which are the ground truth.

Scores are not closed upward here; the comparison closes both systems'
scores the same way at evaluation time.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions
from app.models.tag_mapping import TagMappings
from app.services.ml_probe.labels import ThemeTagGraph

_STREAM_CHUNK = 100_000


@dataclass
class BaselineScores:
    """Mapped confidences for a set of images over the canonical theme tags."""

    image_ids: NDArray[np.int64]
    scores: NDArray[np.float32]
    has_raw: NDArray[np.bool_]

    def save(self, path: Path) -> None:
        np.savez_compressed(
            path, image_ids=self.image_ids, scores=self.scores, has_raw=self.has_raw
        )

    @classmethod
    def load(cls, path: Path) -> BaselineScores:
        with np.load(path) as data:
            return cls(
                image_ids=data["image_ids"].astype(np.int64),
                scores=data["scores"].astype(np.float32),
                has_raw=data["has_raw"].astype(np.bool_),
            )


async def _mapping_lookup(
    db: AsyncSession, graph: ThemeTagGraph
) -> tuple[NDArray[np.int64], NDArray[np.float32]]:
    """Dense external_tag_id -> (theme column or -1, mapping confidence)."""
    rows = (
        await db.execute(
            select(MlExternalTags.id, TagMappings.internal_tag_id, TagMappings.confidence)  # type: ignore[call-overload]
            .join(TagMappings, TagMappings.external_tag == MlExternalTags.name)
            .where(TagMappings.internal_tag_id.is_not(None))  # type: ignore[union-attr]
        )
    ).all()
    size = max((int(r[0]) for r in rows), default=0) + 1
    column = np.full(size, -1, dtype=np.int64)
    weight = np.ones(size, dtype=np.float32)
    for external_id, internal_id, confidence in rows:
        col = graph.column_of.get(int(internal_id), -1)
        if col >= 0:
            column[external_id] = col
            weight[external_id] = confidence
    return column, weight


async def build_baseline_scores(
    db: AsyncSession,
    graph: ThemeTagGraph,
    *,
    model_name: str,
    image_ids: NDArray[np.int64],
) -> BaselineScores:
    """Mapped scores for ``image_ids`` from the raw store rows of ``model_name``.

    Streams every raw row for the model and keeps the requested images, which
    is simpler than a huge IN list and takes a few minutes for the full store.
    """
    model_id = (
        await db.execute(select(MlModels.id).where(MlModels.name == model_name))  # type: ignore[call-overload]
    ).scalar_one_or_none()
    if model_id is None:
        raise ValueError(f"no ml_models row named {model_name!r}")

    requested = np.unique(np.asarray(image_ids, dtype=np.int64))
    scores = np.zeros((len(requested), len(graph.tag_ids)), dtype=np.float32)
    has_raw = np.zeros(len(requested), dtype=np.bool_)
    column, weight = await _mapping_lookup(db, graph)

    statement = select(  # type: ignore[call-overload]
        MlRawPredictions.image_id, MlRawPredictions.external_tag_id, MlRawPredictions.confidence
    ).where(MlRawPredictions.model_id == model_id)
    stream = await db.stream(statement.execution_options(yield_per=_STREAM_CHUNK))
    async for chunk in stream.partitions(_STREAM_CHUNK):
        n = len(chunk)
        images = np.fromiter((r[0] for r in chunk), dtype=np.int64, count=n)
        externals = np.fromiter((r[1] for r in chunk), dtype=np.int64, count=n)
        confidences = np.fromiter((r[2] for r in chunk), dtype=np.float32, count=n)

        rows = np.clip(np.searchsorted(requested, images), 0, max(len(requested) - 1, 0))
        known = (len(requested) > 0) & (requested[rows] == images)
        has_raw[rows[known]] = True

        in_range = externals < len(column)
        cols = np.where(in_range, column[np.clip(externals, 0, len(column) - 1)], -1)
        valid = known & (cols >= 0)
        mapped = confidences[valid] * weight[externals[valid]]
        np.maximum.at(scores, (rows[valid], cols[valid]), mapped)

    return BaselineScores(image_ids=requested, scores=scores, has_raw=has_raw)
