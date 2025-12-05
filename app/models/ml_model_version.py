"""
ML Model Version Model

Tracks deployed ML model versions for tag suggestion system.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Column, Field, SQLModel


class MLModelVersion(SQLModel, table=True):
    """
    Tracks ML model deployments and versions.

    Workers query for is_active=True to find current model.
    """

    __tablename__ = "ml_model_versions"
    __table_args__ = (
        UniqueConstraint("model_name", "version", name="uq_model_name_version"),
    )

    version_id: int | None = Field(default=None, primary_key=True)
    model_name: str = Field(max_length=100, index=True)  # 'custom_theme', 'danbooru'
    version: str = Field(max_length=50)  # 'v1', 'v2', 'wd14_v2'
    file_path: str = Field(max_length=500)
    is_active: bool = Field(default=False, index=True)
    deployed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metrics: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
