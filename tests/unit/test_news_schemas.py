"""Tests for news API schemas."""

import pytest
from pydantic import ValidationError


class TestNewsCreate:
    """Validate NewsCreate schema."""

    def test_valid_create(self):
        from app.schemas.news import NewsCreate

        data = NewsCreate(title="Test News", news_text="Some content")
        assert data.title == "Test News"
        assert data.news_text == "Some content"

    def test_title_required(self):
        from app.schemas.news import NewsCreate

        with pytest.raises(ValidationError):
            NewsCreate(news_text="content")  # type: ignore[call-arg]

    def test_news_text_required(self):
        from app.schemas.news import NewsCreate

        with pytest.raises(ValidationError):
            NewsCreate(title="Title")  # type: ignore[call-arg]

    def test_title_max_length(self):
        from app.schemas.news import NewsCreate

        with pytest.raises(ValidationError):
            NewsCreate(title="x" * 129, news_text="content")

    def test_title_strips_whitespace(self):
        from app.schemas.news import NewsCreate

        data = NewsCreate(title="  padded  ", news_text="content")
        assert data.title == "padded"

    def test_news_text_strips_whitespace(self):
        from app.schemas.news import NewsCreate

        data = NewsCreate(title="Title", news_text="  padded  ")
        assert data.news_text == "padded"

    def test_whitespace_only_news_text_rejected(self):
        from app.schemas.news import NewsCreate

        with pytest.raises(ValidationError):
            NewsCreate(title="Title", news_text="   ")

    def test_whitespace_only_title_rejected(self):
        from app.schemas.news import NewsCreate

        with pytest.raises(ValidationError):
            NewsCreate(title="   ", news_text="content")


class TestNewsUpdate:
    """Validate NewsUpdate schema."""

    def test_update_title_only(self):
        from app.schemas.news import NewsUpdate

        data = NewsUpdate(title="New Title")
        assert data.title == "New Title"
        assert data.news_text is None

    def test_update_text_only(self):
        from app.schemas.news import NewsUpdate

        data = NewsUpdate(news_text="New text")
        assert data.news_text == "New text"
        assert data.title is None

    def test_update_both(self):
        from app.schemas.news import NewsUpdate

        data = NewsUpdate(title="Title", news_text="Text")
        assert data.title == "Title"
        assert data.news_text == "Text"

    def test_update_empty_rejected(self):
        from app.schemas.news import NewsUpdate

        with pytest.raises(ValidationError):
            NewsUpdate()

    def test_update_title_max_length(self):
        from app.schemas.news import NewsUpdate

        with pytest.raises(ValidationError):
            NewsUpdate(title="x" * 129)


class TestNewsResponse:
    """Validate NewsResponse schema."""

    def test_response_from_dict(self):
        from datetime import datetime

        from app.schemas.news import NewsResponse

        data = NewsResponse(
            news_id=1,
            user_id=1,
            username="testuser",
            title="Test",
            news_text="Content",
            date=datetime(2026, 1, 1),
            edited=None,
        )
        assert data.news_id == 1
        assert data.username == "testuser"
