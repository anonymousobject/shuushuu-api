"""
Raw ML prediction store.

Persists the model's raw per-image predictions (external Danbooru-vocabulary
tags + confidence) so that changing tag_mappings can re-surface suggestions via
a cheap re-map instead of full re-inference. ml_external_tags and ml_models are
small dictionaries; ml_raw_predictions is the large fact table.
"""

from sqlalchemy import Column, ForeignKey, Integer, SmallInteger, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.types import UnsignedInt, UnsignedSmallInt


class MlExternalTags(SQLModel, table=True):
    """Dictionary of the model vocabulary: external tag name + its category."""

    __tablename__ = "ml_external_tags"
    __table_args__ = (UniqueConstraint("name", name="unique_ml_external_tag_name"),)

    # INT UNSIGNED to match the migration (edb3f5912896); ml_raw_predictions.external_tag_id
    # below must carry the same type or create_all's FK fails with errno 150.
    id: int | None = Field(
        default=None,
        sa_column=Column(UnsignedInt, primary_key=True, autoincrement=True),
    )
    name: str = Field(max_length=255)
    category: int = Field(sa_column=Column(SmallInteger, nullable=False))


class MlModels(SQLModel, table=True):
    """Dictionary of ML model versions (e.g. caformer_b36.dbv4-full)."""

    __tablename__ = "ml_models"
    __table_args__ = (UniqueConstraint("name", name="unique_ml_model_name"),)

    # SMALLINT UNSIGNED to match the migration (edb3f5912896); ml_raw_predictions.model_id
    # below must carry the same type or create_all's FK fails with errno 150.
    id: int | None = Field(
        default=None,
        sa_column=Column(UnsignedSmallInt, primary_key=True, autoincrement=True),
    )
    name: str = Field(max_length=100)


class MlRawPredictions(SQLModel, table=True):
    """One row per (image, model, predicted external tag). Composite PK."""

    __tablename__ = "ml_raw_predictions"

    # Note: images.image_id is signed in this model's metadata (pre-existing drift
    # from the legacy unsigned schema, tracked in
    # docs/plans/2026-06-10-schema-sync-signed-unsigned-drift.md), so this FK stays
    # signed Integer to match it — unsigning it alone would break create_all with
    # the same errno 150 this file otherwise fixes.
    image_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("images.image_id", ondelete="CASCADE", onupdate="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    model_id: int = Field(
        sa_column=Column(
            UnsignedSmallInt,
            ForeignKey("ml_models.id", ondelete="CASCADE", onupdate="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    external_tag_id: int = Field(
        sa_column=Column(
            UnsignedInt,
            ForeignKey("ml_external_tags.id", ondelete="CASCADE", onupdate="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    confidence: float = Field(ge=0.0, le=1.0)
