"""
Tests for version 2 of the markdown renderer
"""

import time

import pytest

from app.utils.markdown_v1 import render_v1
from app.utils.markdown_v2 import render_v2
from tests.unit.test_markdown import RENDER_TIME_LIMIT_SECONDS

# Text that uses only version 1 syntax and none of the design's expected
# differences renders the same in both versions.
PARITY_CORPUS = [
    "This is **bold** text",
    "*italic*",
    "**bold** and *italic*",
    "[example](https://example.com)",
    "[x](javascript:alert(1))",
    "[search](https://example.com/search?q=test&page=2)",
    "see https://example.com/a_(b). ok",
    "**https://example.com**",
    "> a",
    "> a\n> b",
    "> a\nb",
    "x\n> a",
    "x\n\n> a",
    "Line 1\nLine 2",
    "a\n\nb",
    "a\n",
    "\na",
    "\n\n",
    "<script>alert('xss')</script>",
    "&quot;hi&quot; &amp; bye",
    "*a\nb*",
    "[link\ntext](https://example.com)",
    ">_< oh no",
    "a\n>_<\nb",
    "* nods head* yes",
    "- -",
    "- _ -",
    "- ",
    "I don`t think that`s snow",
    "`_`",
    "``x``",
    "```\na",
    "1234567890. x",
    "> - a",
    "#1 fan!",
    "# x",
    "#### x",
    '[quote="alice"]hi[/quote]',
    "[quote]a\nb[/quote]",
    "[quote]\na\n[/quote]",
    "x\n[quote]a[/quote]\ny",
    "[quote]a[/quote]\n[quote]b[/quote]",
    '[quote="a"][quote="b"]x[/quote] y[/quote]',
    "a [spoiler]b[/spoiler] c",
    "[spoiler]a\nb[/spoiler]",
    "[spoiler]a\n[/spoiler]",
    "[spoiler]a [spoiler]b[/spoiler] c[/spoiler]",
    "[spoiler]a [quote] b[/spoiler]",
    '[spoiler][quote="a"]x[/quote][/spoiler]',
    "[spoiler]unclosed",
    "[/quote] x",
    "[quote]unclosed",
    "> a\n[quote]b[/quote]",
    "[quote]\n> a\n[/quote]",
    '[quote="a"]x\n[/quote]',
    "[quote]x\n[quote]y[/quote]\n[/quote]",
    "[quote]\n[/quote]",
    "x\n[spoiler]a[/spoiler]",
    "x\n [quote]a[/quote]",
    "[spoiler]x\n[quote]a[/quote][/spoiler]",
]


@pytest.mark.parametrize("text", PARITY_CORPUS)
def test_matches_version_1(text):
    assert render_v2(text, headings=False) == render_v1(text)


def test_crlf_is_normalised():
    assert render_v2("a\r\nb\rc", headings=False) == "a<br>\nb<br>\nc"


def test_placeholder_code_points_are_neutralised():
    assert render_v2("x\ue0000\ue001y", headings=False) == "x\ufffd0\ufffdy"


def test_placeholder_entities_are_neutralised():
    # Unescaping a stored entity can produce a placeholder code point too
    assert render_v2("x&#xe000;0&#xe001;y", headings=False) == "x\ufffd0\ufffdy"


def test_empty():
    assert render_v2("", headings=False) == "" and render_v2(None, headings=False) == ""


@pytest.mark.parametrize(
    "text",
    ["[" * 40000, "[" * 40000 + "](", "[x](" * 20000],
    ids=[
        "40000-open-brackets",
        "40000-open-brackets-one-close-and-paren",
        "20000-links-without-closing-paren",
    ],
)
def test_bracket_runs_are_linear(text):
    start = time.perf_counter()
    render_v2(text, headings=False)
    assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS


@pytest.mark.parametrize(
    ("text", "html"),
    [
        # Bold markers on both sides of a spoiler don't pair across it
        ("**a [spoiler]b[/spoiler] c**", '**a <span class="spoiler">b</span> c**'),
        # A marker outside a spoiler doesn't pair with one inside it
        ("*a [spoiler]b*[/spoiler]", '*a <span class="spoiler">b*</span>'),
        # The rules still apply on either side
        ("*a* [spoiler]b[/spoiler] *c*", '<em>a</em> <span class="spoiler">b</span> <em>c</em>'),
    ],
)
def test_inline_rules_do_not_cross_a_container(text, html):
    assert render_v2(text, headings=False) == html


def test_mis_nested_spoiler_keeps_its_text_hidden():
    assert render_v2("[quote][spoiler]x[/quote][/spoiler]", headings=False) == (
        '<span class="spoiler">x</span>'
    )


def test_unclosed_named_quote_is_stripped():
    assert render_v2('[quote="alice"]unclosed', headings=False) == "unclosed"


def test_missing_inner_closer_pairs_innermost():
    text = '[quote="alice"][quote="bob"]question\nanswer[/quote]\nreply'
    assert render_v2(text, headings=False) == (
        "<blockquote><small>bob said:</small> question<br>\nanswer</blockquote><br>\nreply"
    )


@pytest.mark.parametrize(
    ("text", "html"),
    [
        ('[spoiler]a [quote]b [quote="x"]c[/spoiler] d', '<span class="spoiler">a b c</span> d'),
        ('a [quote]b [spoiler]c [quote="x"]d', "a b c d"),
    ],
    ids=["inside-a-pair", "at-the-end"],
)
def test_unmatched_openers_are_stripped_keeping_content_in_order(text, html):
    assert render_v2(text, headings=False) == html


def test_quotes_nest_past_ten():
    text = "[quote]" * 12 + "x" + "[/quote]" * 12
    assert render_v2(text, headings=False) == "<blockquote>" * 12 + "x" + "</blockquote>" * 12


def test_url_before_container_links_only_the_url():
    html = render_v2("see http://a.com/[spoiler]x[/spoiler]", headings=False)
    assert html == (
        'see <a href="http://a.com/" rel="nofollow noopener" target="_blank">http://a.com/</a>'
        '<span class="spoiler">x</span>'
    )


@pytest.mark.parametrize(
    "text",
    [
        "[quote]" * 700 + "[/quote]" * 700,
        "[spoiler]" * 700 + "[/spoiler]" * 700,
        '[quote="x"]' * 700 + "[/quote]" * 700,
        '[quote="x"]' * 5900,
        "[quote]" * 10000,
        "[quote]" + "[spoiler]" * 1100 + "[/quote]" * 1100,
    ],
    ids=[
        "quote-700",
        "spoiler-700",
        "named-700",
        "named-openers",
        "anon-openers",
        "blocked-closers",
    ],
)
def test_container_scan_is_linear(text):
    start = time.perf_counter()
    render_v2(text, headings=False)
    assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS
