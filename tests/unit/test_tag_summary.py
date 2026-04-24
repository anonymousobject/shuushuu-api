"""TagSummary schema tests — usage_count field."""

from app.models.tag import Tags
from app.schemas.image import TagSummary


class TestTagSummaryUsageCount:
    def test_usage_count_populated_from_orm(self):
        tag = Tags(tag_id=1, title="t", type=1, usage_count=42)
        summary = TagSummary.model_validate(tag)
        assert summary.usage_count == 42

    def test_usage_count_defaults_to_zero(self):
        # Validate from a dict without usage_count — should default.
        summary = TagSummary.model_validate(
            {"tag_id": 1, "title": "t", "type": 1}
        )
        assert summary.usage_count == 0
