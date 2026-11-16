"""
Tests for safe Markdown parser
"""

import gc
import itertools
import random
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from html.parser import HTMLParser

import pytest

from app.utils.markdown import parse_markdown, strip_markdown
from app.utils.markdown_inline import is_safe_url

v1 = partial(parse_markdown, version=1)


def _anchor(escaped_url: str, text: str | None = None) -> str:
    """The anchor an auto-linked URL renders to; both arguments are already HTML-escaped."""
    return (
        f'<a href="{escaped_url}" rel="nofollow noopener" target="_blank">'
        f"{escaped_url if text is None else text}</a>"
    )


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

    def test_inline_spoiler(self):
        assert v1("a [spoiler]b[/spoiler] c") == 'a <span class="spoiler">b</span> c'

    def test_multiline_spoiler(self):
        assert v1("[spoiler]a\nb[/spoiler]") == '<span class="spoiler">a<br>\nb</span>'

    def test_bold_text(self):
        result = v1("This is **bold** text")
        assert result == "This is <strong>bold</strong> text"

    def test_italic_text(self):
        result = v1("This is *italic* text")
        assert result == "This is <em>italic</em> text"

    def test_bold_and_italic(self):
        result = v1("**bold** and *italic*")
        assert result == "<strong>bold</strong> and <em>italic</em>"

    def test_link(self):
        result = v1("[example](https://example.com)")
        assert 'href="https://example.com"' in result
        assert ">example</a>" in result
        assert 'rel="nofollow noopener"' in result

    def test_link_with_ampersand(self):
        result = v1("[search](https://example.com/search?q=test&page=2)")
        assert 'href="https://example.com/search?q=test&amp;page=2"' in result

    @pytest.mark.parametrize("version", [1, 2])
    @pytest.mark.parametrize("url", ["https://a.example/*b*/c", "https://a.example/**b**/c"])
    def test_link_url_asterisks_not_rendered_as_emphasis(self, version, url):
        result = parse_markdown(f"[x]({url})", version=version)
        assert 'href="' + url.replace("*", "&#42;") + '"' in result
        assert "<em>" not in result
        assert "<strong>" not in result

    @pytest.mark.parametrize("version", [1, 2])
    def test_link_text_emphasis_still_rendered(self, version):
        result = parse_markdown("[**b**](https://a.example/)", version=version)
        assert 'href="https://a.example/"' in result
        assert "><strong>b</strong></a>" in result

    @pytest.mark.parametrize("version", [1, 2])
    def test_link_inside_bold_still_wrapped(self, version):
        result = parse_markdown("**see [x](https://a.example/) y**", version=version)
        assert result.startswith("<strong>see <a ")
        assert result.endswith("</a> y</strong>")

    def test_unsafe_javascript_link_not_rendered(self):
        result = v1("[click me](javascript:alert('xss'))")
        # Should not create a link
        assert "<a href" not in result
        assert "[click me](javascript:alert(&#x27;xss&#x27;))" in result

    def test_link_text_starts_at_leftmost_open_bracket(self):
        result = v1("[a[b](https://x.com)")
        assert result == '<a href="https://x.com" rel="nofollow noopener" target="_blank">a[b</a>'

    def test_link_after_empty_link_text(self):
        result = v1("[](x)[a](https://x.com)")
        assert (
            result == '[](x)<a href="https://x.com" rel="nofollow noopener" target="_blank">a</a>'
        )

    def test_link_after_bracket_pair_without_url(self):
        result = v1("[a][b](https://x.com)")
        assert result == '[a]<a href="https://x.com" rel="nofollow noopener" target="_blank">b</a>'

    def test_link_with_empty_url_not_rendered(self):
        assert v1("[x]()") == "[x]()"

    def test_link_url_runs_to_first_closing_paren(self):
        result = v1("[a](b)(c)")
        assert result == '<a href="b" rel="nofollow noopener" target="_blank">a</a>(c)'

    def test_link_url_may_contain_brackets(self):
        result = v1("[x](a](b)")
        assert result == '<a href="a](b" rel="nofollow noopener" target="_blank">x</a>'

    def test_link_text_may_span_lines(self):
        result = v1("[a\nb](https://x.com)")
        assert (
            result == '<a href="https://x.com" rel="nofollow noopener" target="_blank">a<br>\nb</a>'
        )

    def test_blockquote(self):
        result = v1("> This is a quote")
        assert result == "<blockquote>This is a quote</blockquote>"

    def test_multiline_blockquote(self):
        result = v1("> Line 1\n> Line 2")
        assert "<blockquote>Line 1 Line 2</blockquote>" in result

    def test_line_breaks(self):
        result = v1("Line 1\nLine 2")
        assert "Line 1<br>" in result
        assert "Line 2" in result

    def test_html_is_escaped(self):
        result = v1("<script>alert('xss')</script>")
        assert "&lt;script&gt;" in result
        assert "<script>" not in result

    def test_html_in_bold_is_escaped(self):
        result = v1("**<script>alert('xss')</script>**")
        assert "&lt;script&gt;" in result
        assert "<strong>" in result
        assert "<script>" not in result

    def test_empty_string(self):
        assert v1("") == ""
        assert v1(None) == ""

    def test_complex_formatting(self):
        text = """This is **bold** and *italic*.
> A quote with a [link](https://example.com)
Another line."""
        result = v1(text)
        assert "<strong>bold</strong>" in result
        assert "<em>italic</em>" in result
        assert "<blockquote>" in result
        assert 'href="https://example.com"' in result

    # --- Auto-linking raw URLs ---

    def test_autolink_bare_https_url(self):
        result = v1("Check https://example.com for details")
        assert '<a href="https://example.com"' in result
        assert ">https://example.com</a>" in result
        assert 'rel="nofollow noopener"' in result

    def test_autolink_bare_http_url(self):
        result = v1("Visit http://example.com today")
        assert '<a href="http://example.com"' in result
        assert ">http://example.com</a>" in result

    def test_autolink_url_with_path(self):
        result = v1("See https://example.com/foo/bar")
        assert '<a href="https://example.com/foo/bar"' in result

    def test_autolink_url_with_query_string(self):
        result = v1("https://example.com/search?q=test&page=2")
        assert '<a href="https://example.com/search?q=test&amp;page=2"' in result

    def test_autolink_url_with_fragment(self):
        result = v1("https://example.com/page#section")
        assert '<a href="https://example.com/page#section"' in result

    def test_autolink_strips_trailing_period(self):
        result = v1("Visit https://example.com.")
        assert '<a href="https://example.com"' in result
        assert ">https://example.com</a>." in result

    def test_autolink_strips_trailing_comma(self):
        result = v1("See https://example.com, then go")
        assert '<a href="https://example.com"' in result
        assert ">https://example.com</a>," in result

    def test_autolink_strips_trailing_exclamation(self):
        result = v1("Wow https://example.com!")
        assert '<a href="https://example.com"' in result
        assert ">https://example.com</a>!" in result

    def test_autolink_does_not_double_link_explicit_markdown(self):
        """Explicit [text](url) should not get auto-linked again."""
        result = v1("[click here](https://example.com)")
        assert result.count("</a>") == 1

    def test_autolink_does_not_link_url_display_text_in_explicit_link(self):
        """[https://example.com](https://other.com) - display URL shouldn't double-link."""
        result = v1("[https://example.com](https://other.com)")
        assert result.count("</a>") == 1
        assert 'href="https://other.com"' in result

    def test_autolink_youtube_url(self):
        """Real-world case from image 1112025."""
        result = v1("https://www.youtube.com/watch?v=QzahOY6liiI")
        assert '<a href="https://www.youtube.com/watch?v=QzahOY6liiI"' in result

    def test_autolink_eshuushuu_url_in_multiline(self):
        """Real-world case from image 1112025."""
        text = "https://e-shuushuu.net/image/827934/\nIt took him 10 years!"
        result = v1(text)
        assert '<a href="https://e-shuushuu.net/image/827934/"' in result
        assert "It took him 10 years!" in result

    def test_autolink_multiple_urls(self):
        text = "Check https://foo.com and https://bar.com"
        result = v1(text)
        assert '<a href="https://foo.com"' in result
        assert '<a href="https://bar.com"' in result
        assert result.count("</a>") == 2

    def test_autolink_no_nested_anchors_with_url_in_link_text(self):
        """[check https://foo.com here](https://bar.com) must not produce nested <a> tags."""
        result = v1("[check https://foo.com here](https://bar.com)")
        assert result.count("<a ") == 1
        assert 'href="https://bar.com"' in result

    def test_autolink_url_wrapped_in_bold(self):
        """**https://example.com** should bold the link, not mangle the <a> tag."""
        result = v1("**https://example.com**")
        assert '<a href="https://example.com"' in result
        assert "<strong>" in result
        # The href must not contain ** or </strong>
        assert "**" not in result

    def test_autolink_url_wrapped_in_italic(self):
        """*https://example.com* should italicize the link."""
        result = v1("*https://example.com*")
        assert '<a href="https://example.com"' in result
        assert "<em>" in result

    def test_autolink_url_ending_with_ampersand(self):
        """URL ending with & should not produce broken &amp entity."""
        result = v1("https://example.com?flag&")
        # The ; from &amp; must not be stripped
        assert "&amp" not in result or "&amp;" in result

    def test_autolink_url_with_parentheses(self):
        """Wikipedia-style URLs with parens."""
        result = v1("https://en.wikipedia.org/wiki/Foo_(bar)")
        assert '<a href="https://en.wikipedia.org/wiki/Foo_(bar)"' in result

    def test_autolink_url_at_line_start(self):
        result = v1("https://example.com")
        assert '<a href="https://example.com"' in result

    def test_autolink_coexists_with_bold(self):
        result = v1("**bold** and https://example.com")
        assert "<strong>bold</strong>" in result
        assert '<a href="https://example.com"' in result

    def test_autolink_coexists_with_blockquote(self):
        result = v1("> https://example.com")
        assert "<blockquote>" in result
        assert '<a href="https://example.com"' in result

    @pytest.mark.parametrize("version", [1, 2])
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                'see "https://a.example/x" ok',
                f"see &quot;{_anchor('https://a.example/x')}&quot; ok",
            ),
            ("<https://a.example/x>", f"&lt;{_anchor('https://a.example/x')}&gt;"),
            ("https://a.example/<b>", f"{_anchor('https://a.example/')}&lt;b&gt;"),
            ("https://a.example/it's", _anchor("https://a.example/it&#x27;s")),
            (
                "'https://a.example/x'",
                f"&#x27;{_anchor('https://a.example/x')}&#x27;",
            ),
            # A typed entity is unescaped by escape_source, so it is a real "<"
            ("https://a.example/?q=&lt;", f"{_anchor('https://a.example/?q=')}&lt;"),
            ("https://a.example/?a=1&b=2", _anchor("https://a.example/?a=1&amp;b=2")),
        ],
    )
    def test_autolink_next_to_quotes_and_brackets(self, version, source, expected):
        assert parse_markdown(source, version=version) == expected

    @pytest.mark.parametrize("version", [1, 2])
    @pytest.mark.parametrize(
        ("path", "shown"),
        [
            (
                "/w/%EC%B9%B8%EC%9E%90%ED%82%A4%20%EB%9E%80%EC%BD%94/"
                "%EC%95%84%EC%9D%B4%EB%8F%8C%20%EB%A7%88%EC%8A%A4%ED%84%B0%20"
                "%EC%8B%A0%EB%8D%B0%EB%A0%90%EB%9D%BC%20%EA%B1%B8%EC%A6%88#s-2.3",
                "/w/칸자키%20란코/아이돌%20마스터%20신데렐라%20걸즈#s-2.3",
            ),
            ("/%ec%b9%b8", "/칸"),  # lowercase hex
            ("/%F0%9F%98%80", "/😀"),  # 4 bytes
            ("/%C3%A9", "/é"),  # 2 bytes
            ("/%EC%B9%B8%EC%9E%90", "/칸자"),  # valid sequences back to back
            # ASCII escapes are never decoded
            ("/a%2Fb%23c%25d%2Ae%3Cf%22g", "/a%2Fb%23c%25d%2Ae%3Cf%22g"),
            # Invalid UTF-8 stays as typed
            ("/%83%4A", "/%83%4A"),  # Shift-JIS
            ("/%EC%B9", "/%EC%B9"),  # truncated
            ("/%C0%AF", "/%C0%AF"),  # overlong
            ("/%ED%A0%80", "/%ED%A0%80"),  # encoded surrogate
            ("/%80", "/%80"),  # lone continuation byte
            ("/%83%4A%EC%B9%B8", "/%83%4A칸"),  # valid after invalid
            ("/%EC%B9%EC%B9%B8", "/%EC%B9칸"),  # valid after a truncated sequence
            # Controls, format, private-use and space characters stay encoded
            ("/%E2%80%AE", "/%E2%80%AE"),  # right-to-left override
            ("/%E2%80%8B", "/%E2%80%8B"),  # zero-width space
            ("/%C2%A0", "/%C2%A0"),  # no-break space
            ("/%E3%80%80", "/%E3%80%80"),  # ideographic space
            ("/%C2%85", "/%C2%85"),  # C1 control
            ("/%EE%80%80", "/%EE%80%80"),  # private use
        ],
    )
    def test_autolink_decodes_percent_escaped_text_for_display_only(self, version, path, shown):
        url = f"https://a.example{path}"
        result = parse_markdown(url, version=version)
        assert result == _anchor(url, f"https://a.example{shown}")

    def test_autolink_display_decoding_cannot_forge_v2_placeholder(self):
        url = "https://a.example/%EE%80%800%EE%80%81"
        result = parse_markdown(f"`code` {url}", version=2)
        assert result.count("<code>code</code>") == 1
        assert f">{url}</a>" in result

    @pytest.mark.parametrize("version", [1, 2])
    def test_explicit_link_not_decoded(self, version):
        result = parse_markdown("[x](https://a.example/%EC%B9%B8)", version=version)
        assert result == _anchor("https://a.example/%EC%B9%B8", "x")

    @pytest.mark.parametrize("version", [1, 2])
    def test_autolink_strips_trailing_punctuation_after_decoded_url(self, version):
        result = parse_markdown("see https://a.example/%EC%B9%B8.", version=version)
        assert (
            result == "see " + _anchor("https://a.example/%EC%B9%B8", "https://a.example/칸") + "."
        )

    def test_nested_spoilers(self):
        result = v1("[spoiler]a [spoiler]b[/spoiler] c[/spoiler]")
        assert result == '<span class="spoiler">a <span class="spoiler">b</span> c</span>'

    def test_unclosed_spoiler_tag_is_removed(self):
        assert v1("[spoiler]unclosed") == "unclosed"

    def test_nested_bold_italic_not_supported(self):
        # We don't support nested formatting like ***text***
        # This is acceptable for our use case
        result = v1("***text***")
        # Exact output depends on regex matching order
        # Just verify no script injection
        assert "<script>" not in result


