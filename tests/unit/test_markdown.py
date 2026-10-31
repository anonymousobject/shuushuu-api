"""
Tests for safe Markdown parser
"""

import time

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

    def test_link_text_starts_at_leftmost_open_bracket(self):
        result = parse_markdown("[a[b](https://x.com)")
        assert result == '<a href="https://x.com" rel="nofollow noopener" target="_blank">a[b</a>'

    def test_link_after_empty_link_text(self):
        result = parse_markdown("[](x)[a](https://x.com)")
        assert (
            result == '[](x)<a href="https://x.com" rel="nofollow noopener" target="_blank">a</a>'
        )

    def test_link_after_bracket_pair_without_url(self):
        result = parse_markdown("[a][b](https://x.com)")
        assert result == '[a]<a href="https://x.com" rel="nofollow noopener" target="_blank">b</a>'

    def test_link_with_empty_url_not_rendered(self):
        assert parse_markdown("[x]()") == "[x]()"

    def test_link_url_runs_to_first_closing_paren(self):
        result = parse_markdown("[a](b)(c)")
        assert result == '<a href="b" rel="nofollow noopener" target="_blank">a</a>(c)'

    def test_link_url_may_contain_brackets(self):
        result = parse_markdown("[x](a](b)")
        assert result == '<a href="a](b" rel="nofollow noopener" target="_blank">x</a>'

    def test_link_text_may_span_lines(self):
        result = parse_markdown("[a\nb](https://x.com)")
        assert (
            result == '<a href="https://x.com" rel="nofollow noopener" target="_blank">a<br>\nb</a>'
        )

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

    def test_nested_spoilers(self):
        result = parse_markdown("[spoiler]a [spoiler]b[/spoiler] c[/spoiler]")
        assert result == '<span class="spoiler">a <span class="spoiler">b</span> c</span>'

    def test_unclosed_spoiler_tag_is_removed(self):
        assert parse_markdown("[spoiler]unclosed") == "unclosed"

    def test_nested_bold_italic_not_supported(self):
        # We don't support nested formatting like ***text***
        # This is acceptable for our use case
        result = parse_markdown("***text***")
        # Exact output depends on regex matching order
        # Just verify no script injection
        assert "<script>" not in result


