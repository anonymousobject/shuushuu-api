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

    # --- Auto-linking raw URLs ---

    def test_autolink_bare_https_url(self):
        result = parse_markdown("Check https://example.com for details")
        assert '<a href="https://example.com"' in result
        assert ">https://example.com</a>" in result
        assert 'rel="nofollow noopener"' in result

    def test_autolink_bare_http_url(self):
        result = parse_markdown("Visit http://example.com today")
        assert '<a href="http://example.com"' in result
        assert ">http://example.com</a>" in result

    def test_autolink_url_with_path(self):
        result = parse_markdown("See https://example.com/foo/bar")
        assert '<a href="https://example.com/foo/bar"' in result

    def test_autolink_url_with_query_string(self):
        result = parse_markdown("https://example.com/search?q=test&page=2")
        assert '<a href="https://example.com/search?q=test&amp;page=2"' in result

    def test_autolink_url_with_fragment(self):
        result = parse_markdown("https://example.com/page#section")
        assert '<a href="https://example.com/page#section"' in result

    def test_autolink_strips_trailing_period(self):
        result = parse_markdown("Visit https://example.com.")
        assert '<a href="https://example.com"' in result
        assert ">https://example.com</a>." in result

    def test_autolink_strips_trailing_comma(self):
        result = parse_markdown("See https://example.com, then go")
        assert '<a href="https://example.com"' in result
        assert ">https://example.com</a>," in result

    def test_autolink_strips_trailing_exclamation(self):
        result = parse_markdown("Wow https://example.com!")
        assert '<a href="https://example.com"' in result
        assert ">https://example.com</a>!" in result

    def test_autolink_does_not_double_link_explicit_markdown(self):
        """Explicit [text](url) should not get auto-linked again."""
        result = parse_markdown("[click here](https://example.com)")
        assert result.count("</a>") == 1

    def test_autolink_does_not_link_url_display_text_in_explicit_link(self):
        """[https://example.com](https://other.com) - display URL shouldn't double-link."""
        result = parse_markdown("[https://example.com](https://other.com)")
        assert result.count("</a>") == 1
        assert 'href="https://other.com"' in result

    def test_autolink_youtube_url(self):
        """Real-world case from image 1112025."""
        result = parse_markdown("https://www.youtube.com/watch?v=QzahOY6liiI")
        assert '<a href="https://www.youtube.com/watch?v=QzahOY6liiI"' in result

    def test_autolink_eshuushuu_url_in_multiline(self):
        """Real-world case from image 1112025."""
        text = "https://e-shuushuu.net/image/827934/\nIt took him 10 years!"
        result = parse_markdown(text)
        assert '<a href="https://e-shuushuu.net/image/827934/"' in result
        assert "It took him 10 years!" in result

    def test_autolink_multiple_urls(self):
        text = "Check https://foo.com and https://bar.com"
        result = parse_markdown(text)
        assert '<a href="https://foo.com"' in result
        assert '<a href="https://bar.com"' in result
        assert result.count("</a>") == 2

    def test_autolink_no_nested_anchors_with_url_in_link_text(self):
        """[check https://foo.com here](https://bar.com) must not produce nested <a> tags."""
        result = parse_markdown("[check https://foo.com here](https://bar.com)")
        assert result.count("<a ") == 1
        assert 'href="https://bar.com"' in result

    def test_autolink_url_wrapped_in_bold(self):
        """**https://example.com** should bold the link, not mangle the <a> tag."""
        result = parse_markdown("**https://example.com**")
        assert '<a href="https://example.com"' in result
        assert "<strong>" in result
        # The href must not contain ** or </strong>
        assert "**" not in result

    def test_autolink_url_wrapped_in_italic(self):
        """*https://example.com* should italicize the link."""
        result = parse_markdown("*https://example.com*")
        assert '<a href="https://example.com"' in result
        assert "<em>" in result

    def test_autolink_url_ending_with_ampersand(self):
        """URL ending with & should not produce broken &amp entity."""
        result = parse_markdown("https://example.com?flag&")
        # The ; from &amp; must not be stripped
        assert "&amp" not in result or "&amp;" in result

    def test_autolink_url_with_parentheses(self):
        """Wikipedia-style URLs with parens."""
        result = parse_markdown("https://en.wikipedia.org/wiki/Foo_(bar)")
        assert '<a href="https://en.wikipedia.org/wiki/Foo_(bar)"' in result

    def test_autolink_url_at_line_start(self):
        result = parse_markdown("https://example.com")
        assert '<a href="https://example.com"' in result

    def test_autolink_coexists_with_bold(self):
        result = parse_markdown("**bold** and https://example.com")
        assert "<strong>bold</strong>" in result
        assert '<a href="https://example.com"' in result

    def test_autolink_coexists_with_blockquote(self):
        result = parse_markdown("> https://example.com")
        assert "<blockquote>" in result
        assert '<a href="https://example.com"' in result

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
