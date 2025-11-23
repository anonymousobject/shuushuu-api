"""
Tests for safe Markdown parser
"""

import pytest

from app.utils.markdown import is_safe_url, parse_markdown, strip_markdown


class TestIsSafeUrl:
    """Tests for URL safety validation"""

    def test_http_url_is_safe(self):
        assert is_safe_url("http://example.com")

    def test_https_url_is_safe(self):
        assert is_safe_url("https://example.com")

    def test_protocol_relative_url_is_safe(self):
        assert is_safe_url("//example.com")

    def test_javascript_url_is_unsafe(self):
        assert not is_safe_url("javascript:alert('xss')")

    def test_data_url_is_unsafe(self):
        assert not is_safe_url("data:text/html,<script>alert('xss')</script>")

    def test_file_url_is_unsafe(self):
        assert not is_safe_url("file:///etc/passwd")

    def test_vbscript_url_is_unsafe(self):
        assert not is_safe_url("vbscript:msgbox('xss')")


class TestParseMarkdown:
    """Tests for Markdown to HTML conversion"""

    def test_bold_text(self):
        result = parse_markdown("This is **bold** text")
        assert result == "This is <strong>bold</strong> text"

    def test_italic_text(self):
        result = parse_markdown("This is *italic* text")
        assert result == "This is <em>italic</em> text"

    def test_bold_and_italic(self):
        result = parse_markdown("**bold** and *italic*")
        assert result == "<strong>bold</strong> and <em>italic</em>"

    def test_link(self):
        result = parse_markdown("[example](https://example.com)")
        assert 'href="https://example.com"' in result
        assert ">example</a>" in result
        assert 'rel="nofollow noopener"' in result

    def test_link_with_ampersand(self):
        result = parse_markdown("[search](https://example.com/search?q=test&page=2)")
        assert 'href="https://example.com/search?q=test&amp;page=2"' in result

    def test_unsafe_javascript_link_not_rendered(self):
        result = parse_markdown("[click me](javascript:alert('xss'))")
        # Should not create a link
        assert "<a href" not in result
        assert "[click me](javascript:alert(&#x27;xss&#x27;))" in result

    def test_blockquote(self):
        result = parse_markdown("> This is a quote")
        assert result == "<blockquote>This is a quote</blockquote>"

    def test_multiline_blockquote(self):
        result = parse_markdown("> Line 1\n> Line 2")
        assert "<blockquote>Line 1 Line 2</blockquote>" in result

    def test_line_breaks(self):
        result = parse_markdown("Line 1\nLine 2")
        assert "Line 1<br>" in result
        assert "Line 2" in result

    def test_html_is_escaped(self):
        result = parse_markdown("<script>alert('xss')</script>")
        assert "&lt;script&gt;" in result
        assert "<script>" not in result

    def test_html_in_bold_is_escaped(self):
        result = parse_markdown("**<script>alert('xss')</script>**")
        assert "&lt;script&gt;" in result
        assert "<strong>" in result
        assert "<script>" not in result

    def test_empty_string(self):
        assert parse_markdown("") == ""
        assert parse_markdown(None) == ""

    def test_complex_formatting(self):
        text = """This is **bold** and *italic*.
> A quote with a [link](https://example.com)
Another line."""
        result = parse_markdown(text)
        assert "<strong>bold</strong>" in result
        assert "<em>italic</em>" in result
        assert "<blockquote>" in result
        assert 'href="https://example.com"' in result

    def test_nested_bold_italic_not_supported(self):
        # We don't support nested formatting like ***text***
        # This is acceptable for our use case
        result = parse_markdown("***text***")
        # Exact output depends on regex matching order
        # Just verify no script injection
        assert "<script>" not in result


class TestStripMarkdown:
    """Tests for stripping Markdown formatting"""

    def test_strip_bold(self):
        assert strip_markdown("**bold text**") == "bold text"

    def test_strip_italic(self):
        assert strip_markdown("*italic text*") == "italic text"

    def test_strip_link(self):
        assert strip_markdown("[example](https://example.com)") == "example"

    def test_strip_blockquote(self):
        assert strip_markdown("> quote") == "quote"

    def test_strip_complex(self):
        text = "**bold** and *italic* with [link](https://example.com)"
        result = strip_markdown(text)
        assert result == "bold and italic with link"

    def test_strip_empty_string(self):
        assert strip_markdown("") == ""
        assert strip_markdown(None) == ""
