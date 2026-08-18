import pytest

from app.config import settings
from app.schemas.image import FavoriteAttribution, RecommendedImageResponse, TagSummary

pytestmark = [pytest.mark.unit]


def test_new_taste_settings_defaults():
    assert settings.TASTE_SCORE_POOL == 1500
    assert settings.TASTE_FAV_SHARE == 0.33
    assert settings.TASTE_FAV_PER_FAVORITE_CAP == 100
    assert settings.TASTE_SAMPLE_DECAY == 0.997


def test_favorite_attribution_shapes():
    tag = FavoriteAttribution(tag=TagSummary(tag_id=1, title="ask", type=3))
    assert tag.character is None and tag.source is None
    combo = FavoriteAttribution(
        character=TagSummary(tag_id=2, title="C.C.", type=4),
        source=TagSummary(tag_id=3, title="Code Geass", type=2),
    )
    assert combo.tag is None
    assert combo.model_dump(by_alias=True)["character"]["title"] == "C.C."


def test_recommended_image_response_defaults_to_no_attribution():
    fields = RecommendedImageResponse.model_fields
    assert fields["because_favorite"].default is None
