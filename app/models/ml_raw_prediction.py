"""
Raw ML prediction store.

Persists the model's raw per-image predictions (external Danbooru-vocabulary
tags + confidence) so that changing tag_mappings can re-surface suggestions via
a cheap re-map instead of full re-inference. ml_external_tags and ml_models are
small dictionaries; ml_raw_predictions is the large fact table.
"""

from sqlalchemy import Column, ForeignKey, Integer, SmallInteger, UniqueConstraint
from sqlmodel import Field, SQLModel


class MlExternalTags(SQLModel, table=True):
    """Dictionary of the model vocabulary: external tag name + its category."""

    __tablename__ = "ml_external_tags"
    __table_args__ = (UniqueConstraint("name", name="unique_ml_external_tag_name"),)

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=255)
    category: int = Field(sa_column=Column(SmallInteger, nullable=False))


class MlModels(SQLModel, table=True):
    """Dictionary of ML model versions (e.g. caformer_b36.dbv4-full)."""

    __tablename__ = "ml_models"
    __table_args__ = (UniqueConstraint("name", name="unique_ml_model_name"),)

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=100)


class MlRawPredictions(SQLModel, table=True):
    """One row per (image, model, predicted external tag). Composite PK."""

    __tablename__ = "ml_raw_predictions"

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
            SmallInteger,
            ForeignKey("ml_models.id", ondelete="CASCADE", onupdate="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    external_tag_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("ml_external_tags.id", ondelete="CASCADE", onupdate="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    confidence: float = Field(ge=0.0, le=1.0)