class TestParseMarkdownQuotes:
    """BBCode [quote] rendering: named, anonymous, nested and orphaned tags"""

    def test_named_quote(self):
        result = v1('[quote="alice"]hi[/quote]')
        assert result == "<blockquote><small>alice said:</small> hi</blockquote>"

    def test_named_quote_with_legacy_entity_encoded_name(self):
        result = v1("[quote=&quot;alice&quot;]hi[/quote]")
        assert result == "<blockquote><small>alice said:</small> hi</blockquote>"

    def test_named_quote_multiline_with_surrounding_text(self):
        result = v1('before [quote="alice"]hi\nthere[/quote] after')
        assert (
            result
            == "before <blockquote><small>alice said:</small> hi<br>\nthere</blockquote> after"
        )

    def test_sibling_named_quotes(self):
        result = v1('[quote="alice"]one[/quote] and [quote="bob"]two[/quote]')
        assert result == (
            "<blockquote><small>alice said:</small> one</blockquote> and "
            "<blockquote><small>bob said:</small> two</blockquote>"
        )

    def test_named_quote_content_is_escaped(self):
        result = v1("[quote=\"alice\"]<script>alert('xss')</script>[/quote]")
        assert result == (
            "<blockquote><small>alice said:</small> "
            "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;</blockquote>"
        )

    def test_named_quote_content_keeps_markdown(self):
        result = v1('[quote="alice"]**bold** [spoiler]s[/spoiler][/quote]')
        assert result == (
            "<blockquote><small>alice said:</small> "
            '<strong>bold</strong> <span class="spoiler">s</span></blockquote>'
        )

    def test_named_quote_inside_spoiler(self):
        result = v1('[spoiler][quote="alice"]x[/quote][/spoiler]')
        assert result == (
            '<span class="spoiler"><blockquote><small>alice said:</small> x</blockquote></span>'
        )

    def test_anonymous_quote(self):
        assert v1("[quote]anon[/quote]") == "<blockquote>anon</blockquote>"

    def test_sibling_anonymous_quotes(self):
        result = v1("[quote]a[/quote] [quote]b[/quote]")
        assert result == "<blockquote>a</blockquote> <blockquote>b</blockquote>"

    def test_nested_named_quotes(self):
        result = v1('[quote="alice"]outer [quote="bob"]inner[/quote] tail[/quote]')
        assert result == (
            "<blockquote><small>alice said:</small> outer "
            "<blockquote><small>bob said:</small> inner</blockquote> tail</blockquote>"
        )

    def test_three_deep_named_quotes(self):
        result = v1('[quote="a"][quote="b"][quote="c"]x[/quote]y[/quote]z[/quote]')
        assert result == (
            "<blockquote><small>a said:</small> "
            "<blockquote><small>b said:</small> "
            "<blockquote><small>c said:</small> x</blockquote>y</blockquote>z</blockquote>"
        )

    def test_anonymous_quote_inside_named_quote(self):
        result = v1('[quote="alice"]outer [quote]anon[/quote] tail[/quote]')
        assert result == (
            "<blockquote><small>alice said:</small> outer "
            "<blockquote>anon</blockquote> tail</blockquote>"
        )

    def test_named_quote_inside_anonymous_quote(self):
        result = v1('[quote]outer [quote="bob"]inner[/quote] tail[/quote]')
        assert result == (
            "<blockquote>outer "
            "<blockquote><small>bob said:</small> inner</blockquote> tail</blockquote>"
        )

    def test_unclosed_named_quote_is_left_as_text(self):
        result = v1('[quote="alice"]unclosed')
        assert result == "[quote=&quot;alice&quot;]unclosed"

    def test_unclosed_anonymous_quote_tag_is_removed(self):
        assert v1("[quote]unclosed") == "unclosed"

    def test_orphan_closing_tag_is_removed(self):
        assert v1("orphan close[/quote]") == "orphan close"

    def test_extra_closing_tag_after_named_quote_is_removed(self):
        result = v1('[quote="alice"]a[/quote][/quote]')
        assert result == "<blockquote><small>alice said:</small> a</blockquote>"

    def test_nested_anonymous_quotes(self):
        result = v1("[quote]outer [quote]inner[/quote] tail[/quote]")
        assert result == "<blockquote>outer <blockquote>inner</blockquote> tail</blockquote>"

    def test_named_quote_missing_a_closer_pairs_leftmost_opener_first(self):
        """Many legacy comments lost an inner quote's closer: the first closer
        follows the outer author's reply, so it must stay with the outer opener."""
        result = v1('[quote="alice"][quote="bob"]question\nanswer[/quote]\nreply')
        assert result == (
            "<blockquote><small>alice said:</small> [quote=&quot;bob&quot;]question<br>\n"
            "answer</blockquote><br>\nreply"
        )


