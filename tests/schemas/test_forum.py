"""Forum schema validation tests."""

import pytest
from pydantic import ValidationError

from app.schemas.forum import (
    ForumCategoryCreate,
    ForumCategoryUpdate,
    ForumPostCreate,
    ForumThreadCreate,
)


class TestForumCategoryPermWhitelist:
    def test_valid_access_perm_accepted(self):
        cat = ForumCategoryCreate(title="Mod Board", view_perm="forum_access_staff")
        assert cat.view_perm == "forum_access_staff"

    def test_null_perm_accepted(self):
        cat = ForumCategoryCreate(title="Public")
        assert cat.view_perm is None

    @pytest.mark.parametrize("field", ["view_perm", "thread_create_perm", "reply_perm"])
    def test_non_access_perm_rejected(self, field):
        with pytest.raises(ValidationError):
            ForumCategoryCreate(**{"title": "Bad", field: "user_ban"})

    def test_update_schema_rejects_non_access_perm(self):
        with pytest.raises(ValidationError):
            ForumCategoryUpdate(reply_perm="forum_moderate")


class TestForumTextValidation:
    def test_thread_title_and_text_stripped(self):
        t = ForumThreadCreate(title="  Hello  ", post_text="  body  ")
        assert t.title == "Hello"
        assert t.post_text == "body"

    def test_empty_post_text_rejected(self):
        with pytest.raises(ValidationError):
            ForumPostCreate(post_text="")

    def test_title_max_length(self):
        with pytest.raises(ValidationError):
            ForumThreadCreate(title="x" * 256, post_text="body")


class TestForumPostHtml:
    def test_post_text_html_renders_markdown_and_quote(self):
        from app.schemas.common import UserSummary
        from app.schemas.forum import ForumPostResponse

        post = ForumPostResponse(
            post_id=1,
            thread_id=1,
            user_id=1,
            post_text='**bold** [quote="alice"]hi[/quote]',
            date="2026-07-06T00:00:00Z",
            deleted=False,
            update_count=0,
            user=UserSummary(user_id=1, username="testuser"),
        )
        assert "<strong>bold</strong>" in post.post_text_html
        assert "blockquote" in post.post_text_html
