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
    "- >",
    "- >_<",
    "- '_'",
    "- &amp;",
    "- -\n* a",
    "* *\n* x",
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
    "a [spoiler]>_> b[/spoiler] c",
    "text [quote]> x[/quote]",
    '[spoiler]Snape kills Dumbledore [quote="lol[/spoiler] what"] after',
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


@pytest.mark.parametrize(
    ("text", "html"),
    [
        # A container opened mid-line keeps its first line inline
        ("a [spoiler]>_> b[/spoiler] c", 'a <span class="spoiler">&gt;_&gt; b</span> c'),
        ("text [quote]> x[/quote]", "text <blockquote>&gt; x</blockquote>"),
        (
            "a [quote][spoiler]> x[/spoiler][/quote]",
            'a <blockquote><span class="spoiler">&gt; x</span></blockquote>',
        ),
        (
            "[spoiler]a[/spoiler][quote]> x[/quote]",
            '<span class="spoiler">a</span><blockquote>&gt; x</blockquote>',
        ),
        # A container opened at the start of a line starts one
        ("[quote]> x[/quote]", "<blockquote><blockquote>x</blockquote></blockquote>"),
        ("  [quote]> x[/quote]", "  <blockquote><blockquote>x</blockquote></blockquote>"),
        ("a\n[quote]> x[/quote]", "a\n<blockquote><blockquote>x</blockquote></blockquote>"),
        (
            "[quote][spoiler]> x[/spoiler][/quote]",
            '<blockquote><span class="spoiler"><blockquote>x</blockquote></span></blockquote>',
        ),
        (
            "a [quote]b\n[spoiler]> x[/spoiler][/quote]",
            'a <blockquote>b<br>\n<span class="spoiler"><blockquote>x</blockquote></span></blockquote>',
        ),
        # Later content lines are ordinary line starts
        (
            "a [spoiler]x\n> y[/spoiler]",
            'a <span class="spoiler">x\n<blockquote>y</blockquote></span>',
        ),
    ],
)
def test_block_rules_on_a_containers_first_line(text, html):
    assert render_v2(text, headings=False) == html


def test_mis_nested_spoiler_keeps_its_text_hidden():
    assert render_v2("[quote][spoiler]x[/quote][/spoiler]", headings=False) == (
        '<span class="spoiler">x</span>'
    )


def test_unclosed_named_quote_is_stripped():
    assert render_v2('[quote="alice"]unclosed', headings=False) == "unclosed"


@pytest.mark.parametrize(
    ("text", "html"),
    [
        (
            '[spoiler]Snape kills Dumbledore [quote="lol[/spoiler] what"] after',
            '<span class="spoiler">Snape kills Dumbledore [quote=&quot;lol</span>'
            " what&quot;] after",
        ),
        (
            '[spoiler]secret [quote="x[/spoiler] y"]z[/quote]',
            '<span class="spoiler">secret [quote=&quot;x</span> y&quot;]z',
        ),
        (
            '[quote="[spoiler]Bob[/spoiler]"]text[/quote]',
            '[quote=&quot;<span class="spoiler">Bob</span>&quot;]text',
        ),
    ],
    ids=["unclosed-name-holds-closer", "closed-name-holds-closer", "name-holds-pair"],
)
def test_a_quote_name_cannot_hold_a_container_tag(text, html):
    # A spoiler's text never escapes it through a tag inside a quote's name
    assert render_v2(text, headings=False) == html


@pytest.mark.parametrize(
    ("text", "html"),
    [
        (
            '[quote="[XYZ]Bob"]hi[/quote]',
            "<blockquote><small>[XYZ]Bob said:</small> hi</blockquote>",
        ),
        ('[quote="*a*"]x[/quote]', "<blockquote><small>*a* said:</small> x</blockquote>"),
    ],
)
def test_quote_names_are_plain_text(text, html):
    assert render_v2(text, headings=False) == html


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
        '[quote="ab]' * 8000,
        '[quote="a' * 2000,
        '[quote="[x]' * 2000,
    ],
    ids=[
        "quote-700",
        "spoiler-700",
        "named-700",
        "named-openers",
        "anon-openers",
        "blocked-closers",
        "names-ending-in-bracket",
        "unterminated-names",
        "names-starting-with-brackets",
    ],
)
def test_container_scan_is_linear(text):
    start = time.perf_counter()
    render_v2(text, headings=False)
    assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS


@pytest.mark.parametrize(
    ("text", "html"),
    [
        ("- a", "<ul><li>a</li></ul>"),
        ("- a\n- b", "<ul><li>a</li><li>b</li></ul>"),
        ("* a\n* b", "<ul><li>a</li><li>b</li></ul>"),
        ("- a\n* b", "<ul><li>a</li><li>b</li></ul>"),
        ("* a*\n* b*", "<ul><li>a*</li><li>b*</li></ul>"),
        ("1. a\n2. b", "<ol><li>a</li><li>b</li></ol>"),
        ("3. a\n7. b", '<ol start="3"><li>a</li><li>b</li></ol>'),
        ("0001. a", "<ol><li>a</li></ol>"),
        ("2012. Coming soon.", '<ol start="2012"><li>Coming soon.</li></ol>'),
        ("- a\n1. b", "<ul><li>a</li></ul>\n<ol><li>b</li></ol>"),
        ("  - a\n    - b", "<ul><li>a</li><li>b</li></ul>"),
        ("- 日本", "<ul><li>日本</li></ul>"),
        ("- **b**", "<ul><li><strong>b</strong></li></ul>"),
        ("- > a", "<ul><li>&gt; a</li></ul>"),
        # The 3 is a typed digit, so this is a list item
        ("- <3", "<ul><li>&lt;3</li></ul>"),
        ("* a", "* a"),
        ("- a\n\n- b", "<ul><li>a</li></ul>\n<ul><li>b</li></ul>"),
        ("- 'a'", "<ul><li>&#x27;a&#x27;</li></ul>"),
        ("- [spoiler]x[/spoiler]", '<ul><li><span class="spoiler">x</span></li></ul>'),
        ("x\n- a\n- b\ny", "x\n<ul><li>a</li><li>b</li></ul>\ny"),
        ("x\n\n- a\n\ny", "x\n<ul><li>a</li></ul>\ny"),
        ("- a\n\n\ny", "<ul><li>a</li></ul>\n<br>\ny"),
        ("[quote]- a\n- b[/quote]", "<blockquote><ul><li>a</li><li>b</li></ul></blockquote>"),
        ("[quote]\n- a\n[/quote]", "<blockquote><ul><li>a</li></ul></blockquote>"),
        # A list marker not at the start of a line of the source is text
        ("a [spoiler]- b[/spoiler] c", 'a <span class="spoiler">- b</span> c'),
        ("[spoiler]- b[/spoiler]", '<span class="spoiler"><ul><li>b</li></ul></span>'),
        ("a [quote]- b\n- c[/quote]", "a <blockquote>- b\n<ul><li>c</li></ul></blockquote>"),
    ],
)
def test_lists(text, html):
    assert render_v2(text, headings=False) == html


def test_huge_list_number_is_text():
    text = "9" * 5000 + ". x"
    assert render_v2(text, headings=False) == render_v1(text)


@pytest.mark.parametrize("text", ["- a\n" * 4000, "* a\n" * 4000, "1. a\n" * 4000, "- -\n" * 4000])
def test_many_list_lines_are_linear(text):
    start = time.perf_counter()
    render_v2(text, headings=False)
    assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS


def test_star_faces_do_not_pair_into_a_list():
    assert "<ul>" not in render_v2("* >_<\n* >.<", headings=False)