# Linear parsing takes milliseconds on these inputs; a scan that restarts from
# every opener takes seconds.
RENDER_TIME_LIMIT_SECONDS = 0.5


@contextmanager
def render_time_limit() -> Iterator[None]:
    """Fail if the block uses more than RENDER_TIME_LIMIT_SECONDS of CPU time.

    The block is charged for the parser's own work and nothing else:

    - CPU time (time.process_time), not elapsed time, so waiting for a core on
      a loaded CI runner does not count.
    - Garbage collection is paused. These inputs allocate enough objects to
      trigger a full collection, and late in the suite one full collection of a
      test worker's heap takes longer than the limit by itself.
    """
    gc_was_enabled = gc.isenabled()
    gc.disable()
    start = time.process_time()
    try:
        yield
        cpu_seconds = time.process_time() - start
    finally:
        if gc_was_enabled:
            gc.enable()
    assert cpu_seconds < RENDER_TIME_LIMIT_SECONDS


class TestRenderTimeLimit:
    """The timing helper itself: what it measures and what it leaves behind."""

    def test_pauses_garbage_collection_inside_the_block(self):
        with render_time_limit():
            assert not gc.isenabled()
        assert gc.isenabled()

    def test_restores_garbage_collection_when_the_block_raises(self):
        with pytest.raises(RuntimeError), render_time_limit():
            raise RuntimeError
        assert gc.isenabled()

    def test_leaves_garbage_collection_off_if_it_was_already_off(self):
        gc.disable()
        try:
            with render_time_limit():
                pass
            assert not gc.isenabled()
        finally:
            gc.enable()

    def test_fails_a_block_that_uses_more_cpu_time_than_the_limit(self):
        with pytest.raises(AssertionError), render_time_limit():
            start = time.process_time()
            while time.process_time() - start <= RENDER_TIME_LIMIT_SECONDS:
                pass


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
        with render_time_limit():
            v1(text)

    def test_many_anonymous_quote_openers(self):
        with render_time_limit():
            v1("[quote]" * 10000)


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
        with render_time_limit():
            v1(text)


