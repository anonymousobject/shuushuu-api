"""Unit tests for BBCode to markdown conversion."""

import pytest

from scripts.convert_bbcode_to_markdown import convert_bbcode_to_markdown


@pytest.mark.unit
class TestConvertBbcodeToMarkdown:
    def test_empty_text(self):
        assert convert_bbcode_to_markdown("") == ("", False)

    def test_none_text(self):
        assert convert_bbcode_to_markdown(None) == (None, False)

    def test_plain_text_unchanged(self):
        text = "hello world"
        assert convert_bbcode_to_markdown(text) == (text, False)

    def test_br_tag_to_newline(self):
        text, modified = convert_bbcode_to_markdown("line1<br />line2")
        assert text == "line1\nline2"
        assert modified is True

    def test_url_with_param(self):
        text, modified = convert_bbcode_to_markdown(
            '[url=http://example.com]click here[/url]'
        )
        assert text == "[click here](http://example.com)"
        assert modified is True

    def test_url_with_quoted_param(self):
        """BBCode [url="http://..."] with quotes around URL."""
        text, modified = convert_bbcode_to_markdown(
            '[url="http://example.com"]click here[/url]'
        )
        assert text == "[click here](http://example.com)"
        assert modified is True

    def test_url_plain(self):
        text, modified = convert_bbcode_to_markdown(
            "[url]http://example.com[/url]"
        )
        assert text == "[http://example.com](http://example.com)"
        assert modified is True

    def test_spoiler_plain(self):
        text, modified = convert_bbcode_to_markdown(
            "[spoiler]hidden text[/spoiler]"
        )
        assert text == "[spoiler]\nhidden text\n[/spoiler]"
        assert modified is True

    def test_spoiler_with_title(self):
        text, modified = convert_bbcode_to_markdown(
            '[spoiler="source"]hidden text[/spoiler]'
        )
        assert text == "[spoiler: source]\nhidden text\n[/spoiler]"
        assert modified is True

    def test_html_entity_normalization(self):
        text, modified = convert_bbcode_to_markdown("&quot;hello&quot;")
        assert text == '"hello"'
        assert modified is True

    def test_already_broken_markdown_link_with_quoted_url(self):
        """Fix previously-converted links that have quotes around the URL."""
        text, modified = convert_bbcode_to_markdown(
            '[click here]("http://example.com")'
        )
        assert text == "[click here](http://example.com)"
        assert modified is True

    def test_already_broken_markdown_link_single_quotes(self):
        """Fix previously-converted links with single quotes around URL."""
        text, modified = convert_bbcode_to_markdown(
            "[click here]('http://example.com')"
        )
        assert text == "[click here](http://example.com)"
        assert modified is True

    def test_already_broken_markdown_link_mixed_with_text(self):
        """Fix broken markdown links embedded in surrounding text."""
        text, modified = convert_bbcode_to_markdown(
            'Check [tags here]("http://e-shuushuu.net/about/tags/") for info'
        )
        assert text == "Check [tags here](http://e-shuushuu.net/about/tags/) for info"
        assert modified is True

    def test_multiple_broken_markdown_links(self):
        """Fix multiple broken markdown links in one text."""
        text, modified = convert_bbcode_to_markdown(
            '[a]("http://x.com") and [b]("http://y.com")'
        )
        assert text == "[a](http://x.com) and [b](http://y.com)"
        assert modified is True

    def test_correct_markdown_link_unchanged(self):
        """Already-correct markdown links should not be modified."""
        text = "[click here](http://example.com)"
        result_text, modified = convert_bbcode_to_markdown(text)
        assert result_text == text
        assert modified is False

    def test_real_php_bbcode_with_html_entities(self):
        """Test with actual BBCode from the legacy PHP database."""
        php_text = (
            'Welcome. You can have an idea of the  '
            '[url=&quot;http://e-shuushuu.net/about/tags/&quot;] tags here[/url]'
            ' as tagging them are necessary and our '
            '[url=&quot;http://e-shuushuu.net/about/rules/&quot;]rules here.[/url]'
            ' If you have any questions, please do not hesitate to ask a '
            'tagging team member [orange name] or a moderator [pink/red name]. '
            'I hope you enjoy your stay.\n\n'
            "We don&#039;t allow AI-generated images on our board though, "
            "so I will need to disable these images."
        )
        text, modified = convert_bbcode_to_markdown(php_text)
        assert modified is True
        # URLs should have quotes stripped and &quot; decoded
        assert "[ tags here](http://e-shuushuu.net/about/tags/)" in text
        assert "[rules here.](http://e-shuushuu.net/about/rules/)" in text
        # No quoted URLs in the output
        assert '("http://' not in text
        assert '"http://e-shuushuu.net' not in text