class TestParseMarkdownQuotes:
    """BBCode [quote] rendering: named, anonymous, nested and orphaned tags"""

    def test_named_quote(self):
        result = parse_markdown('[quote="alice"]hi[/quote]')
        assert result == "<blockquote><small>alice said:</small> hi</blockquote>"

    def test_named_quote_with_legacy_entity_encoded_name(self):
        result = parse_markdown("[quote=&quot;alice&quot;]hi[/quote]")
        assert result == "<blockquote><small>alice said:</small> hi</blockquote>"

    def test_named_quote_multiline_with_surrounding_text(self):
        result = parse_markdown('before [quote="alice"]hi\nthere[/quote] after')
        assert (
            result
            == "before <blockquote><small>alice said:</small> hi<br>\nthere</blockquote> after"
        )

    def test_sibling_named_quotes(self):
        result = parse_markdown('[quote="alice"]one[/quote] and [quote="bob"]two[/quote]')
        assert result == (
            "<blockquote><small>alice said:</small> one</blockquote> and "
            "<blockquote><small>bob said:</small> two</blockquote>"
        )

    def test_named_quote_content_is_escaped(self):
        result = parse_markdown("[quote=\"alice\"]<script>alert('xss')</script>[/quote]")
        assert result == (
            "<blockquote><small>alice said:</small> "
            "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;</blockquote>"
        )

    def test_named_quote_content_keeps_markdown(self):
        result = parse_markdown('[quote="alice"]**bold** [spoiler]s[/spoiler][/quote]')
        assert result == (
            "<blockquote><small>alice said:</small> "
            '<strong>bold</strong> <span class="spoiler">s</span></blockquote>'
        )

    def test_named_quote_inside_spoiler(self):
        result = parse_markdown('[spoiler][quote="alice"]x[/quote][/spoiler]')
        assert result == (
            '<span class="spoiler"><blockquote><small>alice said:</small> x</blockquote></span>'
        )

    def test_anonymous_quote(self):
        assert parse_markdown("[quote]anon[/quote]") == "<blockquote>anon</blockquote>"

    def test_sibling_anonymous_quotes(self):
        result = parse_markdown("[quote]a[/quote] [quote]b[/quote]")
        assert result == "<blockquote>a</blockquote> <blockquote>b</blockquote>"

    def test_nested_named_quotes(self):
        result = parse_markdown('[quote="alice"]outer [quote="bob"]inner[/quote] tail[/quote]')
        assert result == (
            "<blockquote><small>alice said:</small> outer "
            "<blockquote><small>bob said:</small> inner</blockquote> tail</blockquote>"
        )

    def test_three_deep_named_quotes(self):
        result = parse_markdown('[quote="a"][quote="b"][quote="c"]x[/quote]y[/quote]z[/quote]')
        assert result == (
            "<blockquote><small>a said:</small> "
            "<blockquote><small>b said:</small> "
            "<blockquote><small>c said:</small> x</blockquote>y</blockquote>z</blockquote>"
        )

    def test_anonymous_quote_inside_named_quote(self):
        result = parse_markdown('[quote="alice"]outer [quote]anon[/quote] tail[/quote]')
        assert result == (
            "<blockquote><small>alice said:</small> outer "
            "<blockquote>anon</blockquote> tail</blockquote>"
        )

    def test_named_quote_inside_anonymous_quote(self):
        result = parse_markdown('[quote]outer [quote="bob"]inner[/quote] tail[/quote]')
        assert result == (
            "<blockquote>outer "
            "<blockquote><small>bob said:</small> inner</blockquote> tail</blockquote>"
        )

    def test_unclosed_named_quote_is_left_as_text(self):
        result = parse_markdown('[quote="alice"]unclosed')
        assert result == "[quote=&quot;alice&quot;]unclosed"

    def test_unclosed_anonymous_quote_tag_is_removed(self):
        assert parse_markdown("[quote]unclosed") == "unclosed"

    def test_orphan_closing_tag_is_removed(self):
        assert parse_markdown("orphan close[/quote]") == "orphan close"

    def test_extra_closing_tag_after_named_quote_is_removed(self):
        result = parse_markdown('[quote="alice"]a[/quote][/quote]')
        assert result == "<blockquote><small>alice said:</small> a</blockquote>"

    def test_nested_anonymous_quotes(self):
        result = parse_markdown("[quote]outer [quote]inner[/quote] tail[/quote]")
        assert result == "<blockquote>outer <blockquote>inner</blockquote> tail</blockquote>"

    def test_named_quote_missing_a_closer_pairs_leftmost_opener_first(self):
        """Many legacy comments lost an inner quote's closer: the first closer
        follows the outer author's reply, so it must stay with the outer opener."""
        result = parse_markdown('[quote="alice"][quote="bob"]question\nanswer[/quote]\nreply')
        assert result == (
            "<blockquote><small>alice said:</small> [quote=&quot;bob&quot;]question<br>\n"
            "answer</blockquote><br>\nreply"
        )


# Linear parsing takes milliseconds on these inputs; a scan that restarts from
# every opener takes seconds. The gap leaves room for a loaded CI runner.
RENDER_TIME_LIMIT_SECONDS = 0.5


class TestParseMarkdownQuoteScaling:
    """Quote parsing stays linear in input length"""

    @pytest.mark.parametrize(
        "text",
        [
            '[quote="x"]' * 2000,
            '[quote="x"]' * 2000 + "[/quote]",
            '[quote="x"]' * 5900,
            '[quote="x"]' * 5900 + "[/quote]",
            "[/quote]" + '[quote="x"]' * 5900,
        ],
        ids=[
            "2000-openers",
            "2000-openers-one-closer",
            "5900-openers",
            "5900-openers-one-closer",
            "5900-openers-after-a-closer",
        ],
    )
    def test_many_named_quote_openers(self, text):
        start = time.perf_counter()
        parse_markdown(text)
        assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS

    def test_many_anonymous_quote_openers(self):
        start = time.perf_counter()
        parse_markdown("[quote]" * 10000)
        assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS


class TestParseMarkdownLinkScaling:
    """Link parsing stays linear in input length"""

    @pytest.mark.parametrize(
        "text",
        [
            "[" * 20000,
            "[" * 40000,
            "[" * 40000 + "]",
            "[" * 40000 + "](",
            "[x](" * 20000,
        ],
        ids=[
            "20000-open-brackets",
            "40000-open-brackets",
            "40000-open-brackets-one-close",
            "40000-open-brackets-one-close-and-paren",
            "20000-links-without-closing-paren",
        ],
    )
    def test_many_unfinished_links(self, text):
        start = time.perf_counter()
        parse_markdown(text)
        assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS


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