class TestParseMarkdownFaces:
    """Tests for emoticon faces that start with > are not quotes"""

    @pytest.mark.parametrize(
        ("text", "html"),
        [
            (">_< oh no", "&gt;_&lt; oh no"),
            (">.<", "&gt;.&lt;"),
            ("><", "&gt;&lt;"),
            (">'<", "&gt;&#x27;&lt;"),
            ("  >w< hi", "  &gt;w&lt; hi"),
            ("a\n>_<\nb", "a<br>\n&gt;_&lt;<br>\nb"),
        ],
    )
    def test_face_is_not_a_quote(self, text, html):
        assert v1(text) == html

    @pytest.mark.parametrize(
        ("text", "html"),
        [
            (">____< x", "<blockquote>____&lt; x</blockquote>"),
            ("> quote", "<blockquote>quote</blockquote>"),
            (">[spoiler]x[/spoiler]", '<blockquote><span class="spoiler">x</span></blockquote>'),
        ],
    )
    def test_quote_lines_stay_quotes(self, text, html):
        assert v1(text) == html


def test_dispatch():
    assert parse_markdown("- a", 1) == "- a"
    assert parse_markdown("- a", 2) == "<ul><li>a</li></ul>"
    assert parse_markdown("## a", 2) == "## a"
    assert parse_markdown("## a", 2, headings=True) == "<h2>a</h2>"
    assert parse_markdown("## a", 1, headings=True) == "## a"
    assert parse_markdown(None, 2) == ""
    with pytest.raises(ValueError):
        parse_markdown("a", 3)


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


class TestAutolinkAroundExplicitLinks:
    """Bare-URL linking must leave the anchors of explicit links alone."""

    @pytest.mark.parametrize("version", [1, 2])
    def test_explicit_link_with_text_over_two_lines_is_not_relinked(self, version):
        assert parse_markdown("x [a\nb](https://b.example/)", version=version) == (
            "x " + _anchor("https://b.example/", "a<br>\nb")
        )

    @pytest.mark.parametrize("version", [1, 2])
    def test_url_text_over_two_lines_in_explicit_link_is_not_nested(self, version):
        result = parse_markdown("x [see\nhttps://a.example/](https://b.example/)", version=version)
        assert result.count("<a ") == 1
        assert 'href="https://b.example/"' in result

    @pytest.mark.parametrize("version", [1, 2])
    def test_quote_in_multiline_explicit_link_url_cannot_inject_attributes(self, version):
        result = parse_markdown(
            'x [a\nb](https://b.example/"onmouseover=alert(1))', version=version
        )
        checker = _StructureCheck()
        checker.feed(result)
        checker.close()
        assert result.count("<a ") == 1
        assert checker.problems == [], result


_ALLOWED_TAGS = {
    "a", "strong", "em", "blockquote", "small", "span", "br",
    "ul", "ol", "li", "code", "pre", "h2", "h3",
}  # fmt: skip
_URL_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")


def _href_problem(href: str | None) -> str | None:
    """Why a browser would not treat this href as http(s) or scheme-less, if so."""
    if href is None:
        return "href without value"
    # As a browser reads it: strip C0 controls and space at the ends, drop tab and newlines
    cleaned = re.sub(r"[\t\n\r]", "", href.strip("".join(map(chr, range(33)))))
    scheme = _URL_SCHEME.match(cleaned)
    if scheme and scheme[1].lower() not in ("http", "https"):
        return f"scheme {scheme[1]}"
    return None


class _StructureCheck(HTMLParser):
    """Collects every way rendered HTML strays from the tags and attributes we emit."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []
        self._open: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _ALLOWED_TAGS:
            self.problems.append(f"tag <{tag}>")
            return
        if tag == "a":
            self._check_anchor(attrs)
        elif tag == "span":
            if attrs != [("class", "spoiler")]:
                self.problems.append(f"span attrs {attrs}")
        elif tag == "ol":
            if attrs and not (
                len(attrs) == 1 and attrs[0][0] == "start" and (attrs[0][1] or "").isdigit()
            ):
                self.problems.append(f"ol attrs {attrs}")
        elif attrs:
            self.problems.append(f"{tag} attrs {attrs}")
        if tag != "br":
            self._open.append(tag)

    def _check_anchor(self, attrs: list[tuple[str, str | None]]) -> None:
        if "a" in self._open:
            self.problems.append("nested <a>")
        if [name for name, _ in attrs] != ["href", "rel", "target"]:
            self.problems.append(f"a attrs {attrs}")
            return
        values = dict(attrs)
        if values["rel"] != "nofollow noopener" or values["target"] != "_blank":
            self.problems.append(f"a attrs {attrs}")
        if problem := _href_problem(values["href"]):
            self.problems.append(f"href {problem}: {values['href']!r}")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.problems.append(f"self-closing <{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        if tag not in _ALLOWED_TAGS:
            self.problems.append(f"end tag </{tag}>")
        elif tag in self._open:
            self._open.reverse()
            self._open.remove(tag)
            self._open.reverse()

    def handle_comment(self, data: str) -> None:
        self.problems.append("comment")

    def handle_decl(self, decl: str) -> None:
        self.problems.append("declaration")

    def handle_pi(self, data: str) -> None:
        self.problems.append("processing instruction")

    def unknown_decl(self, data: str) -> None:
        self.problems.append("unknown declaration")


_FRAGMENTS = [
    "[", "]", "(", ")", "](", "*", "**", "`", "```", "\n", " ", ">", "<", '"', "'", "&",
    "&quot;", "&amp;", "&#42;", "&lt;", "&#x3c;", "&amp;quot;",
    "http://a.example/", "https://b.example/x", "www.c.example", "Www.d.example/",
    "javascript:alert(1)", " onmouseover=alert(1) ", "onclick=x", "//evil.example",
    "[spoiler]", "[/spoiler]", "[quote]", "[/quote]", '[quote="n"]', '[quote="', '"]',
    "- ", "1. ", "7. ", "# ", "a", "b c", "%EE%80%80", "%E2%80%AE", "\ue000", "0", "\ue001",
    "=", "</a>", '<a href="', "\t", "data:text/html,x", "style=", "\r", "\x00",
]  # fmt: skip

# (opening, middle, closing) around nested content; closing is added after a second body
_WRAPS = [
    ("[", "](", ")"), ("**", "**", ""), ("*", "*", ""), ("[spoiler]", "[/spoiler]", ""),
    ("[quote]", "[/quote]", ""), ('[quote="', '"]', "[/quote]"), ("`", "`", ""),
    ("```\n", "\n```\n", ""), ("> ", "\n", ""), ("- ", "\n", ""), ("(", ")", ""),
]  # fmt: skip

_RENDERS = [(1, False), (2, False), (2, True)]


def _build(rng: random.Random, depth: int) -> str:
    """Random text made of well-formed constructs wrapped around fragments."""
    parts: list[str] = []
    for _ in range(rng.randint(1, 4)):
        if depth and rng.random() < 0.5:
            opening, middle, closing = rng.choice(_WRAPS)
            parts += [opening, _build(rng, depth - 1), middle]
            if closing:
                parts += [_build(rng, depth - 1), closing]
        else:
            parts.append(rng.choice(_FRAGMENTS))
    return "".join(parts)


def _structure_corpus(random_count: int) -> Iterator[str]:
    for size in (1, 2):
        for combo in itertools.product(_FRAGMENTS, repeat=size):
            yield "".join(combo)
    rng = random.Random(0)
    for _ in range(random_count):
        yield "".join(rng.choices(_FRAGMENTS, k=rng.randint(4, 14)))
        yield _build(rng, 3)


class TestRenderedHtmlStructure:
    """Whatever the input, the output holds only the tags and attributes we emit."""

    def test_rendered_html_has_only_allowed_structure(self):
        failures = []
        for text in _structure_corpus(10_000):
            for version, headings in _RENDERS:
                checker = _StructureCheck()
                checker.feed(parse_markdown(text, version, headings=headings))
                checker.close()
                if checker.problems:
                    failures.append(
                        f"input={text!r} version={version} headings={headings}: {checker.problems}"
                    )
        assert not failures, f"{len(failures)} bad renders, first few:\n" + "\n".join(failures[:5])
